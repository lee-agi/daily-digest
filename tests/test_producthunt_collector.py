"""Tests for ProductHuntCollector.

Unit tests (no network):
  - Auto-registration
  - Missing token → empty result
  - _parse_post: valid node, missing name
  - _get_ph_day_range: format and boundary correctness

Integration tests (real API, skipped without token):
  - live collect returns up to max_items valid ContentItems
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import collectors.producthunt_collector  # noqa: F401 — triggers registration
from collectors.base import CollectorRegistry
from collectors.producthunt_collector import ProductHuntCollector
from schema import ContentItem, SourceType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_collector(token: str = "fake-token", max_items: int = 10) -> ProductHuntCollector:
    config = {"score_threshold": 0, "lookback_hours": 24, "max_items": max_items}
    with patch.dict(os.environ, {"PRODUCTHUNT_API_TOKEN": token}):
        return ProductHuntCollector(config)


def _sample_node(
    *,
    name: str = "Cool Tool",
    tagline: str = "The best tool ever",
    description: str = "A longer description",
    url: str = "https://www.producthunt.com/posts/cool-tool",
    votes: int = 420,
    created_at: str = "2026-02-26T10:00:00+00:00",
    topics: list[str] | None = None,
    author: str = "Jane Doe",
    ph_id: str = "123456",
) -> dict:
    topic_edges = [{"node": {"name": t}} for t in (topics if topics is not None else ["AI", "Productivity"])]
    return {
        "id": ph_id,
        "name": name,
        "tagline": tagline,
        "description": description,
        "url": url,
        "votesCount": votes,
        "createdAt": created_at,
        "topics": {"edges": topic_edges},
        "user": {"name": author},
        "thumbnail": {"url": "https://ph-files.imgix.net/thumb.png"},
    }


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_registered_as_producthunt(self):
        assert "producthunt" in CollectorRegistry.all_names()

    def test_registered_class_is_correct(self):
        assert CollectorRegistry.get("producthunt") is ProductHuntCollector


class TestInitAndToken:
    def test_token_loaded_from_env(self):
        with patch.dict(os.environ, {"PRODUCTHUNT_API_TOKEN": "my-secret"}):
            c = ProductHuntCollector({"score_threshold": 0})
        assert c.token == "my-secret"

    def test_missing_token_is_empty_string(self):
        env = {k: v for k, v in os.environ.items() if k != "PRODUCTHUNT_API_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            c = ProductHuntCollector({"score_threshold": 0})
        assert c.token == ""

    @pytest.mark.asyncio
    async def test_collect_returns_empty_without_token(self):
        env = {k: v for k, v in os.environ.items() if k != "PRODUCTHUNT_API_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            c = ProductHuntCollector({"score_threshold": 0, "max_items": 10})
        result = await c.collect()
        assert result == []


class TestParsePost:
    def setup_method(self):
        self.collector = _make_collector()

    def test_valid_node_returns_content_item(self):
        node = _sample_node()
        item = self.collector._parse_post(node)
        assert isinstance(item, ContentItem)
        assert item.title == "Cool Tool"
        assert item.score == 420.0
        assert item.source == "producthunt"
        assert item.source_type == SourceType.API
        assert item.url == "https://www.producthunt.com/posts/cool-tool"
        assert item.author == "Jane Doe"
        assert "AI" in item.tags
        assert "Productivity" in item.tags

    def test_description_preferred_over_tagline(self):
        node = _sample_node(description="Detailed desc", tagline="Short tagline")
        item = self.collector._parse_post(node)
        assert item.content == "Detailed desc"

    def test_tagline_fallback_when_no_description(self):
        node = _sample_node(description="", tagline="Short tagline")
        item = self.collector._parse_post(node)
        assert item.content == "Short tagline"

    def test_none_description_fallback_to_tagline(self):
        node = _sample_node()
        node["description"] = None
        item = self.collector._parse_post(node)
        assert item.content == node["tagline"]

    def test_missing_name_returns_none(self):
        node = _sample_node()
        node["name"] = ""
        assert self.collector._parse_post(node) is None

    def test_none_name_returns_none(self):
        node = _sample_node()
        node["name"] = None
        assert self.collector._parse_post(node) is None

    def test_extra_fields_populated(self):
        node = _sample_node(tagline="Best tool", ph_id="999")
        item = self.collector._parse_post(node)
        assert item.extra["tagline"] == "Best tool"
        assert item.extra["ph_id"] == "999"
        assert item.extra["thumbnail"] == "https://ph-files.imgix.net/thumb.png"

    def test_published_at_parsed_correctly(self):
        node = _sample_node(created_at="2026-02-26T10:00:00+00:00")
        item = self.collector._parse_post(node)
        assert item.published_at == datetime(2026, 2, 26, 10, 0, 0, tzinfo=timezone.utc)

    def test_published_at_naive_gets_utc(self):
        node = _sample_node(created_at="2026-02-26T10:00:00")
        item = self.collector._parse_post(node)
        assert item.published_at.tzinfo is not None

    def test_missing_created_at_uses_now(self):
        node = _sample_node()
        node["createdAt"] = ""
        before = datetime.now(timezone.utc)
        item = self.collector._parse_post(node)
        after = datetime.now(timezone.utc)
        assert before <= item.published_at <= after

    def test_empty_topics(self):
        node = _sample_node(topics=[])
        item = self.collector._parse_post(node)
        assert item.tags == []

    def test_null_thumbnail_handled(self):
        node = _sample_node()
        node["thumbnail"] = None
        item = self.collector._parse_post(node)
        assert item.extra["thumbnail"] == ""

    def test_votes_mapped_to_score(self):
        node = _sample_node(votes=1234)
        item = self.collector._parse_post(node)
        assert item.score == 1234.0


class TestPhDayRange:
    def setup_method(self):
        self.collector = _make_collector()

    def test_returns_two_iso_strings(self):
        after, before = self.collector._get_ph_day_range()
        assert isinstance(after, str)
        assert isinstance(before, str)

    def test_after_before_have_timezone_offset(self):
        after, before = self.collector._get_ph_day_range()
        # isoformat() on timezone-aware datetime includes offset
        assert "+" in after or "-" in after.split("T")[1]
        assert "+" in before or "-" in before.split("T")[1]

    def test_after_is_before_before(self):
        after, before = self.collector._get_ph_day_range()
        dt_after = datetime.fromisoformat(after)
        dt_before = datetime.fromisoformat(before)
        assert dt_after < dt_before

    def test_span_is_24_hours(self):
        after, before = self.collector._get_ph_day_range()
        dt_after = datetime.fromisoformat(after)
        dt_before = datetime.fromisoformat(before)
        assert dt_before - dt_after == timedelta(hours=24)

    def test_after_is_midnight_pacific(self):
        after, _ = self.collector._get_ph_day_range()
        dt = datetime.fromisoformat(after)
        # Midnight local time → time component is 00:00:00
        from zoneinfo import ZoneInfo
        dt_pt = dt.astimezone(ZoneInfo("America/Los_Angeles"))
        assert dt_pt.hour == 0
        assert dt_pt.minute == 0
        assert dt_pt.second == 0


class TestCollectMocked:
    """Test collect() with mocked httpx responses."""

    def _make_api_response(self, nodes: list[dict]) -> dict:
        return {
            "data": {
                "posts": {
                    "edges": [{"node": n} for n in nodes]
                }
            }
        }

    @pytest.mark.asyncio
    async def test_collect_returns_items_from_api(self):
        collector = _make_collector(max_items=5)
        nodes = [_sample_node(name=f"Product {i}", votes=100 - i) for i in range(3)]
        api_response = self._make_api_response(nodes)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = api_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            items = await collector.collect()

        assert len(items) == 3
        assert all(isinstance(i, ContentItem) for i in items)
        assert items[0].title == "Product 0"

    @pytest.mark.asyncio
    async def test_collect_skips_invalid_nodes(self):
        collector = _make_collector(max_items=5)
        nodes = [
            _sample_node(name="Valid Product"),
            {**_sample_node(), "name": ""},  # invalid — no name
        ]
        api_response = self._make_api_response(nodes)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = api_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            items = await collector.collect()

        assert len(items) == 1
        assert items[0].title == "Valid Product"

    @pytest.mark.asyncio
    async def test_collect_empty_response(self):
        collector = _make_collector()
        api_response = {"data": {"posts": {"edges": []}}}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = api_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            items = await collector.collect()

        assert items == []

    @pytest.mark.asyncio
    async def test_run_applies_score_threshold(self):
        """BaseCollector.run() should filter by score_threshold."""
        config = {"score_threshold": 200, "lookback_hours": 24, "max_items": 10}
        with patch.dict(os.environ, {"PRODUCTHUNT_API_TOKEN": "fake-token"}):
            collector = ProductHuntCollector(config)

        nodes = [
            _sample_node(name="High Score", votes=500),
            _sample_node(name="Low Score", votes=50),
        ]
        api_response = {"data": {"posts": {"edges": [{"node": n} for n in nodes]}}}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = api_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await collector.run()

        assert result.success
        assert result.filtered_count == 1
        assert result.items[0].title == "High Score"


# ---------------------------------------------------------------------------
# Integration tests (real API — skipped without token)
# ---------------------------------------------------------------------------

def _has_token() -> bool:
    return bool(os.environ.get("PRODUCTHUNT_API_TOKEN"))


@pytest.mark.skipif(not _has_token(), reason="PRODUCTHUNT_API_TOKEN not set")
@pytest.mark.asyncio
async def test_live_collect_top10():
    """Real API call: fetch today's top 10 products from Product Hunt."""
    config = {"score_threshold": 0, "lookback_hours": 24, "max_items": 10}
    collector = ProductHuntCollector(config)

    result = await collector.run()

    assert result.success, f"Collection failed: {result.error}"
    assert result.filtered_count <= 10
    # Product Hunt may have 0 items early in the day (PST midnight)
    for item in result.items:
        assert item.source == "producthunt"
        assert item.title
        assert item.url.startswith("https://")
        assert item.score >= 0
        assert item.published_at.tzinfo is not None


@pytest.mark.skipif(not _has_token(), reason="PRODUCTHUNT_API_TOKEN not set")
@pytest.mark.asyncio
async def test_live_items_have_votes():
    """Verify live results are ordered by votes (score desc after run())."""
    config = {"score_threshold": 0, "lookback_hours": 24, "max_items": 10}
    collector = ProductHuntCollector(config)
    result = await collector.run()

    if len(result.items) >= 2:
        scores = [item.score for item in result.items]
        assert scores == sorted(scores, reverse=True), "Items should be sorted by score desc"
