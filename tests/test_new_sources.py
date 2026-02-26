"""Tests for cn_tech_blog and baoyu_blog collectors (real API calls)."""

from __future__ import annotations

import asyncio

import httpx
import pytest


def _github_rss_available() -> bool:
    """Check if GitHub raw RSS is reachable."""
    try:
        resp = httpx.get(
            "https://raw.githubusercontent.com/osnsyc/Wechat-Scholar/main/channels/gh_dbc0a5474692.xml",
            timeout=10,
            follow_redirects=True,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _rsshub_available() -> bool:
    """Check if RSSHub is running on localhost."""
    try:
        transport = httpx.HTTPTransport()
        with httpx.Client(timeout=5, transport=transport) as client:
            resp = client.get("http://localhost:1200/")
            return resp.status_code == 200
    except Exception:
        return False


class TestCnTechBlogCollector:
    """Test CnTechBlogCollector with real RSS feeds."""

    @pytest.fixture
    def config(self):
        return {
            "enabled": True,
            "source_type": "rss",
            "score_threshold": 0,
            "lookback_hours": 168,
            "max_items": 30,
            "feeds": [
                "https://raw.githubusercontent.com/osnsyc/Wechat-Scholar/main/channels/gh_dbc0a5474692.xml",
                "https://raw.githubusercontent.com/osnsyc/Wechat-Scholar/main/channels/gh_114e76fd6e5d.xml",
                "https://justlovemaki.github.io/CloudFlare-AI-Insight-Daily/rss.xml",
            ],
        }

    def test_cn_tech_blog_registration(self):
        """CnTechBlogCollector auto-registers as 'cn_tech_blog'."""
        from collectors.cn_tech_blog import CnTechBlogCollector
        from collectors.base import CollectorRegistry

        cls = CollectorRegistry.get("cn_tech_blog")
        assert cls is CnTechBlogCollector

    def test_cn_tech_blog_init(self, config):
        """Collector correctly reads feeds from config."""
        from collectors.cn_tech_blog import CnTechBlogCollector

        collector = CnTechBlogCollector(config)
        assert len(collector.feeds) == 3
        assert collector.source_name == "cn_tech_blog"

    def test_cn_tech_blog_empty_feeds(self):
        """Returns empty list when no feeds configured."""
        from collectors.cn_tech_blog import CnTechBlogCollector

        collector = CnTechBlogCollector({"feeds": []})
        items = asyncio.run(collector.collect())
        assert items == []

    @pytest.mark.skipif(
        not _github_rss_available(),
        reason="GitHub raw content not reachable",
    )
    def test_cn_tech_blog_collects_jiqizhixin(self):
        """Collects items from 机器之心 RSS (real API)."""
        from collectors.cn_tech_blog import CnTechBlogCollector

        config = {
            "score_threshold": 0,
            "lookback_hours": 720,  # 30 days for test reliability
            "max_items": 50,
            "feeds": [
                "https://raw.githubusercontent.com/osnsyc/Wechat-Scholar/main/channels/gh_dbc0a5474692.xml",
            ],
        }
        collector = CnTechBlogCollector(config)
        items = asyncio.run(collector.collect())
        assert len(items) > 0, "Expected 机器之心 RSS to return items"
        for item in items:
            assert item.source == "cn_tech_blog"
            assert item.title
            assert item.url
            assert item.language == "zh"

    @pytest.mark.skipif(
        not _github_rss_available(),
        reason="GitHub raw content not reachable",
    )
    def test_cn_tech_blog_collects_all_feeds(self):
        """Collects from all 3 feeds with cross-feed dedup (real API)."""
        from collectors.cn_tech_blog import CnTechBlogCollector

        config = {
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 100,
            "feeds": [
                "https://raw.githubusercontent.com/osnsyc/Wechat-Scholar/main/channels/gh_dbc0a5474692.xml",
                "https://raw.githubusercontent.com/osnsyc/Wechat-Scholar/main/channels/gh_114e76fd6e5d.xml",
                "https://justlovemaki.github.io/CloudFlare-AI-Insight-Daily/rss.xml",
            ],
        }
        collector = CnTechBlogCollector(config)
        items = asyncio.run(collector.collect())
        assert len(items) > 0, "Expected combined feeds to return items"
        # Check dedup: no duplicate URLs
        urls = [item.url for item in items]
        assert len(urls) == len(set(urls)), "Duplicate URLs found"

    @pytest.mark.skipif(
        not _github_rss_available(),
        reason="GitHub raw content not reachable",
    )
    def test_cn_tech_blog_run_integration(self):
        """Full run() with score filter and max_items (real API)."""
        from collectors.cn_tech_blog import CnTechBlogCollector

        config = {
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 5,
            "feeds": [
                "https://raw.githubusercontent.com/osnsyc/Wechat-Scholar/main/channels/gh_dbc0a5474692.xml",
            ],
        }
        collector = CnTechBlogCollector(config)
        result = asyncio.run(collector.run())
        assert result.success
        assert result.filtered_count <= 5


class TestBaoyuBlogCollector:
    """Test BaoyuBlogCollector with real RSSHub."""

    @pytest.fixture
    def config(self):
        return {
            "enabled": True,
            "source_type": "rss",
            "score_threshold": 0,
            "lookback_hours": 168,
            "max_items": 20,
            "rsshub_base": "http://localhost:1200",
            "route": "/baoyu/blog",
        }

    def test_baoyu_blog_registration(self):
        """BaoyuBlogCollector auto-registers as 'baoyu_blog'."""
        from collectors.rsshub_collector import BaoyuBlogCollector
        from collectors.base import CollectorRegistry

        cls = CollectorRegistry.get("baoyu_blog")
        assert cls is BaoyuBlogCollector

    def test_baoyu_blog_init(self, config):
        """Collector correctly reads RSSHub config."""
        from collectors.rsshub_collector import BaoyuBlogCollector

        collector = BaoyuBlogCollector(config)
        assert collector.source_name == "baoyu_blog"
        assert collector.rsshub_base == "http://localhost:1200"

    def test_baoyu_blog_build_feed_urls(self, config):
        """Build correct RSSHub feed URL."""
        from collectors.rsshub_collector import BaoyuBlogCollector

        collector = BaoyuBlogCollector(config)
        urls = collector._build_feed_urls()
        assert urls == ["http://localhost:1200/baoyu/blog"]

    @pytest.mark.skipif(
        not _rsshub_available(),
        reason="RSSHub not running on localhost:1200",
    )
    def test_baoyu_blog_collects_items(self, config):
        """Collects items from 宝玉博客 via RSSHub (real API)."""
        from collectors.rsshub_collector import BaoyuBlogCollector

        config["lookback_hours"] = 8760  # 1 year for test reliability
        collector = BaoyuBlogCollector(config)
        items = asyncio.run(collector.collect())
        assert len(items) > 0, "Expected baoyu/blog to return items"
        for item in items:
            assert item.source == "baoyu_blog"
            assert item.title

    @pytest.mark.skipif(
        not _rsshub_available(),
        reason="RSSHub not running on localhost:1200",
    )
    def test_baoyu_blog_run_integration(self, config):
        """Full run() integration (real API)."""
        from collectors.rsshub_collector import BaoyuBlogCollector

        config["lookback_hours"] = 8760
        config["max_items"] = 5
        collector = BaoyuBlogCollector(config)
        result = asyncio.run(collector.run())
        assert result.success
        assert result.source == "baoyu_blog"
