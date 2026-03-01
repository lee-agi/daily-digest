"""Tests for RedditCollector pagination and retry."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.reddit_collector import RedditCollector


@pytest.fixture
def collector():
    return RedditCollector({
        "enabled": True,
        "score_threshold": 0,
        "lookback_hours": 24,
        "max_items": 100,
        "subreddits": ["MachineLearning"],
    })


def _make_post(post_id: str, score: int = 100) -> dict:
    """Create a mock Reddit post data dict."""
    return {
        "kind": "t3",
        "data": {
            "title": f"Post {post_id}",
            "permalink": f"/r/MachineLearning/comments/{post_id}/test/",
            "author": "test_user",
            "selftext": "Test content",
            "score": score,
            "created_utc": time.time(),  # recent
            "num_comments": 10,
            "upvote_ratio": 0.95,
        },
    }


def _make_listing(post_ids: list[str], after: str | None = None) -> dict:
    """Create a mock Reddit listing response."""
    return {
        "data": {
            "children": [_make_post(pid) for pid in post_ids],
            "after": after,
        }
    }


class TestRedditPagination:
    """Tests for Reddit cursor-based pagination."""

    def test_public_pagination(self, collector):
        """Public API should paginate using 'after' cursor."""
        page1 = _make_listing(["aaa", "bbb"], after="t3_bbb")
        page2 = _make_listing(["ccc", "ddd"], after=None)  # last page

        call_count = {"n": 0}

        async def mock_request(method, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            idx = call_count["n"]
            call_count["n"] += 1
            resp.json.return_value = page1 if idx == 0 else page2
            return resp

        with patch.dict("os.environ", {}, clear=False):
            with patch("collectors.reddit_collector.httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.request = mock_request
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                items = asyncio.run(collector.collect())

        assert len(items) == 4
        assert call_count["n"] == 2  # 2 pages fetched

    def test_public_stops_when_no_after(self, collector):
        """Public API should stop when 'after' is None."""
        page1 = _make_listing(["aaa", "bbb"], after=None)

        async def mock_request(method, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = page1
            return resp

        with patch.dict("os.environ", {}, clear=False):
            with patch("collectors.reddit_collector.httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.request = mock_request
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                items = asyncio.run(collector.collect())

        assert len(items) == 2
