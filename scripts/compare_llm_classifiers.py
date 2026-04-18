"""Compare Gemini 3.1 Flash Lite vs GLM-4.7-Flash on a single YouTube video.

Read-only experiment: does not touch DB or production code paths other than
reusing their prompt builders and transcript fetcher.

Usage:
    GLM_API_KEY=... uv run python scripts/compare_llm_classifiers.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import settings  # noqa: E402

# Override any bad model name from .env for this experiment.
settings.GEMINI_MODEL = "gemini-3.1-flash-lite-preview"

from src.ingestion.service import (  # noqa: E402
    _prepare_transcript_for_llm,
    _render_segments_for_llm,
)
from src.llm import service as llm_service  # noqa: E402
from src.llm.prompts import ANALYSIS_PROMPT_TEMPLATE  # noqa: E402
from src.videos import service as videos_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("compare")

VIDEO_ID = "jhMUUsynnPc"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
ARTIFACTS = ROOT / "artifacts" / "compare"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
TRANSCRIPT_CACHE = ARTIFACTS / "transcript.json"

GLM_ENDPOINT = "https://api.z.ai/api/paas/v4/chat/completions"
GLM_MODEL = "GLM-4.7-Flash"
GLM_API_KEY = os.environ.get("GLM_API_KEY") or "d2a2a57ec752487382f812321c4be9fb.z4sFdfrJcLcJjDuI"

CHANNEL_NAME = "ATİLLA YEŞİLADA"
VIDEO_TITLE = ""  # filled after transcript fetch from video metadata, fallback below
PUBLISHED_AT = ""
SOURCE_PLATFORM = "youtube"
EXPECTED_SUBTOPICS_CTX = "bist-turk-piyasalari, doviz-kur, altin, faiz-para-politikasi, enflasyon, jeopolitik"


@dataclass
class RunSpec:
    run_id: str
    model: str         # "gemini" | "glm"
    transcript_mode: str  # "full" | "truncated"
    expected_subtopics: str  # "" or comma list


RUNS = [
    RunSpec("01_gemini_full",        "gemini", "full",      ""),
    RunSpec("02_glm_full",           "glm",    "full",      ""),
    RunSpec("03_gemini_truncated",   "gemini", "truncated", ""),
    RunSpec("04_glm_truncated",      "glm",    "truncated", ""),
    RunSpec("05_gemini_full_ctx",    "gemini", "full",      EXPECTED_SUBTOPICS_CTX),
]


def fetch_transcript() -> dict[str, Any]:
    if TRANSCRIPT_CACHE.exists():
        logger.info("Transcript cache hit: %s", TRANSCRIPT_CACHE)
        return json.loads(TRANSCRIPT_CACHE.read_text())

    logger.info("Fetching transcript for %s via videos_service...", VIDEO_ID)
    fetched = videos_service.fetch_transcript_from_youtube(VIDEO_ID, ["tr", "en"])
    data = {
        "video_id": VIDEO_ID,
        "full_text": fetched["full_text"],
        "language": fetched.get("language", "tr"),
        "segments": fetched.get("segments") or [],
    }
    TRANSCRIPT_CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    logger.info("Transcript cached (%d chars, %d segments)", len(data["full_text"]), len(data["segments"]))
    return data


def render_transcript(segments: list[dict[str, Any]], raw_text: str, mode: str) -> str:
    if mode == "full":
        rendered = _render_segments_for_llm(segments) if segments else raw_text
        return rendered or raw_text
    if mode == "truncated":
        return _prepare_transcript_for_llm(raw_text, transcript_segments=segments, max_chars=12000)
    raise ValueError(mode)


def build_prompt(transcript_text: str, expected_subtopics: str, published_at: str) -> str:
    values = {
        "source_platform": SOURCE_PLATFORM,
        "channel_name": CHANNEL_NAME,
        "speaker_name": CHANNEL_NAME,
        "video_title": VIDEO_TITLE or "Atilla Yeşilada — ekonomi değerlendirmesi",
        "published_at": published_at or "unknown",
        "source_url": VIDEO_URL,
        "transcript": transcript_text,
        "output_language": "tr",
        "channel_primary_topic": "ekonomi",
        "channel_expected_subtopics": expected_subtopics or "none specified",
    }
    rendered = ANALYSIS_PROMPT_TEMPLATE
    for k, v in values.items():
        rendered = rendered.replace(f"{{{{{k}}}}}", v)
    return rendered


def call_gemini(prompt: str) -> tuple[dict[str, Any], float]:
    start = time.time()
    payload = llm_service._call_gemini_json(prompt)  # reuses prod retry/JSON parse
    return payload, time.time() - start


def call_glm(prompt: str) -> tuple[dict[str, Any], float]:
    body = {
        "model": GLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 8192,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body).encode("utf-8")
    req = Request(
        GLM_ENDPOINT,
        data=data,
        headers={
            "Authorization": f"Bearer {GLM_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    start = time.time()
    try:
        with urlopen(req, timeout=300) as resp:  # nosec B310
            raw = resp.read().decode("utf-8")
    except HTTPError as exc:
        body_txt = ""
        try:
            body_txt = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"GLM HTTP {exc.code}: {body_txt[:500]}") from exc
    elapsed = time.time() - start
    envelope = json.loads(raw)
    content = envelope["choices"][0]["message"].get("content") or ""
    usage = envelope.get("usage", {})
    parsed = llm_service._extract_json_payload(content)
    parsed["__meta__"] = {"usage": usage, "raw_envelope_id": envelope.get("id")}
    return parsed, elapsed


def save_artifact(run_id: str, prompt: str, response: dict[str, Any], elapsed: float) -> None:
    out = ARTIFACTS / f"{run_id}.json"
    out.write_text(json.dumps(
        {"run_id": run_id, "elapsed_s": round(elapsed, 2), "response": response},
        ensure_ascii=False, indent=2,
    ))
    prompt_path = ARTIFACTS / f"{run_id}.prompt.txt"
    prompt_path.write_text(prompt)
    logger.info("Saved %s (%.1fs)", out.name, elapsed)


def run_one(spec: RunSpec, transcript_text: str, published_at: str) -> tuple[dict[str, Any] | None, float, str | None]:
    prompt = build_prompt(transcript_text, spec.expected_subtopics, published_at)
    try:
        if spec.model == "gemini":
            resp, elapsed = call_gemini(prompt)
        elif spec.model == "glm":
            resp, elapsed = call_glm(prompt)
        else:
            raise ValueError(spec.model)
    except Exception as exc:
        logger.error("Run %s failed: %s", spec.run_id, exc)
        return None, 0.0, str(exc)
    save_artifact(spec.run_id, prompt, resp, elapsed)
    return resp, elapsed, None


# -----------------------------------------------------------------------------
# Scoring vs gold
# -----------------------------------------------------------------------------

GOLD = {
    "subtopics": [
        {
            "slug": "jeopolitik", "stance": "negative",
            "key_levels": [],
            "start": "00:32", "end": "03:13",
        },
        {
            "slug": "petrol-enerji", "stance": "negative",
            "key_levels": ["60", "3", "18"],
            "start": "03:32", "end": "05:05",
        },
        {
            "slug": "enflasyon", "stance": "negative",
            "key_levels": ["0.9", "25", "27", "28", "32", "33"],
            "start": "04:55", "end": "12:07",
        },
        {
            "slug": "faiz-para-politikasi", "stance": "negative",
            "key_levels": [],
            "start": "05:20", "end": "16:26",
        },
        {
            "slug": "amerikan-piyasalari", "stance": "negative",
            "key_levels": ["20", "3", "6"],
            "start": "00:52", "end": "10:23",
        },
        {
            "slug": "altin", "stance": "cautious",
            "key_levels": ["4700", "5500", "5700", "4000"],
            "start": "14:05", "end": "14:22",
        },
        {
            "slug": "kripto-paralar", "stance": "cautious",
            "key_levels": [],
            "start": "14:22", "end": "15:07",
        },
    ],
    "primary_topic": "ekonomi",
}

GOLD_SLUGS = {s["slug"] for s in GOLD["subtopics"]}


def _normalize_levels(levels: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(levels, list):
        return out
    for raw in levels:
        s = str(raw).strip()
        if not s:
            continue
        # extract numbers only for fuzzy comparison
        import re as _re
        numbers = _re.findall(r"\d+[\.,]?\d*", s)
        out.extend(n.replace(",", ".") for n in numbers)
    return out


def score(payload: dict[str, Any]) -> dict[str, Any]:
    segs = payload.get("topic_segments") or []
    found: dict[str, dict[str, Any]] = {}
    extras: list[str] = []
    for s in segs:
        if not isinstance(s, dict):
            continue
        slug = str(s.get("subtopic", "")).strip().lower()
        if slug in GOLD_SLUGS:
            if slug not in found or float(s.get("confidence", 0)) > float(found[slug].get("confidence", 0)):
                found[slug] = s
        else:
            extras.append(slug)

    per_subtopic: list[dict[str, Any]] = []
    for gold in GOLD["subtopics"]:
        slug = gold["slug"]
        got = found.get(slug)
        if not got:
            per_subtopic.append({
                "slug": slug, "found": False,
            })
            continue
        got_levels = _normalize_levels(got.get("key_levels"))
        gold_levels = set(gold["key_levels"])
        hit_levels = [lvl for lvl in got_levels if lvl in gold_levels or any(lvl.startswith(gl) for gl in gold_levels)]
        level_recall = round(len(set(hit_levels)) / max(1, len(gold_levels)), 2) if gold_levels else None
        per_subtopic.append({
            "slug": slug,
            "found": True,
            "stance_gold": gold["stance"],
            "stance_got": str(got.get("stance", "")).strip().lower(),
            "stance_match": str(got.get("stance", "")).strip().lower() == gold["stance"],
            "start_time": got.get("start_time"),
            "end_time": got.get("end_time"),
            "times_present": bool(str(got.get("start_time") or "").strip() and str(got.get("end_time") or "").strip()),
            "confidence": got.get("confidence"),
            "level_recall": level_recall,
            "got_levels": got_levels[:12],
            "summary_len": len((got.get("summary") or "").strip()),
        })

    summary_block = payload.get("summary") or {}
    short = (summary_block.get("short") or "").strip() if isinstance(summary_block, dict) else ""
    detailed = (summary_block.get("detailed") or "").strip() if isinstance(summary_block, dict) else ""

    primary = payload.get("primary_topic") or {}
    primary_label = str(primary.get("label", "") if isinstance(primary, dict) else primary).strip().lower()

    return {
        "recall": f"{sum(1 for p in per_subtopic if p['found'])}/{len(GOLD['subtopics'])}",
        "extras": extras,
        "per_subtopic": per_subtopic,
        "short_summary_len": len(short),
        "detailed_summary_len": len(detailed),
        "key_points": len(payload.get("key_points") or []),
        "primary_topic": primary_label,
        "primary_topic_match": primary_label == GOLD["primary_topic"],
    }


def main() -> None:
    transcript = fetch_transcript()
    published_at = ""  # not critical for classification quality
    global VIDEO_TITLE
    VIDEO_TITLE = VIDEO_TITLE or "Atilla Yeşilada — değerlendirme"

    rendered_full = render_transcript(transcript["segments"], transcript["full_text"], "full")
    rendered_trunc = render_transcript(transcript["segments"], transcript["full_text"], "truncated")
    logger.info("Transcript chars — full: %d, truncated: %d", len(rendered_full), len(rendered_trunc))

    results: dict[str, dict[str, Any]] = {}
    for spec in RUNS:
        text = rendered_full if spec.transcript_mode == "full" else rendered_trunc
        logger.info("=== %s (model=%s transcript=%s chars=%d ctx=%r) ===",
                    spec.run_id, spec.model, spec.transcript_mode, len(text), spec.expected_subtopics)
        resp, elapsed, err = run_one(spec, text, published_at)
        if err:
            results[spec.run_id] = {"error": err, "elapsed_s": 0.0, "score": None}
            continue
        sc = score(resp)
        results[spec.run_id] = {"elapsed_s": round(elapsed, 2), "score": sc, "chars": len(text)}

    report = {
        "video_id": VIDEO_ID,
        "gold_subtopics": list(GOLD_SLUGS),
        "runs": results,
    }
    (ARTIFACTS / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    logger.info("Wrote report.json")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
