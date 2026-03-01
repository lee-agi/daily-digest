"""Tests for GitHubCollector pagination and retry."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.github_collector import GitHubCollector


@pytest.fixture
def collector():
    return GitHubCollector({
        "enabled": True,
        "score_threshold": 0,
        "lookback_hours": 24,
        "max_items": 100,
    })


def _make_repo(repo_id: int, stars: int = 10) -> dict:
    """Create a mock GitHub repo dict."""
    return {
        "full_name": f"user/repo-{repo_id}",
        "html_url": f"https://github.com/user/repo-{repo_id}",
        "description": f"Test repo {repo_id}",
        "owner": {"login": "user"},
        "pushed_at": "2026-02-28T10:00:00Z",
        "stargazers_count": stars,
        "forks_count": 1,
        "topics": ["test"],
        "language": "Python",
    }


def _make_search_response(repo_ids: list[int]) -> dict:
    """Create a mock GitHub search response."""
    return {
        "total_count": len(repo_ids) * 3,  # pretend there are more
        "items": [_make_repo(rid) for rid in repo_ids],
    }


class TestGitHubPagination:
    """Tests for GitHub search pagination."""

    def test_trending_multiple_pages(self, collector):
        """Trending search should fetch multiple pages."""
        page1 = _make_search_response(list(range(1, 31)))  # 30 items
        page2 = _make_search_response(list(range(31, 51)))  # 20 items (< 30 → last)

        call_count = {"n": 0}

        async def mock_request(method, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            idx = call_count["n"]
            call_count["n"] += 1
            resp.json.return_value = page1 if idx == 0 else page2
            return resp

        with patch.dict("os.environ", {"GITHUB_TOKEN": ""}, clear=False):
            with patch("collectors.github_collector.httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.request = mock_request
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                items = asyncio.run(collector.collect())

        assert len(items) == 50
        assert call_count["n"] == 2  # 2 pages

    def test_trending_stops_at_max_items(self):
        """Trending search should stop when max_items reached."""
        collector = GitHubCollector({
            "enabled": True,
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 25,
        })
        page1 = _make_search_response(list(range(1, 31)))  # 30 items >= max_items(25)

        call_count = {"n": 0}

        async def mock_request(method, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            call_count["n"] += 1
            resp.json.return_value = page1
            return resp

        with patch.dict("os.environ", {"GITHUB_TOKEN": ""}, clear=False):
            with patch("collectors.github_collector.httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.request = mock_request
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                items = asyncio.run(collector.collect())

        # 30 items from page1 >= max_items(25), stop after page 1
        assert len(items) == 30
        assert call_count["n"] == 1
