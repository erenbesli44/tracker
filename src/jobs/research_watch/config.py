"""YAML config loader for the research watch job (config/watched_research.yaml)."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from src.config import settings

AdapterName = Literal["tcmb_blog"]

DEFAULT_USER_AGENT = "dovizveri-bot/0.1 (+https://dovizveri.com/iletisim)"


class ResearchSource(BaseModel):
    """One entry from watched_research.yaml."""

    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1)
    adapter: AdapterName
    url: str
    disclaimer: str | None = None
    auto_publish: bool = False
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value


class ResearchWatchDefaults(BaseModel):
    user_agent: str = DEFAULT_USER_AGENT
    max_runs_per_day: int = Field(default=2, ge=1)
    lookback_days: int = Field(default=21, ge=1)
    max_new_items: int = Field(default=5, ge=1)


class ResearchWatchConfig(BaseModel):
    sources: list[ResearchSource] = Field(default_factory=list)
    defaults: ResearchWatchDefaults = Field(default_factory=ResearchWatchDefaults)

    @model_validator(mode="after")
    def _unique_slugs(self) -> "ResearchWatchConfig":
        slugs = [s.slug for s in self.sources]
        if len(slugs) != len(set(slugs)):
            raise ValueError("duplicate source slug")
        return self

    @property
    def enabled_sources(self) -> list[ResearchSource]:
        return [s for s in self.sources if s.enabled]

    def source(self, slug: str) -> ResearchSource | None:
        return next((s for s in self.sources if s.slug == slug), None)


def load_research_config(path: str | None = None) -> ResearchWatchConfig:
    """Load and validate watched_research.yaml.

    Raises:
        FileNotFoundError: config file does not exist.
        ValueError: config file is structurally invalid.
    """
    config_path = Path(path or settings.RESEARCH_WATCH_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(f"Research watch config not found: {config_path}")
    with config_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError("Invalid config: root must be a YAML mapping")
    try:
        return ResearchWatchConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid research config: {exc}") from exc
