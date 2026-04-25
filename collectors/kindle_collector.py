"""Kindle books collector (RSS/Atom feeds)."""

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


class KindleBooksCollector(BaseCollector):
    """Collect Kindle high-quality books from configurable feeds."""

    source_name = "kindle_books"
    source_type = SourceType.RSS

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.feeds = config.get("feeds", [])

    async def collect(self) -> list[ContentItem]:
        if not self.feeds:
            logger.info("[kindle_books] No feeds configured")
            return []

        items: list[ContentItem] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for feed_url in self.feeds:
                try:
                    resp = await self._request_with_retry(client, feed_url)
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)
                except httpx.HTTPError as exc:
                    logger.warning("[kindle_books] Failed to fetch %s: %s", feed_url, exc)
                    continue

                feed_title = feed.feed.get("title", "Kindle")
                for entry in feed.entries:
                    item = self._parse_entry(entry, feed_title)
                    if not item:
                        continue
                    if item.published_at < self.cutoff_time:
                        continue
                    if item.url and item.url in seen:
                        continue
                    if item.url:
                        seen.add(item.url)
                    items.append(item)

        return items

    def _parse_entry(self, entry: dict[str, Any], feed_title: str) -> ContentItem | None:
        title = (entry.get("title") or "").strip()
        if not title:
            return None

        content = entry.get("summary", "") or ""
        url = entry.get("link", "")
        author = entry.get("author", "")
        published_at = self._parse_published(entry)

        return ContentItem(
            source="kindle_books",
            source_type=SourceType.RSS,
            title=title,
            url=url,
            author=author,
            content=content[:500],
            published_at=published_at,
            score=0.0,
            tags=["book", "kindle"],
            language="en",
            extra={"feed_title": feed_title},
        )

    @staticmethod
    def _parse_published(entry: dict[str, Any]) -> datetime:
        for parsed_field in ("published_parsed", "updated_parsed"):
            parsed_time = entry.get(parsed_field)
            if parsed_time:
                try:
                    return datetime(*parsed_time[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    pass

        for raw_field in ("published", "updated"):
            raw_date = entry.get(raw_field)
            if not raw_date:
                continue
            try:
                dt = dateutil_parser.parse(raw_date)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (TypeError, ValueError):
                continue

        return datetime.now(timezone.utc)
