"""Google Blog collector via multiple RSS feeds with cross-feed dedup."""

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

DEFAULT_FEEDS = [
    "https://blog.google/innovation-and-ai/rss/",
    "https://blog.google/technology/ai/rss/",
]


class GoogleBlogCollector(BaseCollector):
    """Collect blog posts from Google Blog RSS feeds with cross-feed dedup."""

    source_name = "google_blog"
    source_type = SourceType.RSS

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.feeds: list[str] = config.get("feeds", DEFAULT_FEEDS)

    async def collect(self) -> list[ContentItem]:
        if not self.feeds:
            logger.info("[google_blog] No feeds configured")
            return []

        items: list[ContentItem] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for feed_url in self.feeds:
                try:
                    resp = await client.get(
                        feed_url,
                        headers={"User-Agent": "daily-digest/0.2.0"},
                    )
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)
                except httpx.HTTPError as e:
                    logger.error("[google_blog] Failed to fetch %s: %s", feed_url, e)
                    continue

                for entry in feed.entries:
                    item = self._parse_entry(entry)
                    if item is None:
                        continue

                    normalized_url = self._normalize_url(item.url)
                    if normalized_url in seen_urls:
                        continue
                    seen_urls.add(normalized_url)

                    if item.published_at >= self.cutoff_time:
                        items.append(item)

        return items

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize URL by stripping trailing slash for dedup."""
        return url.rstrip("/")

    def _parse_entry(self, entry: dict) -> ContentItem | None:
        """Parse a single RSS entry into a ContentItem."""
        title = entry.get("title", "").strip()
        if not title:
            return None

        published = self._parse_date(entry)
        tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]

        return ContentItem(
            source="google_blog",
            source_type=SourceType.RSS,
            title=title,
            url=entry.get("link", ""),
            author=entry.get("author", "Google"),
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
