"""Guest attribution for multi-guest channels (Mesele Ekonomi, Integral Forextv, NCM).

These channels host different guests, so a view in their videos belongs to the
guest who said it, not to the channel. Per video we:

1. find the participants (title names + one LLM pass over the transcript intro),
2. match each guest to a Person (known aliases, then name/slug), creating a
   Person for a new finance guest — never a channel row,
3. let the outlook step name the speaker of every view. Any named participant
   can be a speaker (hosts too: a host's own forward-looking view is theirs;
   their questions are not views). An unnamed host, an unknown speaker or a
   name not in the list gets no person and is not shown on any page.
"""

import json
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from slugify import slugify
from sqlmodel import Session, select

from src.llm import service as llm_service
from src.llm.prompts import PARTICIPANTS_PROMPT_VERSION
from src.persons import detector
from src.persons.models import Person, VideoParticipants
from src.videos.models import Video

logger = logging.getLogger(__name__)

_INTRO_SECONDS = 240
_INTRO_MAX_CHARS = 4000
_NAME_SEPARATORS = re.compile(r"\s*(?:&|,|\+|\bve\b|\bile\b)\s*")
_NOT_A_NAME = re.compile(r"\d|[?!:]")


_HONORIFICS = re.compile(
    r"^(?:(?:prof|doç|doc|dr|av|uzm|yrd|öğr|ogr)\.?\s+)+", re.IGNORECASE
)


def clean_name(name: str) -> str:
    """Drop titles: 'Prof. Dr. Burak Arzova' -> 'Burak Arzova'."""
    return " ".join(_HONORIFICS.sub("", name.strip()).split())


def normalize_name(name: str) -> str:
    """Turkish-aware, accent-insensitive key: 'ATİLLA YEŞİLADA' == 'Atilla Yesilada'."""
    lowered = name.replace("I", "ı").replace("İ", "i").lower()
    folded = unicodedata.normalize("NFKD", lowered.replace("ı", "i"))
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z\s]", " ", ascii_only).split())


def title_names(title: str | None) -> list[str]:
    """Person-like names after the last '|' in 'Konu | Ad Soyad & Ad Soyad'."""
    if not title or "|" not in title:
        return []
    tail = title.rsplit("|", 1)[1]
    names = []
    for part in _NAME_SEPARATORS.split(tail):
        part = clean_name(part)
        words = part.split()
        looks_like_name = 2 <= len(words) <= 4 and not _NOT_A_NAME.search(part)
        if looks_like_name and all(w[0].isupper() for w in words):
            names.append(part)
    return names


def transcript_intro(segments: list[dict[str, Any]] | None) -> str:
    if not segments:
        return ""
    words = [
        str(s.get("text", "")).strip()
        for s in segments
        if float(s.get("start", 0.0)) <= _INTRO_SECONDS
    ]
    return " ".join(w for w in words if w)[:_INTRO_MAX_CHARS]


def resolve_participants(
    payload: dict[str, Any], names_in_title: list[str]
) -> list[dict[str, str]]:
    """Merge the LLM answer with title names into [{name, role}] (role host|guest).

    Title spelling wins. The model's host/guest label is unreliable (it called
    Atilla Yeşilada the host of a Mesele Ekonomi episode), so it is not used
    to drop anyone. A title name counts only when the model also lists it as a
    speaker (titles also hold show names like "Ekonomi Kulübü"); model-only
    names count only when the model says they are finance commentators.
    """
    by_key: dict[str, dict[str, str]] = {}
    title_keys = {normalize_name(n): n for n in names_in_title}
    raw = payload.get("participants") if isinstance(payload, dict) else None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = clean_name(str(item.get("name") or ""))
        key = _closest_title_key(normalize_name(name), title_keys)
        if not key or len(key.split()) < 2:
            continue
        role = "host" if str(item.get("role", "")).lower() == "host" else "guest"
        if key not in title_keys and item.get("finance") is not True:
            continue
        by_key[key] = {"name": title_keys.get(key, name), "role": role}
    return list(by_key.values())


def _closest_title_key(key: str, title_keys: dict[str, str]) -> str:
    """Map a model spelling like 'atila yesilada' onto the title's 'atilla yesilada'."""
    if key in title_keys or not key:
        return key
    best = max(title_keys, key=lambda t: SequenceMatcher(None, key, t).ratio(), default=None)
    if best and SequenceMatcher(None, key, best).ratio() >= 0.9:
        return best
    return key


def _known_names(session: Session) -> dict[str, tuple[str, str | None]]:
    """normalized name -> (display name, known slug or None) for config persons and DB persons."""
    names: dict[str, tuple[str, str | None]] = {}
    for entry in detector._load_config().get("persons", []):
        names[normalize_name(entry["name"])] = (entry["name"], entry["slug"])
        for alias in entry.get("aliases", []) or []:
            names.setdefault(normalize_name(alias), (entry["name"], entry["slug"]))
    for person in session.exec(select(Person)).all():
        names.setdefault(normalize_name(person.name), (person.name, person.slug))
    return names


def find_or_create_person(
    session: Session, name: str, *, create: bool = True, fuzzy: bool = False
) -> Person | None:
    """Existing person by exact (or, with fuzzy=True, near-identical) name, else a new one.

    Names read from auto-captions are often misspelt ("Cüneyt Faksoy"), so callers
    pass fuzzy=True and create=False for them: they may only match someone known.
    """
    from src.persons import service as persons_service

    slug = detector.detect_from_title(name)
    if slug:
        person = persons_service.upsert_known_person(session, slug)
        if person:
            return person
    key = normalize_name(name)
    known = _known_names(session)
    match = known.get(key)
    if match is None and fuzzy:
        best = max(known, key=lambda k: SequenceMatcher(None, key, k).ratio(), default=None)
        if best and SequenceMatcher(None, key, best).ratio() >= 0.88:
            match = known[best]
    if match is not None:
        display, known_slug = match
        person = persons_service.get_by_slug(session, known_slug) if known_slug else None
        if person is None and known_slug:
            person = persons_service.upsert_known_person(session, known_slug)
        if person is not None:
            return person
        name = display
    existing = persons_service.get_by_slug(session, slugify(name))
    if existing or not create:
        return existing
    # Deliberately not persons_service.create(): that also creates a channel row.
    # Backfill workers run in parallel: another one may create the same person.
    from sqlalchemy.exc import IntegrityError

    try:
        with session.begin_nested():
            person = Person(name=name.strip(), slug=slugify(name))
            session.add(person)
    except IntegrityError:
        return persons_service.get_by_slug(session, slugify(name))
    session.refresh(person)
    return person


def detect_participants(
    session: Session,
    video: Video,
    channel_name: str,
    segments: list[dict[str, Any]] | None,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Participants for one video, cached in video_participants. [{person_id, name, role}]."""
    if not dry_run:
        cached = session.get(VideoParticipants, video.id)
        if cached and cached.version == PARTICIPANTS_PROMPT_VERSION:
            return json.loads(cached.participants_json or "[]")

    names = title_names(video.title)
    payload = llm_service.generate_participants_json(
        channel_name=channel_name,
        video_title=video.title or "",
        title_names=", ".join(names) or "(yok)",
        transcript_intro=transcript_intro(segments) or "(yok)",
    )
    participants: list[dict[str, Any]] = []
    title_keys = {normalize_name(n) for n in names}
    title_text = f" {normalize_name(video.title or '')} "
    for p in resolve_participants(payload, names):
        key = normalize_name(p["name"])
        # Spelled exactly as in the title (tail names or "Alparslan Budak EKOTÜRK'te: ...").
        from_title = key in title_keys or f" {key} " in title_text
        person = find_or_create_person(
            session, p["name"], create=from_title and not dry_run, fuzzy=not from_title
        )
        participants.append(
            {"person_id": person.id if person else None, "name": p["name"], "role": p["role"]}
        )

    if not dry_run:
        row = session.get(VideoParticipants, video.id) or VideoParticipants(
            video_id=video.id, version=PARTICIPANTS_PROMPT_VERSION
        )
        row.participants_json = json.dumps(participants, ensure_ascii=False)
        row.version = PARTICIPANTS_PROMPT_VERSION
        session.add(row)
        session.flush()
    return participants


def speaker_context(participants: list[dict[str, Any]]) -> str:
    """The 'Kanal / konuşmacı' line for the outlook prompt on a multi-guest video."""
    listed = ", ".join(p["name"] for p in participants) or "(belirlenemedi)"
    return (
        f"Konuklu program. Konuşmacı listesi: {listed}. "
        "Her beklentiyi söyleyen kişiye yaz; soru sormak görüş değildir. "
        "Listede olmayan veya adı geçmeyen sunucu için \"sunucu\" yaz."
    )


def speaker_person_id(speaker: str | None, participants: list[dict[str, Any]]) -> int | None:
    """Map the outlook step's speaker name to a participant's person_id; else None."""
    key = normalize_name(speaker or "")
    if not key:
        return None
    named = [p for p in participants if p.get("person_id")]
    for p in named:
        if normalize_name(p["name"]) == key:
            return p["person_id"]
    # "Atilla Bey" / "Yeşilada": words of the speaker found in exactly one participant's name.
    words = set(key.split()) - {"bey", "hanim", "hoca", "hocam"}
    hits = [p for p in named if words and words <= set(normalize_name(p["name"]).split())]
    return hits[0]["person_id"] if len(hits) == 1 else None
