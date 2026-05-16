from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, model_validator


class InferenceSourceOut(BaseModel):
    video_id: int
    title: Optional[str] = None
    channel_name: Optional[str] = None
    person_name: Optional[str] = None
    weight_used: float
    contribution_note: Optional[str] = None


class InferenceTopicOut(BaseModel):
    topic_key: str
    topic_label: str
    direction: str
    confidence: float
    summary: str
    tags: list[str]
    status: str
    changed_from_prev: bool
    change_reason: Optional[str] = None
    sources: list[InferenceSourceOut] = []

    @model_validator(mode="before")
    @classmethod
    def parse_tags(cls, values):
        if isinstance(values, dict):
            raw = values.get("tags")
            if isinstance(raw, str):
                try:
                    values["tags"] = json.loads(raw)
                except Exception:
                    values["tags"] = []
            elif raw is None:
                values["tags"] = []
        return values


class InferenceRunOut(BaseModel):
    run_id: int
    run_date: str
    status: str
    generated_at: Optional[datetime] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    topics: list[InferenceTopicOut] = []


class InferenceRunSummary(BaseModel):
    run_id: int
    run_date: str
    status: str
    finished_at: Optional[datetime] = None


class InferenceTopicHistory(BaseModel):
    run_date: str
    direction: str
    confidence: float
    changed_from_prev: bool
