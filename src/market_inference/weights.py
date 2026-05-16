"""Deterministic weight resolution for inference sources.

`resolve_weight(channel_handle, person_slug, topic_key)` evaluates rules
from config/inference_weights.yaml top-to-bottom and returns the first match.
Pure function — no DB access, easy to unit-test.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "inference_weights.yaml"


@lru_cache(maxsize=1)
def _load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _normalize_handle(handle: str | None) -> str:
    if not handle:
        return ""
    return re.sub(r"[^a-z0-9]", "", handle.lstrip("@").lower())


def resolve_weight(
    channel_handle: str | None,
    person_slug: str | None,
    topic_key: str | None = None,
) -> float:
    """Return the effective weight for a (channel, person, topic) combination.

    Evaluates rules top-to-bottom; first match wins.
    Falls back to default_weight if no rule matches.
    """
    config = _load_config()
    default: float = float(config.get("default_weight", 1.0))
    channel_norm = _normalize_handle(channel_handle)
    person_norm = (person_slug or "").lower().strip()

    for rule in config.get("rules", []):
        rule_channel = _normalize_handle(rule.get("channel"))
        if rule_channel and rule_channel != channel_norm:
            continue

        rule_person = rule.get("person", "").lower().strip()
        if rule_person and rule_person != person_norm:
            continue

        rule_topics: list[str] | None = rule.get("topics")
        if rule_topics and topic_key not in rule_topics:
            continue

        return float(rule["weight"])

    return default


def is_included(
    channel_handle: str | None,
    person_slug: str | None,
    topic_key: str | None = None,
) -> bool:
    config = _load_config()
    min_w: float = float(config.get("min_weight_to_include", 0.1))
    return resolve_weight(channel_handle, person_slug, topic_key) >= min_w
