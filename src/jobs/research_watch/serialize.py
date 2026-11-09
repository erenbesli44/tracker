"""JSON shapes shared by the admin and public research routers."""

import json

from src.jobs.research_watch.config import ResearchWatchConfig
from src.jobs.research_watch.models import ResearchItem


def item_payload(item: ResearchItem, config: ResearchWatchConfig | None) -> dict:
    source = config.source(item.source_slug) if config else None
    return {
        "id": item.id,
        "source": {
            "slug": item.source_slug,
            "name": source.name if source else item.source_slug,
            "disclaimer": source.disclaimer if source else None,
        },
        "url": item.url,
        "title": item.title,
        "authors": json.loads(item.authors or "[]"),
        "category": item.category,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "digest": json.loads(item.digest_json) if item.digest_json else None,
        "digest_version": item.digest_version,
        "digested_at": item.digested_at.isoformat() if item.digested_at else None,
    }
