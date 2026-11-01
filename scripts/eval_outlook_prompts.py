"""Offline A/B of the instrument-outlook prompt against hand-labelled views. Not run in CI.

Rebuilds, per labelled video, the exact items ``classify_video`` sends (every tracked-instrument
mention, its summary and the padded transcript excerpt) from READ-ONLY production GETs, calls
MiniMax through ``llm_service`` with the baseline prompt (read from a git ref) and the current
prompt, and scores only the labelled (video, instrument) pairs.

Labels (JSON list): {"instrument", "video_id", "current", "quote", "label"} where label is
``keep`` (a real forward view in direction ``current``), ``none_current_state`` or ``none_other``.

Usage (no secret is printed):
    set -a; source ../doviz/v1/finance-api/.env; set +a   # TRACKER_API_KEY
    export TRACKER_API_URL=<tracker API base URL>
    uv run --frozen python scripts/eval_outlook_prompts.py \\
        --labels labels.json --out eval.md --cache-dir /tmp/eval-cache \\
        --minimax-env ../tracker/.env --baseline-ref 0c6e6f1

Approximations: the API exposes no segment timings, so captions are re-fetched from YouTube.
When their joined text equals the stored transcript the excerpt is exact; otherwise the window's
character span in the captions is scaled onto the stored text. Without captions (YouTube blocks
bulk fetches) the window is mapped by time share and slid onto the labelled production quote.
Multi-guest speaker context is the channel name (no participants list).
"""

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SITE_TO_SLUG = {
    "dolar-tl": "doviz-kur",
    "bist": "bist-turk-piyasalari",
    "altin": "altin",
    "bitcoin": "kripto-paralar",
    "us-markets": "amerikan-piyasalari",
    "gumus": "gumus",
    "petrol-enerji": "petrol-enerji",
}


def load_env(path: Path, prefixes: tuple[str, ...]) -> None:
    """Export only the given prefixes from the tracker .env (never DATABASE_URL etc.)."""
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key.startswith(prefixes) and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def template_at_ref(ref: str) -> str:
    """OUTLOOK_PROMPT_TEMPLATE literal from src/llm/prompts.py at a git ref (no exec)."""
    source = subprocess.run(
        ["git", "show", f"{ref}:src/llm/prompts.py"], check=True, capture_output=True, text=True
    ).stdout
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "OUTLOOK_PROMPT_TEMPLATE" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise SystemExit(f"OUTLOOK_PROMPT_TEMPLATE not found at {ref}")


class Cache:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def get(self, name: str):
        path = self.root / name
        return json.loads(path.read_text()) if path.exists() else None

    def put(self, name: str, value) -> None:
        (self.root / name).write_text(json.dumps(value, ensure_ascii=False))


def fetch_video(client: httpx.Client, cache: Cache, video_id: int) -> dict:
    cached = cache.get(f"video_{video_id}.json")
    if cached:
        return cached
    video = client.get(f"/videos/{video_id}").raise_for_status().json()
    data = {
        "video": video,
        "mentions": client.get(f"/videos/{video_id}/classification")
        .raise_for_status()
        .json()["mentions"],
        "raw_text": client.get(f"/videos/{video_id}/transcript").raise_for_status().json()[
            "raw_text"
        ],
        "channel": (
            client.get(f"/channels/{video['channel_id']}").raise_for_status().json()
            if video.get("channel_id")
            else None
        ),
    }
    cache.put(f"video_{video_id}.json", data)
    return data


def fetch_segments(cache: Cache, youtube_id: str, use_proxy: bool) -> list[dict] | None:
    """Caption segments (cached). With use_proxy, via the tracker's configured YouTube proxy."""
    cached = cache.get(f"segments_{youtube_id}.json")
    if cached:
        return cached
    try:
        if use_proxy:
            from src.videos import service as videos_service

            fetched = videos_service.fetch_transcript_from_youtube(youtube_id, ["tr"])
            segments = fetched.get("segments") or []
        else:
            from youtube_transcript_api import YouTubeTranscriptApi

            segments = YouTubeTranscriptApi().fetch(youtube_id, languages=["tr"]).to_raw_data()
    except Exception as exc:  # noqa: BLE001 — fall back to the proportional excerpt
        print(f"segments unavailable for {youtube_id}: {type(exc).__name__}", file=sys.stderr)
        return None
    cache.put(f"segments_{youtube_id}.json", segments)
    return segments or None


def _window(start, end) -> tuple[float, float] | None:
    from src.classification import outlook

    s, e = outlook._seconds(start), outlook._seconds(end)
    if s is None:
        return None
    lo = max(0.0, s - outlook._WINDOW_PAD_SECONDS)
    return lo, (e if e is not None and e > s else s + 240) + outlook._WINDOW_PAD_SECONDS


def aligned_excerpt(raw_text: str, segments: list[dict] | None, duration: float, start, end):
    """Excerpt window mapped onto the stored raw text when exact segment timings are missing.

    With re-fetched captions (a different ASR pass of the same audio) the window's character
    span in the caption text is scaled onto the stored text; without captions, by time share.
    """
    from src.classification import outlook

    window = _window(start, end)
    if window is None:
        return ""
    lo, hi = window
    n = len(raw_text)
    if segments:
        texts = [str(seg["text"]).strip() for seg in segments]
        offsets, pos = [], 0
        for t in texts:
            offsets.append(pos)
            pos += len(t) + 1
        inside = [i for i, seg in enumerate(segments) if lo <= float(seg["start"]) <= hi]
        if not inside:
            return ""
        a, b = offsets[inside[0]], offsets[inside[-1]] + len(texts[inside[-1]])
        text = raw_text[int(n * a / pos) : int(n * b / pos)]
    elif duration:
        text = raw_text[int(n * lo / duration) : int(n * min(hi, duration) / duration)]
    else:
        text = ""
    return text[: outlook._EXCERPT_MAX_CHARS]


def locate_quote(raw_text: str, quote: str) -> int | None:
    """Char offset in raw_text of the best token-overlap match of quote (>= 60%), else None."""
    words = [(m.start(), m.group().lower()) for m in re.finditer(r"\w+", raw_text)]
    q = [w.lower() for w in re.findall(r"\w+", quote)]
    if not q or len(words) < len(q):
        return None
    qset = set(q)
    best, best_i = 0, None
    for i in range(len(words) - len(q) + 1):
        hits = sum(1 for _, w in words[i : i + len(q)] if w in qset)
        if hits > best:
            best, best_i = hits, i
    return words[best_i][0] if best_i is not None and best >= 0.6 * len(q) else None


def anchored_excerpt(raw_text: str, excerpt: str, quotes: list[str]) -> str:
    """Slide an approximate window so it contains the stored (verbatim) production quote."""
    from src.classification import outlook

    start = raw_text.find(excerpt) if excerpt else -1
    size = len(excerpt) or outlook._EXCERPT_MAX_CHARS // 2
    for quote in quotes:
        pos = locate_quote(raw_text, quote)
        if pos is None or (start >= 0 and start <= pos <= start + size - len(quote)):
            continue
        lo = max(0, min(pos - size // 2, len(raw_text) - size))
        return raw_text[lo : lo + size][: outlook._EXCERPT_MAX_CHARS]
    return excerpt


def build_video_items(
    data: dict, segments: list[dict] | None, slugs: dict[int, str],
    anchors: dict[str, list[str]] | None = None,
):
    """Returns (items, mode); mode is exact | aligned | proportional.

    Without captions, ``anchors`` (topic slug -> production quotes) move the approximate window
    of that topic's mention onto the quote: the real excerpt contained it (quotes are verbatim).
    """
    from src.classification import outlook

    raw = data["raw_text"].strip()
    joined = {" ".join(str(s["text"]) for s in segments or []).strip(),
              " ".join(str(s["text"]).strip() for s in segments or []).strip()}
    mode = (
        "exact" if segments and raw in joined
        else "aligned" if segments else "proportional"
    )
    items = []
    for m in data["mentions"]:
        slug = slugs.get(m["topic_id"])
        if slug not in outlook.INSTRUMENTS:
            continue
        if mode == "exact":
            excerpt = outlook.transcript_excerpt(segments, m["start_time"], m["end_time"])
        else:
            excerpt = aligned_excerpt(
                data["raw_text"], segments, float(data["video"].get("duration") or 0),
                m["start_time"], m["end_time"],
            )
            if mode == "proportional" and anchors and anchors.get(slug):
                excerpt = anchored_excerpt(data["raw_text"], excerpt, anchors[slug])
        items.append((SimpleNamespace(id=m["id"], summary=m["summary"], slug=slug),
                      outlook.INSTRUMENTS[slug], excerpt))
    return items, mode


def call_prompt(cache: Cache, template: str, data: dict, items_block: str) -> dict:
    from src.llm import service as llm_service

    speaker = (data["channel"] or {}).get("name") or data["video"].get("title") or ""
    prompt = llm_service._replace_placeholders(
        template,
        {
            "video_title": data["video"].get("title") or "",
            "speaker_name": speaker,
            "published_at": data["video"].get("published_at") or "",
            "items": items_block,
        },
    )
    key = "llm_" + hashlib.sha256(prompt.encode()).hexdigest()[:24] + ".json"
    cached = cache.get(key)
    if cached is not None:
        return cached
    payload = llm_service._call_llm_json(prompt)
    cache.put(key, payload)
    return payload


def parse_v3(payload: dict, ids: set[int]) -> dict[int, dict]:
    """Baseline parser (as on main before the time/own_view guard)."""
    out = {}
    for item in payload.get("items") or []:
        try:
            mid = int(item.get("mention_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        val = str(item.get("outlook", "")).strip().lower()
        if mid in ids and val in {"up", "down", "flat", "none"}:
            out[mid] = {"outlook": val,
                        "outlook_explicit": item.get("explicit") is True and val != "none",
                        "outlook_quote": item.get("quote") or ""}
    return out


def _tokens(text: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split()
            if len(w) > 2}


def pick_mention(label: dict, items) -> int | None:
    slug = SITE_TO_SLUG[label["instrument"]]
    candidates = [(m, ex) for m, _, ex in items if m.slug == slug]
    if not candidates:
        return None
    q = _tokens(label.get("quote") or "")
    best = max(candidates, key=lambda c: len(q & _tokens(c[1] + " " + c[0].summary)))
    return best[0].id


def requoted(row: dict, variant: str) -> bool:
    """Still explicit, but on a different sentence than the labelled (wrong) quote."""
    q, new = _tokens(row.get("quote") or ""), _tokens(row.get(variant + "_quote") or "")
    return bool(new) and len(q & new) < 0.5 * max(1, len(q))


def score(rows: list[dict], variant: str) -> dict:
    """keep: explicit, same direction. none_*: not explicit (``*_requoted``: explicit on another
    sentence, counted separately, not as removed)."""
    by = Counter()
    hit = Counter()
    for r in rows:
        res = r.get(variant)
        if res is None:
            continue
        by[r["label"]] += 1
        explicit = res["outlook_explicit"]
        if r["label"] == "keep":
            hit["keep"] += explicit and res["outlook"] == r["current"]
        else:
            hit[r["label"]] += not explicit
            hit[r["label"] + "_requoted"] += explicit and requoted(r, variant)
    out = {k: (hit[k], by[k]) for k in ("keep", "none_current_state", "none_other")}
    for k in ("none_current_state", "none_other"):
        out[k + "_requoted"] = (hit[k + "_requoted"], by[k])
    return out


def pct(pair) -> str:
    h, n = pair
    return f"{h}/{n} ({100 * h / n:.0f}%)" if n else "n/a"


def fmt(res: dict | None) -> str:
    if res is None:
        return "—"
    return f"{res['outlook']}{'*' if res['outlook_explicit'] else ''}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("--minimax-env", type=Path, required=True)
    ap.add_argument("--baseline-ref", default="0c6e6f1")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--tag", default="v4", help="label for the current prompt in the report")
    ap.add_argument("--youtube-proxy", action="store_true",
                    help="fetch captions through the tracker's YouTube proxy settings")
    args = ap.parse_args()

    prefixes = ("MINIMAX_",) + (("YOUTUBE_", "WEBSHARE_") if args.youtube_proxy else ())
    load_env(args.minimax_env, prefixes)
    from src.classification import outlook
    from src.llm.prompts import OUTLOOK_PROMPT_TEMPLATE, OUTLOOK_PROMPT_VERSION

    api_key = os.environ.get("TRACKER_API_KEY")
    base_url = os.environ.get("TRACKER_API_URL")
    if not api_key or not base_url:
        raise SystemExit("TRACKER_API_KEY and TRACKER_API_URL must be set")
    labels = [r for r in json.loads(args.labels.read_text()) if r.get("quote")]
    cache = Cache(args.cache_dir)
    baseline = template_at_ref(args.baseline_ref)

    with httpx.Client(base_url=base_url, headers={"X-API-Key": api_key}, timeout=60) as c:
        slugs = {t["id"]: t["slug"] for t in c.get("/topics/").raise_for_status().json()}
        video_ids = sorted({r["video_id"] for r in labels})
        videos = {vid: fetch_video(c, cache, vid) for vid in video_ids}

    built = {}
    for vid, data in videos.items():
        segments = fetch_segments(cache, data["video"]["video_id"], args.youtube_proxy)
        anchors: dict[str, list[str]] = {}
        for r in labels:
            if r["video_id"] == vid:
                anchors.setdefault(SITE_TO_SLUG[r["instrument"]], []).append(r["quote"])
        built[vid] = build_video_items(data, segments, slugs, anchors)

    def run(vid: int) -> tuple[int, dict]:
        items, _ = built[vid]
        ids = {m.id for m, _, _ in items}
        block = outlook.build_items_block(items)
        results = {}
        for name, template in (("v3", baseline), ("v4", OUTLOOK_PROMPT_TEMPLATE)):
            try:
                payload = call_prompt(cache, template, videos[vid], block)
                parse = parse_v3 if name == "v3" else outlook.parse_outlook_items
                results[name] = parse(payload, ids)
                results[name + "_raw"] = {
                    int(i["mention_id"]): i for i in payload.get("items") or []
                    if isinstance(i, dict) and str(i.get("mention_id", "")).isdigit()
                }
            except Exception as exc:  # noqa: BLE001
                print(f"video {vid} {name}: {exc}", file=sys.stderr)
                results[name] = None
        return vid, results

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        outputs = dict(pool.map(run, list(built)))

    rows = []
    for label in labels:
        items, mode = built[label["video_id"]]
        mid = pick_mention(label, items)
        res = outputs[label["video_id"]]
        row = {**label, "mention_id": mid, "excerpt_mode": mode}
        for name in ("v3", "v4"):
            parsed = res.get(name)
            row[name] = parsed.get(mid) if parsed and mid else None
            raw = (res.get(name + "_raw") or {}).get(mid) or {}
            row[name + "_time"] = raw.get("time")
            row[name + "_own"] = raw.get("own_view")
            row[name + "_quote"] = raw.get("quote") or ""
        rows.append(row)

    s3, s4 = score(rows, "v3"), score(rows, "v4")
    caption_rows = [r for r in rows if r["excerpt_mode"] != "proportional"]
    # Noise-adjusted keep: only the keep rows the baseline rerun itself still reproduces.
    stable = [
        r for r in rows
        if r["label"] == "keep" and r["v3"] and r["v3"]["outlook_explicit"]
        and r["v3"]["outlook"] == r["current"]
    ]
    n4 = score(stable, "v4")["keep"]
    c3, c4 = score(caption_rows, "v3"), score(caption_rows, "v4")
    modes = Counter(mode for _, mode in built.values())
    lines = [
        f"# Outlook prompt eval — baseline {args.baseline_ref} (v3) vs {OUTLOOK_PROMPT_VERSION} "
        f"({args.tag})",
        "",
        f"Labelled rows with a quote: {len(rows)}; videos: {len(built)}; excerpt modes: "
        f"{dict(modes)}. Speaker context = channel name.",
        "Production v3 = the live site (every labelled row is shown as explicit by construction).",
        "",
        "| metric | target | production v3 | v3 rerun | v4 |",
        "|---|---|---|---|---|",
        f"| none_current_state no longer explicit | >= 90% | 0/{s3['none_current_state'][1]} "
        f"| {pct(s3['none_current_state'])} | {pct(s4['none_current_state'])} |",
        f"| none_other no longer explicit | >= 75% | 0/{s3['none_other'][1]} "
        f"| {pct(s3['none_other'])} | {pct(s4['none_other'])} |",
        f"| keep still explicit, same direction | >= 85% | {s3['keep'][1]}/{s3['keep'][1]} "
        f"| {pct(s3['keep'])} | {pct(s4['keep'])} |",
        f"| keep, rows the v3 rerun reproduces (noise-adjusted) | | | {len(stable)}/{len(stable)} "
        f"| {pct(n4)} |",
        f"| (none_current_state explicit on a different sentence) | | | "
        f"{pct(s3['none_current_state_requoted'])} | {pct(s4['none_current_state_requoted'])} |",
        f"| (none_other explicit on a different sentence) | | | "
        f"{pct(s3['none_other_requoted'])} | {pct(s4['none_other_requoted'])} |",
        "",
        f"Caption-timed subset only (exact + aligned excerpts, {len(caption_rows)} rows):",
        "",
        "| metric | v3 rerun | v4 |",
        "|---|---|---|",
        f"| none_current_state no longer explicit | {pct(c3['none_current_state'])} "
        f"| {pct(c4['none_current_state'])} |",
        f"| none_other no longer explicit | {pct(c3['none_other'])} | {pct(c4['none_other'])} |",
        f"| keep still explicit, same direction | {pct(c3['keep'])} | {pct(c4['keep'])} |",
        "",
        "`*` = explicit. v4 time/own = model fields before the code guard.",
        "",
        "| idx | instr | video | label | site | v3 rerun | v4 | v4 time/own | quote (label) "
        "| v4 quote | note |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def cell(text: str) -> str:
        return (text or "").replace("|", "/").replace("\n", " ")[:140]

    for r in rows:
        lines.append(
            f"| {r['idx']} | {r['instrument']} | {r['video_id']} | {r['label']} | {r['current']} "
            f"| {fmt(r['v3'])} | {fmt(r['v4'])} | {r['v4_time']}/{r['v4_own']} "
            f"| {cell(r['quote'])} | {cell(r['v4_quote'])} | {cell(r.get('note', ''))} |"
        )
    args.out.write_text("\n".join(lines) + "\n")
    (args.out.with_suffix(".json")).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
