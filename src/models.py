from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


def utc_now() -> datetime:
    """Return a naive UTC datetime (consistent SQLite storage)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CustomModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )
