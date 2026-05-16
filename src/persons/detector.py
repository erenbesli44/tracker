"""Title-based person detection.

Loads config/known_persons.yaml once and exposes a single pure function
`detect_person(title, channel_handle) -> slug | None`.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "known_persons.yaml"


@lru_cache(maxsize=1)
def _load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _normalize_handle(handle: str | None) -> str:
    if not handle:
        return ""
    return handle.lstrip("@").lower()


def _normalize_title(title: str) -> str:
    return title.lower()


def detect_from_title(title: str | None) -> str | None:
    """Return the first matching person slug found in the video title, or None."""
    if not title:
        return None
    normalized = _normalize_title(title)
    config = _load_config()
    for entry in config.get("persons", []):
        for alias in entry.get("aliases", []):
            if alias.lower() in normalized:
                return entry["slug"]
    return None


def detect_channel_default(channel_handle: str | None) -> str | None:
    """Return the default person slug for a channel (single-host channels)."""
    key = _normalize_handle(channel_handle)
    if not key:
        return None
    defaults: dict[str, str] = _load_config().get("channel_defaults", {})
    # Direct match
    if key in defaults:
        return defaults[key]
    # Partial match: strip non-alpha chars for Turkish handle variants
    key_alpha = re.sub(r"[^a-z0-9]", "", key)
    for handle_key, slug in defaults.items():
        if re.sub(r"[^a-z0-9]", "", handle_key.lower()) == key_alpha:
            return slug
    return None


def detect_person(title: str | None, channel_handle: str | None) -> str | None:
    """Detect person slug: title aliases first, then channel default."""
    slug = detect_from_title(title)
    if slug:
        return slug
    return detect_channel_default(channel_handle)


def list_known_person_slugs() -> list[str]:
    config = _load_config()
    return [entry["slug"] for entry in config.get("persons", [])]


def get_person_name_for_slug(slug: str) -> str | None:
    config = _load_config()
    for entry in config.get("persons", []):
        if entry["slug"] == slug:
            return entry["name"]
    return None
