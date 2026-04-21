"""Apple Podcast collector via native RSS feeds."""

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


class ApplePodcastCollector(BaseCollector):
    """Collect new episodes from Apple Podcast RSS feeds."""
    source_name = "apple_podcast"
    source_type = SourceType.RSS

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.feeds = config.get("feeds", [])

    async def collect(self) -> list[ContentItem]:
        if not self.feeds:
            logger.info("[apple_podcast] No feeds configured")
            return []

        items = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for feed_url in self.feeds:
                try:
                    resp = await self._request_with_retry(client, feed_url)
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)
                except httpx.HTTPError as e:
                    logger.error("[apple_podcast] Failed to fetch %s: %s", feed_url, e)
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

        # Get duration if available
        duration = entry.get("itunes_duration", "")

        # Parse enclosure audio URL and file size (bytes)
        audio_url = ""
        file_size_bytes = 0
        enclosures = entry.get("enclosures", [])
        if enclosures:
            audio_url = enclosures[0].get("href", "")
            try:
                file_size_bytes = int(enclosures[0].get("length", 0))
            except (ValueError, TypeError, IndexError):
                pass

        return ContentItem(
            source="apple_podcast",
            source_type=SourceType.RSS,
            title=f"[{podcast_title}] {entry.get('title', 'Untitled')}",
            url=entry.get("link", ""),
            author=podcast_title,
            content=entry.get("summary", "")[:500],
            published_at=published,
            score=0.0,  # RSS doesn't expose listen counts
            tags=["podcast"],
            extra={
                "podcast": podcast_title,
                "duration": duration,
                "audio_url": audio_url,
                "file_size_bytes": file_size_bytes,
            },
        )
