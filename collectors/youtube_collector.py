"""YouTube collector via native RSS feeds."""

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

# YouTube channel RSS feed template
YT_RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


class YouTubeCollector(BaseCollector):
    """Collect new videos from subscribed YouTube channels via RSS."""
    source_name = "youtube"
    source_type = SourceType.RSS

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.channels = config.get("channels", [])

    async def collect(self) -> list[ContentItem]:
        if not self.channels:
            logger.info("[youtube] No channels configured")
            return []

        items = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for channel_id in self.channels:
                url = YT_RSS_URL.format(channel_id=channel_id)
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)
                except httpx.HTTPError as e:
                    logger.error("[youtube] Failed to fetch channel %s: %s", channel_id, e)
                    continue

                for entry in feed.entries:
                    item = self._parse_entry(entry, channel_id)
                    if item and item.published_at >= self.cutoff_time:
                        items.append(item)

        return items

    def _parse_entry(self, entry: dict, channel_id: str) -> ContentItem | None:
        """Parse a YouTube RSS entry."""
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

        # YouTube RSS includes media:statistics with view count
        views = 0
        media_stats = entry.get("media_statistics", {})
        if isinstance(media_stats, dict):
            try:
                views = int(media_stats.get("views", 0))
            except (ValueError, TypeError):
                pass

        return ContentItem(
            source="youtube",
            source_type=SourceType.RSS,
            title=entry.get("title", "Untitled"),
            url=entry.get("link", ""),
            author=entry.get("author", ""),
            content=entry.get("summary", ""),
            published_at=published,
            score=float(views),
            tags=["video"],
            extra={"channel_id": channel_id, "views": views},
        )
