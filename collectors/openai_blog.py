"""OpenAI Blog collector via native RSS feed."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
from dateutil import parser as dateutil_parser

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

OPENAI_RSS_URL = "https://openai.com/blog/rss.xml"


class OpenAIBlogCollector(BaseCollector):
    """Collect blog posts from OpenAI's RSS feed."""

    source_name = "openai"
    source_type = SourceType.RSS

    async def collect(self) -> list[ContentItem]:
        items: list[ContentItem] = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            try:
                resp = await self._request_with_retry(
                    client, OPENAI_RSS_URL,
                    headers={"User-Agent": "daily-digest/0.2.0"},
                )
                resp.raise_for_status()
                feed = feedparser.parse(resp.text)
            except httpx.HTTPError as e:
                logger.error("[openai] Failed to fetch RSS: %s", e)
                return []

        for entry in feed.entries:
            item = self._parse_entry(entry)
            if item and item.published_at >= self.cutoff_time:
                items.append(item)

        return items

    def _parse_entry(self, entry: dict) -> ContentItem | None:
        """Parse a single RSS entry into a ContentItem."""
        title = entry.get("title", "").strip()
        if not title:
            return None

        published = self._parse_date(entry)
        tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]

        return ContentItem(
            source="openai",
            source_type=SourceType.RSS,
            title=title,
            url=entry.get("link", ""),
            author=entry.get("author", "OpenAI"),
            content=entry.get("summary", "")[:500],
            published_at=published,
            score=0.0,
            tags=tags if tags else ["blog"],
        )

    def _parse_date(self, entry: dict) -> datetime:
        """Three-level date fallback: published_parsed → dateutil → now()."""
        for date_field in ("published_parsed", "updated_parsed"):
            parsed_time = entry.get(date_field)
            if parsed_time:
                try:
                    return datetime(*parsed_time[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    pass

        for date_str_field in ("published", "updated"):
            date_str = entry.get(date_str_field, "")
            if date_str:
                try:
                    dt = dateutil_parser.parse(date_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except (ValueError, TypeError):
                    pass

        return datetime.now(timezone.utc)
