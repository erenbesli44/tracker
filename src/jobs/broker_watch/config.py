"""YAML config loader for the broker watch job (config/watched_brokers.yaml)."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from src.config import settings

AdapterName = Literal["isy_rss", "yk_json", "gedik_page"]

DEFAULT_USER_AGENT = "dovizveri-bot/0.1 (+https://dovizveri.com/iletisim)"


class WatchedBroker(BaseModel):
    """One entry from watched_brokers.yaml."""

    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1)
    adapter: AdapterName
    url: str
    enabled: bool = True
    notice_text: str | None = None
    # isy_rss only: how many feed pages (?paged=N) discovery follows.
    max_pages: int = Field(default=3, ge=1)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value

    @property
    def site_url(self) -> str:
        """Scheme + host of the discovery URL, i.e. the broker's own domain."""
        parts = urlsplit(self.url)
        return f"{parts.scheme}://{parts.netloc}"


class BrokerWatchDefaults(BaseModel):
    user_agent: str = DEFAULT_USER_AGENT
    max_runs_per_day: int = Field(default=2, ge=1)


class BrokerWatchConfig(BaseModel):
    brokers: list[WatchedBroker] = Field(default_factory=list)
    defaults: BrokerWatchDefaults = Field(default_factory=BrokerWatchDefaults)

    @model_validator(mode="after")
    def _unique_slugs(self) -> "BrokerWatchConfig":
        seen: set[str] = set()
        for broker in self.brokers:
            if broker.slug in seen:
                raise ValueError(f"duplicate broker slug: {broker.slug}")
            seen.add(broker.slug)
        return self

    @property
    def enabled_brokers(self) -> list[WatchedBroker]:
        return [b for b in self.brokers if b.enabled]


def load_broker_config(path: str | None = None) -> BrokerWatchConfig:
    """Load and validate watched_brokers.yaml.

    Args:
        path: override the config file path; defaults to settings.BROKER_WATCH_CONFIG_PATH.

    Raises:
        FileNotFoundError: config file does not exist.
        ValueError: config file is structurally invalid.
    """
    config_path = Path(path or settings.BROKER_WATCH_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(f"Broker watch config not found: {config_path}")

    with config_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError("Invalid config: root must be a YAML mapping")

    try:
        return BrokerWatchConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid broker config: {exc}") from exc
