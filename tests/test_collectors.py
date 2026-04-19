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
import collectors.ccf_bestpaper  # noqa: F401
import collectors.zhihu_collector  # noqa: F401
import collectors.cn_tech_blog  # noqa: F401
import collectors.producthunt_collector  # noqa: F401

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
            "ccf_bestpaper", "cn_tech_blog", "baoyu_blog",
            "producthunt",
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
                "xiaoyuzhou": {"enabled": True, "feeds": []},
                "anthropic": {"enabled": True, "score_threshold": 0},
                "openai": {"enabled": True, "score_threshold": 0},
                "google_blog": {"enabled": True, "score_threshold": 0, "feeds": []},
                "ccf_bestpaper": {"enabled": True, "score_threshold": 0},
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

    @pytest.mark.integration
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

    @pytest.mark.integration
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

    @pytest.mark.integration
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


class TestXiaoyuzhouCollector:
    """Test XiaoyuzhouCollector (direct RSS via xyzfm.space)."""

    def test_no_feeds_returns_empty_sync(self):
        """Collector initialised with no feeds should store empty list."""
        from collectors.rsshub_collector import XiaoyuzhouCollector
        collector = XiaoyuzhouCollector({"feeds": []})
        assert collector.feeds == []

    def test_feeds_from_config(self):
        """Feeds list should be read directly from config."""
        from collectors.rsshub_collector import XiaoyuzhouCollector
        feeds = [
            "https://feed.xyzfm.space/qw7x9eum9utp",
            "https://feed.xyzfm.space/tmaapfx9v3hl",
        ]
        collector = XiaoyuzhouCollector({"feeds": feeds})
        assert collector.feeds == feeds

    @pytest.mark.asyncio
    async def test_no_feeds_returns_empty(self):
        """collect() with empty feeds list returns []."""
        from collectors.rsshub_collector import XiaoyuzhouCollector
        collector = XiaoyuzhouCollector({
            "feeds": [],
            "score_threshold": 0,
            "lookback_hours": 168,
            "max_items": 20,
        })
        items = await collector.collect()
        assert items == []

    def test_parse_entry_podcast_tag(self):
        """_parse_entry should tag item as 'podcast' and set source to 'xiaoyuzhou'."""
        from collectors.rsshub_collector import XiaoyuzhouCollector
        collector = XiaoyuzhouCollector({
            "feeds": [],
            "score_threshold": 0,
            "lookback_hours": 168,
        })
        fake_entry = {
            "title": "Episode 42",
            "link": "https://www.xiaoyuzhoufm.com/episode/abc123",
            "summary": "A great episode about AI",
            "published": "Mon, 24 Feb 2026 10:00:00 +0000",
            "itunes_duration": "45:30",
        }
        item = collector._parse_entry(fake_entry, "Test Podcast")
        assert item is not None
        assert item.source == "xiaoyuzhou"
        assert "podcast" in item.tags
        assert item.extra["podcast"] == "Test Podcast"
        assert item.extra["duration"] == "45:30"
        assert "[Test Podcast]" in item.title
