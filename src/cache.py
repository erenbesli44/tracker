import json
import logging
from typing import Any

import redis as redis_lib

from src.config import settings

logger = logging.getLogger(__name__)

_client: redis_lib.Redis | None = None


def connect() -> None:
    global _client
    if not settings.REDIS_URL:
        logger.info("REDIS_URL not set; cache disabled")
        return
    _client = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    logger.info("Redis cache connected")


def disconnect() -> None:
    global _client
    if _client:
        _client.close()
        _client = None


def get_cached(key: str) -> dict[str, Any] | None:
    if _client is None:
        return None
    try:
        raw = _client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        logger.warning("Redis get failed key=%s", key, exc_info=True)
        return None


def set_cached(key: str, value: dict[str, Any], ttl: int | None = None) -> None:
    if _client is None:
        return
    try:
        serialized = json.dumps(value, default=str)
        if ttl:
            _client.setex(key, ttl, serialized)
        else:
            _client.set(key, serialized)
    except Exception:
        logger.warning("Redis set failed key=%s", key, exc_info=True)


def invalidate(key: str) -> None:
    if _client is None:
        return
    try:
        _client.delete(key)
    except Exception:
        logger.warning("Redis delete failed key=%s", key, exc_info=True)
