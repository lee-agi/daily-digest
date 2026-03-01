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
        if not urls:
            return []

        # Use explicit transport to fully bypass system proxy env vars
        # (http_proxy). httpx[socks] + proxy=None still routes through
        # SOCKS proxy in httpx 0.28; AsyncHTTPTransport avoids this.
        transport = httpx.AsyncHTTPTransport()
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True, transport=transport,
        ) as client:
            # Quick reachability check for RSSHub (HEAD, 3s timeout)
            try:
                probe = await client.head(
                    self.rsshub_base, timeout=3.0,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout):
                logger.warning(
                    "[%s] RSSHub at %s unreachable, skipping",
                    self.source_name, self.rsshub_base,
                )
                return []

            for url in urls:
                try:
                    resp = await self._request_with_retry(client, url)
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)

                    for entry in feed.entries:
                        item = self._parse_entry(entry)
                        if item.published_at >= self.cutoff_time:
                            items.append(item)

                except httpx.HTTPError as e:
                    logger.error("[%s] Failed to fetch %s: %s", self.source_name, url, e)

        return items



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


class XiaoyuzhouCollector(BaseCollector):
    """小宇宙播客 via 直接 RSS feeds (xyzfm.space proxy).

    Reads `feeds` list from config — each entry is a full RSS URL.
    Uses system proxy (unlike RSSHubCollector which bypasses it),
    since xyzfm.space is an external service.
    """
    source_name = "xiaoyuzhou"
    source_type = SourceType.RSS

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.feeds = config.get("feeds", [])

    async def collect(self) -> list[ContentItem]:
        if not self.feeds:
            logger.info("[xiaoyuzhou] No feeds configured")
            return []

        items = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for feed_url in self.feeds:
                try:
                    resp = await self._request_with_retry(client, feed_url)
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)
                except httpx.HTTPError as e:
                    logger.error("[xiaoyuzhou] Failed to fetch %s: %s", feed_url, e)
                    continue

                podcast_title = feed.feed.get("title", "Unknown Podcast")

                for entry in feed.entries:
                    item = self._parse_entry(entry, podcast_title)
                    if item and item.published_at >= self.cutoff_time:
                        items.append(item)

        return items

    def _parse_entry(self, entry: dict, podcast_title: str) -> ContentItem | None:
        """Parse a podcast RSS entry."""
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

        duration = entry.get("itunes_duration", "")

        # Parse enclosure file size (bytes)
        file_size_bytes = 0
        enclosures = entry.get("enclosures", [])
        if enclosures:
            try:
                file_size_bytes = int(enclosures[0].get("length", 0))
            except (ValueError, TypeError, IndexError):
                pass

        return ContentItem(
            source="xiaoyuzhou",
            source_type=SourceType.RSS,
            title=f"[{podcast_title}] {entry.get('title', 'Untitled')}",
            url=entry.get("link", ""),
            author=podcast_title,
            content=entry.get("summary", "")[:500],
            published_at=published,
            score=0.0,
            tags=["podcast"],
            extra={
                "podcast": podcast_title,
                "duration": duration,
                "file_size_bytes": file_size_bytes,
            },
        )


class BaoyuBlogCollector(RSSHubCollector):
    """宝玉博客 via RSSHub /baoyu/blog."""
    source_name = "baoyu_blog"
