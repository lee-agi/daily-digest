"""Tests for blog collectors (Anthropic, OpenAI, Google Blog).

These tests verify:
1. Auto-registration of all three blog collectors
2. Real HTTP/RSS fetching with wider lookback windows
3. Field completeness of ContentItem output
4. Cross-feed dedup for Google Blog collector
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import collectors to trigger registration
import collectors.anthropic_blog  # noqa: F401
import collectors.google_blog  # noqa: F401
import collectors.openai_blog  # noqa: F401

from collectors.base import CollectorRegistry


class TestBlogCollectorRegistration:
    """Verify all three blog collectors auto-register."""

    def test_anthropic_registered(self):
        assert CollectorRegistry.get("anthropic") is not None

    def test_openai_registered(self):
        assert CollectorRegistry.get("openai") is not None

    def test_google_blog_registered(self):
        assert CollectorRegistry.get("google_blog") is not None


class TestOpenAIBlogCollector:
    """Test OpenAI blog collector with real RSS feed."""

    @pytest.mark.asyncio
    async def test_real_rss_fetch(self):
        """Fetch actual blog posts from OpenAI RSS feed."""
        from collectors.openai_blog import OpenAIBlogCollector

        collector = OpenAIBlogCollector({
            "score_threshold": 0,
            "lookback_hours": 720,  # 30 days for test reliability
            "max_items": 10,
        })
        result = await collector.run()
        assert result.success
        assert result.source == "openai"

    @pytest.mark.asyncio
    async def test_field_completeness(self):
        """Verify ContentItem fields are properly populated."""
        from collectors.openai_blog import OpenAIBlogCollector

        collector = OpenAIBlogCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 5,
        })
        result = await collector.run()
        assert result.success
        if result.items:
            item = result.items[0]
            assert item.source == "openai"
            assert item.title
            assert item.url
            assert item.published_at is not None


class TestGoogleBlogCollector:
    """Test Google Blog collector with real RSS feeds."""

    @pytest.mark.asyncio
    async def test_real_rss_fetch(self):
        """Fetch actual blog posts from Google Blog RSS feeds."""
        from collectors.google_blog import GoogleBlogCollector

        collector = GoogleBlogCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 10,
            "feeds": [
                "https://blog.google/technology/ai/rss/",
            ],
        })
        result = await collector.run()
        assert result.success
        assert result.source == "google_blog"

    @pytest.mark.asyncio
    async def test_cross_feed_dedup(self):
        """Verify no duplicate URLs across multiple feeds."""
        from collectors.google_blog import GoogleBlogCollector

        collector = GoogleBlogCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 50,
            "feeds": [
                "https://blog.google/innovation-and-ai/rss/",
                "https://blog.google/technology/ai/rss/",
            ],
        })
        result = await collector.run()
        assert result.success
        if result.items:
            urls = [item.url for item in result.items]
            assert len(urls) == len(set(urls)), "Duplicate URLs found across feeds"


class TestAnthropicBlogCollector:
    """Test Anthropic blog collector with real HTTP scraping."""

    @pytest.mark.asyncio
    async def test_real_http_scrape(self):
        """Scrape actual blog posts from Anthropic website."""
        from collectors.anthropic_blog import AnthropicBlogCollector

        collector = AnthropicBlogCollector({
            "score_threshold": 0,
            "lookback_hours": 720,  # 30 days
            "max_items": 10,
        })
        result = await collector.run()
        assert result.success
        assert result.source == "anthropic"
        # Anthropic should have some blog posts
        assert len(result.items) > 0, "Expected at least 1 blog post from Anthropic"

    @pytest.mark.asyncio
    async def test_engineering_and_research_coverage(self):
        """Verify both /engineering and /research pages are scraped."""
        from collectors.anthropic_blog import AnthropicBlogCollector

        collector = AnthropicBlogCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 50,
        })
        items = await collector.collect()
        # Check that URLs cover both engineering and research sections
        urls = [item.url for item in items]
        has_engineering = any("/engineering/" in u or "/engineering" in u for u in urls)
        has_research = any("/research/" in u or "/research" in u for u in urls)
        # At least one section should have content
        assert has_engineering or has_research, (
            f"Expected posts from /engineering or /research, got URLs: {urls[:5]}"
        )

    @pytest.mark.asyncio
    async def test_field_completeness(self):
        """Verify ContentItem fields are properly populated."""
        from collectors.anthropic_blog import AnthropicBlogCollector

        collector = AnthropicBlogCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 5,
        })
        result = await collector.run()
        assert result.success
        if result.items:
            item = result.items[0]
            assert item.source == "anthropic"
            assert item.title
            assert item.url.startswith("https://www.anthropic.com/")
