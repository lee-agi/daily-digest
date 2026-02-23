"""Tests for collector implementations.

These tests verify:
1. Auto-registration mechanism
2. Collector instantiation from config
3. Real API calls where possible (GitHub, HuggingFace)
4. RSS parsing logic
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import all collectors to trigger registration
import collectors.rsshub_collector  # noqa: F401
import collectors.github_collector  # noqa: F401
import collectors.reddit_collector  # noqa: F401
import collectors.youtube_collector  # noqa: F401
import collectors.huggingface_papers  # noqa: F401
import collectors.coolpaper_collector  # noqa: F401
import collectors.x_twitter  # noqa: F401
import collectors.weread_collector  # noqa: F401
import collectors.apple_podcast  # noqa: F401
import collectors.anthropic_blog  # noqa: F401
import collectors.openai_blog  # noqa: F401
import collectors.google_blog  # noqa: F401

from collectors.base import CollectorRegistry


class TestCollectorRegistration:
    """Verify all collectors are properly registered."""

    def test_all_sources_registered(self):
        expected = {
            "zhihu", "jike", "xiaoyuzhou",
            "github", "reddit", "youtube",
            "huggingface", "coolpaper",
            "x_twitter", "weread", "apple_podcast",
            "anthropic", "openai", "google_blog",
        }
        registered = set(CollectorRegistry.all_names())
        assert expected.issubset(registered), f"Missing: {expected - registered}"

    def test_create_all_from_full_config(self):
        """Test that all enabled sources create collector instances."""
        config = {
            "sources": {
                "github": {"enabled": True, "score_threshold": 5},
                "reddit": {"enabled": True, "score_threshold": 50, "subreddits": ["LocalLLaMA"]},
                "youtube": {"enabled": True, "channels": []},
                "zhihu": {"enabled": True, "rsshub_base": "http://localhost:1200", "route": "/zhihu/hot"},
                "huggingface": {"enabled": True, "score_threshold": 20},
                "coolpaper": {"enabled": True, "score_threshold": 20},
                "x_twitter": {"enabled": True, "lists": {}},
                "weread": {"enabled": False},
                "apple_podcast": {"enabled": True, "feeds": []},
                "jike": {"enabled": True, "rsshub_base": "http://localhost:1200", "user_ids": []},
                "xiaoyuzhou": {"enabled": True, "rsshub_base": "http://localhost:1200", "podcast_ids": []},
                "anthropic": {"enabled": True, "score_threshold": 0},
                "openai": {"enabled": True, "score_threshold": 0},
                "google_blog": {"enabled": True, "score_threshold": 0, "feeds": []},
            }
        }
        instances = CollectorRegistry.create_all(config)
        names = {c.source_name for c in instances}
        assert "weread" not in names  # Disabled
        assert "github" in names
        assert "huggingface" in names

    def test_score_threshold_from_config(self):
        config = {"score_threshold": 42, "subreddits": ["test"]}
        from collectors.reddit_collector import RedditCollector
        collector = RedditCollector(config)
        assert collector.score_threshold == 42

    def test_lookback_hours_from_config(self):
        config = {"lookback_hours": 12, "score_threshold": 0}
        from collectors.github_collector import GitHubCollector
        collector = GitHubCollector(config)
        assert collector.lookback_hours == 12


class TestHuggingFaceCollector:
    """Test HuggingFace collector with real API call."""

    @pytest.mark.asyncio
    async def test_real_api_call(self):
        """Fetch actual daily papers from HuggingFace API."""
        from collectors.huggingface_papers import HuggingFacePapersCollector
        collector = HuggingFacePapersCollector({
            "score_threshold": 0,
            "lookback_hours": 72,  # Wider window for test reliability
            "max_items": 5,
        })
        result = await collector.run()
        assert result.success
        # HF Daily Papers API should return some data
        if result.items:
            item = result.items[0]
            assert item.source == "huggingface"
            assert item.title
            assert item.url.startswith("https://huggingface.co/papers/")


class TestGitHubCollector:
    """Test GitHub collector with real API call (no auth required for search)."""

    @pytest.mark.asyncio
    async def test_trending_no_auth(self):
        """Fetch trending repos without auth token."""
        from collectors.github_collector import GitHubCollector
        collector = GitHubCollector({
            "score_threshold": 0,
            "lookback_hours": 48,
            "max_items": 5,
        })
        # Only test trending (no starred since no auth)
        import httpx
        async with httpx.AsyncClient(timeout=30, headers=collector._headers()) as client:
            items = await collector._fetch_trending(client)
        assert isinstance(items, list)
        if items:
            assert items[0].source == "github"
            assert items[0].url.startswith("https://github.com/")


class TestRedditCollector:
    """Test Reddit collector with public JSON API (no OAuth required)."""

    @pytest.mark.asyncio
    async def test_public_api(self):
        """Fetch hot posts from a subreddit via public API."""
        from collectors.reddit_collector import RedditCollector
        collector = RedditCollector({
            "score_threshold": 0,
            "lookback_hours": 48,
            "max_items": 5,
            "subreddits": ["LocalLLaMA"],
        })
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            items = await collector._collect_public(client)
        assert isinstance(items, list)
        if items:
            assert items[0].source == "reddit"
            assert "LocalLLaMA" in items[0].tags
