"""Data models for daily-digest pipeline."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field


class SourceType(str, Enum):
    """Supported source types for extensibility."""
    RSS = "rss"
    API = "api"
    HTTP_SCRAPE = "http_scrape"
    BROWSER_RELAY = "browser_relay"


class ContentItem(BaseModel):
    """A single content item from any source.

    This is the universal schema all collectors must produce.
    Extensible via `extra` dict for source-specific metadata.
    """
    source: str = Field(description="Source identifier, e.g. 'github', 'reddit'")
    source_type: SourceType
    title: str
    url: str = Field(default="")
    author: str = Field(default="")
    content: str = Field(default="", description="Full text or summary")
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    score: float = Field(default=0.0, description="Engagement score (likes, upvotes, etc.)")
    tags: list[str] = Field(default_factory=list)
    language: str = Field(default="en")
    arxiv_id: str | None = Field(default=None, description="ArXiv paper ID for dedup")
    extra: dict[str, Any] = Field(default_factory=dict, description="Source-specific metadata")

    @computed_field
    @property
    def content_hash(self) -> str:
        """SHA-256 hash for dedup: based on url (primary) or title+source."""
        if self.url:
            return hashlib.sha256(self.url.encode()).hexdigest()
        key = f"{self.source}:{self.title}"
        return hashlib.sha256(key.encode()).hexdigest()


class CollectorResult(BaseModel):
    """Result from a single collector run."""
    source: str
    success: bool
    items: list[ContentItem] = Field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0
    raw_count: int = Field(default=0, description="Items before filtering")
    filtered_count: int = Field(default=0, description="Items after filtering")


class DigestReport(BaseModel):
    """Final digest report ready for distribution."""
    date: str = Field(description="YYYY-MM-DD")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    headline_summary: str = Field(default="")
    category_sections: dict[str, str] = Field(
        default_factory=dict,
        description="Category name -> markdown content"
    )
    full_markdown: str = Field(default="")
    stats: dict[str, Any] = Field(default_factory=dict)
    items_count: int = 0
    sources_count: int = 0


class RunRecord(BaseModel):
    """Record of a pipeline run for state tracking."""
    run_id: str
    date: str
    phase: str  # "collect" | "summarize" | "full"
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"  # "running" | "success" | "partial" | "failed"
    collector_results: list[CollectorResult] = Field(default_factory=list)
    error: str | None = None
