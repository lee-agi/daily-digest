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


def _sample_rss_xml(
    channel_id: str = "UCXl4i9dYBrFOabk0xGmbkRA",
    video_id: str = "dQw4w9WgXcQ",
    title: str = "Test Video",
    published: str = "2026-02-27T18:00:00+00:00",
    description: str = "Test description",
) -> str:
    """Return a minimal YouTube Atom RSS feed XML string."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Test Channel</title>
  <id>yt:channel:{channel_id}</id>
  <entry>
    <id>yt:video:{video_id}</id>
    <yt:videoId>{video_id}</yt:videoId>
    <title>{title}</title>
    <published>{published}</published>
    <media:group>
      <media:description>{description}</media:description>
    </media:group>
  </entry>
</feed>"""


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
    async def test_no_channels_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No channels configured → always returns empty regardless of API key."""
        monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
        collector = YouTubeCollector({
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 30,
            "channels": [],
        })
        items = await collector.collect()
        assert items == []

    @pytest.mark.asyncio
    async def test_rss_fallback_when_no_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without API key, should use RSS fallback and return items."""
        monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
        collector = YouTubeCollector({
            "score_threshold": 0,
            "lookback_hours": 720,  # wide window to include test data
            "max_items": 30,
            "channels": ["UCXl4i9dYBrFOabk0xGmbkRA"],
        })
        rss_xml = _sample_rss_xml(
            channel_id="UCXl4i9dYBrFOabk0xGmbkRA",
            video_id="dQw4w9WgXcQ",
            title="RSS Fallback Video",
            published="2026-02-27T18:00:00+00:00",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = rss_xml

        with patch("collectors.youtube_collector.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            items = await collector.collect()

        assert len(items) == 1
        assert items[0].source == "youtube"
        assert "dQw4w9WgXcQ" in items[0].url
        assert items[0].title == "RSS Fallback Video"


# ---------------------------------------------------------------------------
# RSS fallback behavior
# ---------------------------------------------------------------------------


class TestRSSFallback:
    """Tests for RSS fallback when API key is missing or returns 403."""

    def _make_collector(self, channels=None) -> YouTubeCollector:
        return YouTubeCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 30,
            "channels": channels or ["UCXl4i9dYBrFOabk0xGmbkRA"],
        })

    @pytest.mark.asyncio
    async def test_403_triggers_rss_fallback_for_remaining_channels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When first channel returns 403, all remaining channels use RSS fallback."""
        monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "invalid-key")
        collector = self._make_collector(
            channels=["UCfirst", "UCsecond"]
        )

        rss_xml = _sample_rss_xml(
            channel_id="UCfirst", video_id="vid001", title="RSS Video 1"
        )
        rss_xml2 = _sample_rss_xml(
            channel_id="UCsecond", video_id="vid002", title="RSS Video 2"
        )

        call_urls = []

        async def mock_request(method, url, **kwargs):
            call_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "googleapis.com" in url:
                # First API call → 403
                error_resp = MagicMock()
                error_resp.status_code = 403
                import httpx
                raise httpx.HTTPStatusError(
                    "403 Forbidden",
                    request=MagicMock(),
                    response=error_resp,
                )
            elif "UCfirst" in url:
                resp.text = rss_xml
            else:
                resp.text = rss_xml2
            return resp

        with patch("collectors.youtube_collector.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=mock_request)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            items = await collector.collect()

        assert len(items) == 2
        video_ids = {i.extra["video_id"] for i in items}
        assert video_ids == {"vid001", "vid002"}
        # Data API should only be called once (for first channel)
        api_calls = [u for u in call_urls if "googleapis.com" in u]
        assert len(api_calls) == 1

    @pytest.mark.asyncio
    async def test_rss_parse_atom_xml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_fetch_channel_via_rss should correctly parse Atom XML."""
        monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
        collector = self._make_collector()

        rss_xml = _sample_rss_xml(
            channel_id="UCXl4i9dYBrFOabk0xGmbkRA",
            video_id="testvideo123",
            title="Parsed RSS Title",
            published="2026-03-01T12:00:00+00:00",
            description="Parsed description",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = rss_xml

        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=5) as real_client:
            with patch.object(collector, "_request_with_retry", new=AsyncMock(return_value=mock_resp)):
                items = await collector._fetch_channel_via_rss(real_client, "UCXl4i9dYBrFOabk0xGmbkRA")

        assert len(items) == 1
        item = items[0]
        assert item.extra["video_id"] == "testvideo123"
        assert item.title == "Parsed RSS Title"
        assert item.url == "https://www.youtube.com/watch?v=testvideo123"
        assert item.content == "Parsed description"
        assert item.published_at.year == 2026
        assert item.published_at.month == 3
        assert item.extra.get("via_rss") is True


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
