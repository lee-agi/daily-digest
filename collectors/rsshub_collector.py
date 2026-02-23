"""Generic RSSHub/Atom collector.

Reusable for any RSSHub-based source: 知乎, 即刻, 小宇宙, etc.
Each source is a separate subclass with minimal config overrides.
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


class RSSHubCollector(BaseCollector):
    """Base class for all RSSHub-based sources.

    Not registered directly (source_name=""). Subclasses set source_name.
    """
    source_name = ""
    source_type = SourceType.RSS

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.rsshub_base = config.get("rsshub_base", "http://localhost:1200")

    def _build_feed_urls(self) -> list[str]:
        """Build the RSSHub feed URL(s). Override in subclasses for multi-feed sources."""
        route = self.config.get("route", "")
        return [f"{self.rsshub_base}{route}"]

    def _parse_score(self, entry: dict) -> float:
        """Extract engagement score from feed entry. Override per source."""
        return 0.0

    def _parse_entry(self, entry: dict) -> ContentItem:
        """Convert a feedparser entry to ContentItem."""
        # Parse published date
        published = None
        for date_field in ("published_parsed", "updated_parsed"):
            parsed_time = entry.get(date_field)
            if parsed_time:
                try:
                    published = datetime(*parsed_time[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    pass
                break

        if published is None:
            for date_str_field in ("published", "updated"):
                date_str = entry.get(date_str_field, "")
                if date_str:
                    try:
                        published = dateutil_parser.parse(date_str)
                        if published.tzinfo is None:
                            published = published.replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        pass
                    break

        if published is None:
            published = datetime.now(timezone.utc)

        # Extract content
        content = ""
        if entry.get("summary"):
            content = entry["summary"]
        elif entry.get("content"):
            content = entry["content"][0].get("value", "")

        return ContentItem(
            source=self.source_name,
            source_type=SourceType.RSS,
            title=entry.get("title", "Untitled"),
            url=entry.get("link", ""),
            author=entry.get("author", ""),
            content=content,
            published_at=published,
            score=self._parse_score(entry),
            language=entry.get("language", "zh"),
        )

    async def collect(self) -> list[ContentItem]:
        """Fetch and parse RSS/Atom feed."""
        items = []
        urls = self._build_feed_urls()

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)

                    for entry in feed.entries:
                        item = self._parse_entry(entry)
                        if item.published_at >= self.cutoff_time:
                            items.append(item)

                except httpx.HTTPError as e:
                    logger.error("[%s] Failed to fetch %s: %s", self.source_name, url, e)

        return items


class ZhihuCollector(RSSHubCollector):
    """知乎热榜 via RSSHub /zhihu/hot."""
    source_name = "zhihu"

    def _parse_score(self, entry: dict) -> float:
        # RSSHub zhihu hot includes heat score in description
        # Try to extract numeric value from entry
        summary = entry.get("summary", "")
        # zhihu hot entries often contain vote counts
        try:
            for field in ("slash:comments", "slash:hit"):
                if field in entry:
                    return float(entry[field])
        except (ValueError, TypeError):
            pass
        return 0.0


class JikeCollector(RSSHubCollector):
    """即刻用户动态 via RSSHub /jike/user/:id."""
    source_name = "jike"

    def _build_feed_urls(self) -> list[str]:
        user_ids = self.config.get("user_ids", [])
        route_template = self.config.get("route", "/jike/user/{user_id}")
        return [
            f"{self.rsshub_base}{route_template.format(user_id=uid)}"
            for uid in user_ids
        ] if user_ids else []

    def _parse_score(self, entry: dict) -> float:
        return 0.0  # Jike RSS doesn't expose like counts


class XiaoyuzhouCollector(RSSHubCollector):
    """小宇宙播客 via RSSHub /xiaoyuzhou/podcast/:id."""
    source_name = "xiaoyuzhou"

    def _build_feed_urls(self) -> list[str]:
        podcast_ids = self.config.get("podcast_ids", [])
        route_template = self.config.get("route", "/xiaoyuzhou/podcast/{podcast_id}")
        return [
            f"{self.rsshub_base}{route_template.format(podcast_id=pid)}"
            for pid in podcast_ids
        ] if podcast_ids else []
