"""YouTube collector via Data API v3 playlistItems.list.

YouTube RSS feeds have been permanently deprecated (404). This collector
uses the YouTube Data API v3 to fetch recent uploads from subscribed channels.

Requires ``YOUTUBE_DATA_API_KEY`` environment variable.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from dateutil import parser as dateutil_parser

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

# YouTube Data API v3 endpoints
YT_PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
YT_DATA_API_URL = "https://www.googleapis.com/youtube/v3/videos"

# Regex to extract video ID from standard YouTube URLs
_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})")


def _uploads_playlist_id(channel_id: str) -> str:
    """Derive the uploads playlist ID from a channel ID.

    YouTube channel IDs start with ``UC``; the uploads playlist replaces
    the ``UC`` prefix with ``UU``.
    """
    if channel_id.startswith("UC"):
        return "UU" + channel_id[2:]
    return channel_id


class YouTubeCollector(BaseCollector):
    """Collect new videos from subscribed YouTube channels via Data API v3."""
    source_name = "youtube"
    source_type = SourceType.API

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.channels = config.get("channels", [])

    async def collect(self) -> list[ContentItem]:
        api_key = os.environ.get("YOUTUBE_DATA_API_KEY", "").strip()
        if not api_key:
            logger.warning(
                "[youtube] YOUTUBE_DATA_API_KEY not set, skipping collection"
            )
            return []

        if not self.channels:
            logger.info("[youtube] No channels configured")
            return []

        items: list[ContentItem] = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for channel_id in self.channels:
                playlist_id = _uploads_playlist_id(channel_id)
                try:
                    resp = await self._request_with_retry(
                        client,
                        YT_PLAYLIST_ITEMS_URL,
                        params={
                            "playlistId": playlist_id,
                            "part": "snippet,contentDetails",
                            "maxResults": 5,
                            "key": api_key,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPError as e:
                    logger.error(
                        "[youtube] Failed to fetch playlist %s (channel %s): %s",
                        playlist_id, channel_id, e,
                    )
                    continue

                for pl_item in data.get("items", []):
                    item = self._parse_playlist_item(pl_item, channel_id)
                    if item and item.published_at >= self.cutoff_time:
                        items.append(item)

            # Fetch engagement stats from YouTube Data API
            await self._fetch_video_stats_batch(client, items)

        return items

    def _parse_playlist_item(
        self, pl_item: dict, channel_id: str,
    ) -> ContentItem | None:
        """Parse a playlistItems.list response item."""
        snippet = pl_item.get("snippet", {})
        content_details = pl_item.get("contentDetails", {})

        video_id = snippet.get("resourceId", {}).get("videoId", "")
        if not video_id:
            return None

        title = snippet.get("title", "Untitled")
        # Skip private/deleted videos
        if title in ("Private video", "Deleted video"):
            return None

        # Parse published date
        published = None
        date_str = content_details.get(
            "videoPublishedAt", snippet.get("publishedAt", ""),
        )
        if date_str:
            try:
                published = dateutil_parser.parse(date_str)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

        if published is None:
            published = datetime.now(timezone.utc)

        url = f"https://www.youtube.com/watch?v={video_id}"
        channel_title = snippet.get("channelTitle", "")
        description = snippet.get("description", "")

        return ContentItem(
            source="youtube",
            source_type=SourceType.API,
            title=title,
            url=url,
            author=channel_title,
            content=description[:500],
            published_at=published,
            score=0.0,
            tags=["video"],
            extra={"channel_id": channel_id, "video_id": video_id},
        )

    @staticmethod
    def _extract_video_id(url: str) -> str | None:
        """Extract the 11-char video ID from a YouTube URL."""
        m = _VIDEO_ID_RE.search(url)
        return m.group(1) if m else None

    async def _fetch_video_stats_batch(
        self, client: httpx.AsyncClient, items: list[ContentItem],
    ) -> None:
        """Fetch engagement stats from YouTube Data API v3 for all items.

        Updates each item's ``score`` to engagement_rate * 1e6 and stores
        raw stats in ``extra["youtube_stats"]``.
        """
        api_key = os.environ.get("YOUTUBE_DATA_API_KEY", "").strip()
        if not api_key or not items:
            return

        # Build video ID → item mapping
        id_to_items: dict[str, list[ContentItem]] = {}
        for item in items:
            vid = self._extract_video_id(item.url)
            if vid:
                id_to_items.setdefault(vid, []).append(item)

        if not id_to_items:
            return

        # YouTube API allows up to 50 IDs per request
        video_ids = list(id_to_items.keys())
        for batch_start in range(0, len(video_ids), 50):
            batch = video_ids[batch_start : batch_start + 50]
            try:
                resp = await self._request_with_retry(
                    client,
                    YT_DATA_API_URL,
                    params={
                        "part": "statistics",
                        "id": ",".join(batch),
                        "key": api_key,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning(
                    "[youtube] Data API stats fetch failed: %s", exc,
                )
                return

            for vid_data in data.get("items", []):
                vid = vid_data.get("id", "")
                stats = vid_data.get("statistics", {})
                view_count = int(stats.get("viewCount", 0))
                like_count = int(stats.get("likeCount", 0))
                comment_count = int(stats.get("commentCount", 0))

                engagement_rate = (
                    (like_count + comment_count) / max(view_count, 1)
                )

                for item in id_to_items.get(vid, []):
                    item.extra["youtube_stats"] = {
                        "viewCount": view_count,
                        "likeCount": like_count,
                        "commentCount": comment_count,
                        "engagementRate": engagement_rate,
                    }
                    item.score = engagement_rate * 1e6

        logger.info(
            "[youtube] Fetched Data API stats for %d videos", len(id_to_items),
        )
