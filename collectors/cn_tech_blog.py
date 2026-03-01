"""Chinese tech blog collector via RSS feeds.

Collects from multiple Chinese tech/AI blog RSS sources:
- 机器之心 (Synced) via Wechat-Scholar GitHub RSS
- 量子位 (QbitAI) via Wechat-Scholar GitHub RSS
- AI洞察日报 via CloudFlare-hosted RSS
"""

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


class CnTechBlogCollector(BaseCollector):
    """Collect articles from Chinese tech/AI blog RSS feeds."""

    source_name = "cn_tech_blog"
    source_type = SourceType.RSS

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.feeds: list[str] = config.get("feeds", [])

    async def collect(self) -> list[ContentItem]:
        if not self.feeds:
            logger.info("[cn_tech_blog] No feeds configured")
            return []

        items: list[ContentItem] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for feed_url in self.feeds:
                try:
                    resp = await self._request_with_retry(
                        client, feed_url,
                        headers={"User-Agent": "daily-digest/0.9.0"},
                    )
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)
                except httpx.HTTPError as e:
                    logger.error(
                        "[cn_tech_blog] Failed to fetch %s: %s", feed_url, e
                    )
                    continue

                feed_title = feed.feed.get("title", "")
                for entry in feed.entries:
                    item = self._parse_entry(entry, feed_title)
                    if item is None:
                        continue

                    normalized = item.url.rstrip("/")
                    if normalized in seen_urls:
                        continue
                    seen_urls.add(normalized)

                    if item.published_at >= self.cutoff_time:
                        items.append(item)

        return items

    def _parse_entry(
        self, entry: dict, feed_title: str
    ) -> ContentItem | None:
        """Parse a single RSS entry into a ContentItem."""
        title = entry.get("title", "").strip()
        if not title:
            return None

        published = self._parse_date(entry)

        # Prefer content:encoded over summary for full text
        content = ""
        if entry.get("content"):
            content = entry["content"][0].get("value", "")
        elif entry.get("summary"):
            content = entry["summary"]

        tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
        if not tags:
            tags = ["blog", "ai"]

        return ContentItem(
            source="cn_tech_blog",
            source_type=SourceType.RSS,
            title=title,
            url=entry.get("link", ""),
            author=entry.get("author", feed_title),
            content=content[:500],
            published_at=published,
            score=0.0,
            tags=tags,
            language="zh",
            extra={"feed": feed_title},
        )

    @staticmethod
    def _parse_date(entry: dict) -> datetime:
        """Three-level date fallback: published_parsed -> dateutil -> now()."""
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
