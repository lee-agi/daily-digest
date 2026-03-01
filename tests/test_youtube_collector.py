"""Tests for YouTube collector (Data API v3).

Covers:
1. Uploads playlist ID derivation (UC... → UU...)
2. _parse_playlist_item() parsing
3. No API key returns empty list
4. Integration test (requires real YOUTUBE_DATA_API_KEY)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.youtube_collector import (
    YouTubeCollector,
    _uploads_playlist_id,
)


# ---------------------------------------------------------------------------
# Uploads playlist ID derivation
# ---------------------------------------------------------------------------


class TestUploadsPlaylistId:
    """UC prefix → UU prefix."""

    def test_standard_channel(self) -> None:
        assert _uploads_playlist_id("UCXl4i9dYBrFOabk0xGmbkRA") == "UUXl4i9dYBrFOabk0xGmbkRA"

    def test_already_uu(self) -> None:
        """Non-UC prefix passes through unchanged."""
        assert _uploads_playlist_id("UUXl4i9dYBrFOabk0xGmbkRA") == "UUXl4i9dYBrFOabk0xGmbkRA"

    def test_short_channel_id(self) -> None:
        assert _uploads_playlist_id("UC") == "UU"

    def test_non_uc_prefix(self) -> None:
        assert _uploads_playlist_id("PLsomething") == "PLsomething"


# ---------------------------------------------------------------------------
# _parse_playlist_item
# ---------------------------------------------------------------------------


SAMPLE_PLAYLIST_ITEM = {
    "snippet": {
        "title": "Building the Future of AI",
        "channelTitle": "Andrej Karpathy",
        "description": "In this video we explore...",
        "resourceId": {"videoId": "dQw4w9WgXcQ"},
        "publishedAt": "2026-02-27T18:00:00Z",
    },
    "contentDetails": {
        "videoPublishedAt": "2026-02-27T18:00:00Z",
    },
}


class TestParsePlaylistItem:
    """Test _parse_playlist_item() output."""

    def _make_collector(self) -> YouTubeCollector:
        return YouTubeCollector({
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 30,
            "channels": [],
        })

    def test_basic_parsing(self) -> None:
        collector = self._make_collector()
        item = collector._parse_playlist_item(SAMPLE_PLAYLIST_ITEM, "UCXUPKJO5MZQN11PqgIvyuvQ")

        assert item is not None
        assert item.title == "Building the Future of AI"
        assert item.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert item.author == "Andrej Karpathy"
        assert item.source == "youtube"
        assert item.extra["channel_id"] == "UCXUPKJO5MZQN11PqgIvyuvQ"
        assert item.extra["video_id"] == "dQw4w9WgXcQ"
        assert "video" in item.tags

    def test_published_at_parsed(self) -> None:
        collector = self._make_collector()
        item = collector._parse_playlist_item(SAMPLE_PLAYLIST_ITEM, "UCtest")

        assert item is not None
        assert item.published_at.year == 2026
        assert item.published_at.month == 2
        assert item.published_at.day == 27

    def test_private_video_skipped(self) -> None:
        collector = self._make_collector()
        private_item = {
            "snippet": {
                "title": "Private video",
                "channelTitle": "Some Channel",
                "description": "",
                "resourceId": {"videoId": "abc12345678"},
            },
            "contentDetails": {},
        }
        result = collector._parse_playlist_item(private_item, "UCtest")
        assert result is None

    def test_deleted_video_skipped(self) -> None:
        collector = self._make_collector()
        deleted_item = {
            "snippet": {
                "title": "Deleted video",
                "channelTitle": "Some Channel",
                "description": "",
                "resourceId": {"videoId": "abc12345678"},
            },
            "contentDetails": {},
        }
        result = collector._parse_playlist_item(deleted_item, "UCtest")
        assert result is None

    def test_missing_video_id(self) -> None:
        collector = self._make_collector()
        item = {
            "snippet": {
                "title": "No Video ID",
                "resourceId": {},
            },
            "contentDetails": {},
        }
        result = collector._parse_playlist_item(item, "UCtest")
        assert result is None

    def test_uses_content_details_date(self) -> None:
        """contentDetails.videoPublishedAt takes priority."""
        collector = self._make_collector()
        item = {
            "snippet": {
                "title": "Test",
                "channelTitle": "Test",
                "description": "",
                "resourceId": {"videoId": "abc12345678"},
                "publishedAt": "2026-01-01T00:00:00Z",
            },
            "contentDetails": {
                "videoPublishedAt": "2026-02-15T12:00:00Z",
            },
        }
        result = collector._parse_playlist_item(item, "UCtest")
        assert result is not None
        assert result.published_at.month == 2
        assert result.published_at.day == 15


# ---------------------------------------------------------------------------
# No API key → empty list
# ---------------------------------------------------------------------------


class TestNoApiKey:
    @pytest.mark.asyncio
    async def test_returns_empty_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
        collector = YouTubeCollector({
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 30,
            "channels": ["UCXl4i9dYBrFOabk0xGmbkRA"],
        })
        items = await collector.collect()
        assert items == []

    @pytest.mark.asyncio
    async def test_returns_empty_with_blank_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "  ")
        collector = YouTubeCollector({
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 30,
            "channels": ["UCXl4i9dYBrFOabk0xGmbkRA"],
        })
        items = await collector.collect()
        assert items == []


# ---------------------------------------------------------------------------
# extract_video_id
# ---------------------------------------------------------------------------


class TestExtractVideoId:
    def test_standard_url(self) -> None:
        assert YouTubeCollector._extract_video_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ) == "dQw4w9WgXcQ"

    def test_short_url(self) -> None:
        assert YouTubeCollector._extract_video_id(
            "https://youtu.be/dQw4w9WgXcQ"
        ) == "dQw4w9WgXcQ"

    def test_no_match(self) -> None:
        assert YouTubeCollector._extract_video_id("https://example.com") is None


# ---------------------------------------------------------------------------
# Integration test (requires YOUTUBE_DATA_API_KEY)
# ---------------------------------------------------------------------------


class TestIntegration:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_collect_real_channel(self) -> None:
        """Fetch real uploads from Andrej Karpathy's channel."""
        import os
        api_key = os.environ.get("YOUTUBE_DATA_API_KEY", "").strip()
        if not api_key:
            pytest.skip("YOUTUBE_DATA_API_KEY not set")

        collector = YouTubeCollector({
            "score_threshold": 0,
            "lookback_hours": 720,  # 30 days to ensure results
            "max_items": 5,
            "channels": ["UCXUPKJO5MZQN11PqgIvyuvQ"],  # Andrej Karpathy
        })
        result = await collector.run()

        assert result.success, f"Collector failed: {result.error}"
        assert result.source == "youtube"
        # Karpathy posts infrequently, so may have 0 items in 30 days
        # but the API call itself should succeed
