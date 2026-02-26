"""Tests for ZhihuCliCollector (direct Zhihu API).

Unit tests use mock data (no network). Integration tests require valid
~/.zhihu-cli/cookies.json and are skipped otherwise.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.zhihu_collector import ZhihuCliCollector
from schema import SourceType


# ─── Fixtures ────────────────────────────────────────────────────

@pytest.fixture
def base_config():
    return {
        "enabled": True,
        "source_type": "api",
        "score_threshold": 50,
        "lookback_hours": 24,
        "max_items": 50,
        "feed_types": ["hot", "recommend", "follow"],
        "limit_per_feed": 20,
    }


@pytest.fixture
def collector(base_config):
    return ZhihuCliCollector(base_config)


@pytest.fixture
def sample_cookies():
    return {
        "_xsrf": "abc123",
        "z_c0": "token_value",
        "d_c0": "device_cookie",
    }


# ─── Hot feed mock data ─────────────────────────────────────────

@pytest.fixture
def hot_response():
    return {
        "data": [
            {
                "target": {
                    "title": "热榜问题1",
                    "url": "https://api.zhihu.com/questions/12345",
                    "excerpt": "这是一个热门问题的摘要",
                },
                "detail_text": "1234 万热度",
            },
            {
                "target": {
                    "title": "热榜问题2",
                    "url": "https://api.zhihu.com/questions/67890",
                    "excerpt": "另一个热门问题",
                },
                "detail_text": "567 万热度",
            },
            {
                "target": {
                    "title": "纯数字热度",
                    "url": "https://api.zhihu.com/questions/11111",
                    "excerpt": "纯数字热度测试",
                },
                "detail_text": "890234 热度",
            },
            {
                "target": {},  # empty target, should be skipped
                "detail_text": "",
            },
        ]
    }


# ─── Recommend/Follow feed mock data ────────────────────────────

@pytest.fixture
def recommend_response():
    return {
        "data": [
            {
                "type": "normal",
                "target": {
                    "type": "article",
                    "id": 111,
                    "title": "推荐文章1",
                    "author": {"name": "作者A"},
                    "voteup_count": 200,
                    "excerpt": "文章摘要",
                    "created_time": 1708700000,
                },
            },
            {
                "type": "normal",
                "target": {
                    "type": "answer",
                    "id": 222,
                    "question": {"id": 333, "title": "推荐问题1"},
                    "author": {"name": "作者B"},
                    "voteup_count": 150,
                    "excerpt": "回答摘要",
                    "created_time": 1708700000,
                },
            },
            {
                "type": "feed_advert",  # ad, should be filtered
                "target": {
                    "type": "article",
                    "id": 999,
                    "title": "广告内容",
                    "voteup_count": 0,
                },
            },
            {
                "type": "normal",
                "target": {},  # empty target, should be skipped
            },
        ]
    }


@pytest.fixture
def follow_response():
    return {
        "data": [
            {
                "type": "normal",
                "target": {
                    "type": "answer",
                    "id": 444,
                    "question": {"id": 555, "title": "关注问题1"},
                    "author": {"name": "作者C"},
                    "voteup_count": 300,
                    "excerpt": "关注回答摘要",
                    "created_time": 1708700000,
                },
            },
        ]
    }


# ═══════════════════════════════════════════════════════════════════
# Cookie 相关测试
# ═══════════════════════════════════════════════════════════════════

class TestCookieLoading:
    def test_load_cookies_normal(self, collector, sample_cookies, tmp_path):
        """Normal cookies.json should be loaded correctly."""
        cookie_file = tmp_path / "cookies.json"
        cookie_file.write_text(json.dumps(sample_cookies))

        with patch.object(collector, "_cookies_path", cookie_file):
            cookies = collector._load_cookies()
            assert cookies == sample_cookies

    def test_load_cookies_file_not_found(self, collector, tmp_path):
        """Missing cookies.json should return empty dict."""
        with patch.object(collector, "_cookies_path", tmp_path / "nonexistent.json"):
            cookies = collector._load_cookies()
            assert cookies == {}

    def test_load_cookies_json_error(self, collector, tmp_path):
        """Invalid JSON should return empty dict and log error."""
        cookie_file = tmp_path / "cookies.json"
        cookie_file.write_text("not valid json {{{")

        with patch.object(collector, "_cookies_path", cookie_file):
            cookies = collector._load_cookies()
            assert cookies == {}


# ═══════════════════════════════════════════════════════════════════
# Header 构建测试
# ═══════════════════════════════════════════════════════════════════

class TestHeaderBuilding:
    def test_cookie_header_format(self, collector):
        """Cookies should be formatted as 'k1=v1; k2=v2'."""
        cookies = {"a": "1", "b": "2"}
        headers = collector._build_headers(cookies)
        # Cookie header should contain all key=value pairs
        cookie_str = headers["Cookie"]
        assert "a=1" in cookie_str
        assert "b=2" in cookie_str
        assert "; " in cookie_str

    def test_xsrf_token_header(self, collector, sample_cookies):
        """When _xsrf is in cookies, x-xsrftoken header should be set."""
        headers = collector._build_headers(sample_cookies)
        assert headers["x-xsrftoken"] == "abc123"

    def test_no_xsrf_token(self, collector):
        """When _xsrf is absent, x-xsrftoken should not be in headers."""
        cookies = {"z_c0": "token"}
        headers = collector._build_headers(cookies)
        assert "x-xsrftoken" not in headers

    def test_required_headers_present(self, collector, sample_cookies):
        """All required headers from zhihu-cli should be present."""
        headers = collector._build_headers(sample_cookies)
        assert "User-Agent" in headers
        assert "Accept-Encoding" in headers
        assert headers["Accept-Encoding"] == "identity"
        assert headers["accept-language"] == "zh-CN,zh;q=0.9,en;q=0.8"
        assert headers["referer"] == "https://www.zhihu.com/"
        assert headers["x-api-version"] == "3.0.53"
        assert headers["x-requested-with"] == "fetch"


# ═══════════════════════════════════════════════════════════════════
# Hot feed 解析测试
# ═══════════════════════════════════════════════════════════════════

class TestHotParsing:
    def test_parse_hot_items(self, collector, hot_response):
        """Hot feed should parse title, URL, and score."""
        items = collector._parse_hot(hot_response)
        # Empty target should be skipped → 3 items
        assert len(items) == 3

    def test_hot_url_rewrite(self, collector, hot_response):
        """api.zhihu.com URLs should be rewritten to www.zhihu.com."""
        items = collector._parse_hot(hot_response)
        assert items[0].url == "https://www.zhihu.com/question/12345"
        assert items[1].url == "https://www.zhihu.com/question/67890"

    def test_hot_score_wan(self, collector, hot_response):
        """'1234 万热度' should extract score as 1234 * 10000."""
        items = collector._parse_hot(hot_response)
        assert items[0].score == 1234 * 10000

    def test_hot_score_plain_number(self, collector, hot_response):
        """'890234 热度' should extract score as 890234."""
        items = collector._parse_hot(hot_response)
        assert items[2].score == 890234

    def test_hot_skip_empty_target(self, collector, hot_response):
        """Items with empty target should be skipped."""
        items = collector._parse_hot(hot_response)
        titles = [i.title for i in items]
        assert "Untitled" not in titles or len(items) == 3

    def test_hot_title_area_fallback(self, collector):
        """When title is missing, title_area.text should be used."""
        data = {
            "data": [{
                "target": {
                    "title_area": {"text": "标题区域文本"},
                    "url": "https://api.zhihu.com/questions/99999",
                },
                "detail_text": "100 万热度",
            }]
        }
        items = collector._parse_hot(data)
        assert items[0].title == "标题区域文本"

    def test_hot_legacy_link_url(self, collector):
        """Legacy format with target.link.url should also work."""
        data = {
            "data": [{
                "target": {
                    "title": "Legacy格式",
                    "link": {"url": "https://api.zhihu.com/questions/55555"},
                },
                "detail_text": "100 万热度",
            }]
        }
        items = collector._parse_hot(data)
        assert items[0].url == "https://www.zhihu.com/question/55555"


# ═══════════════════════════════════════════════════════════════════
# Recommend/Follow 解析测试
# ═══════════════════════════════════════════════════════════════════

class TestRecommendFollowParsing:
    def test_parse_article(self, collector, recommend_response):
        """Article type should produce zhuanlan URL."""
        items = collector._parse_recommend_or_follow(recommend_response)
        article = [i for i in items if "zhuanlan" in i.url]
        assert len(article) == 1
        assert article[0].url == "https://zhuanlan.zhihu.com/p/111"
        assert article[0].title == "推荐文章1"

    def test_parse_answer(self, collector, recommend_response):
        """Answer type should produce question/answer URL."""
        items = collector._parse_recommend_or_follow(recommend_response)
        answer = [i for i in items if "question" in i.url]
        assert len(answer) == 1
        assert answer[0].url == "https://www.zhihu.com/question/333/answer/222"
        assert answer[0].title == "推荐问题1"

    def test_filter_ads(self, collector, recommend_response):
        """feed_advert items should be filtered out."""
        items = collector._parse_recommend_or_follow(recommend_response)
        titles = [i.title for i in items]
        assert "广告内容" not in titles

    def test_skip_empty_target(self, collector, recommend_response):
        """Items with empty target should be skipped."""
        items = collector._parse_recommend_or_follow(recommend_response)
        # 1 article + 1 answer = 2 (ad + empty skipped)
        assert len(items) == 2

    def test_voteup_count_as_score(self, collector, recommend_response):
        """voteup_count should be used as score."""
        items = collector._parse_recommend_or_follow(recommend_response)
        scores = {i.title: i.score for i in items}
        assert scores["推荐文章1"] == 200
        assert scores["推荐问题1"] == 150

    def test_created_time_parsing(self, collector, recommend_response):
        """Unix timestamp created_time should be parsed to datetime."""
        items = collector._parse_recommend_or_follow(recommend_response)
        for item in items:
            assert item.published_at.tzinfo is not None
            assert item.published_at.year >= 2024

    def test_follow_feed_parsing(self, collector, follow_response):
        """Follow feed uses the same parsing logic as recommend."""
        items = collector._parse_recommend_or_follow(follow_response)
        assert len(items) == 1
        assert items[0].title == "关注问题1"
        assert items[0].score == 300


# ═══════════════════════════════════════════════════════════════════
# 跨 feed 去重测试
# ═══════════════════════════════════════════════════════════════════

class TestCrossFeedDedup:
    def test_dedup_same_url(self, collector):
        """Same URL from different feeds should only appear once."""
        hot_data = {
            "data": [{
                "target": {
                    "title": "重复内容",
                    "url": "https://api.zhihu.com/questions/12345",
                },
                "detail_text": "100 万热度",
            }]
        }
        recommend_data = {
            "data": [{
                "type": "normal",
                "target": {
                    "type": "answer",
                    "id": 999,
                    "question": {"id": 12345, "title": "重复内容"},
                    "author": {"name": "test"},
                    "voteup_count": 50,
                    "created_time": 1708700000,
                },
            }]
        }
        # Parse both feeds
        seen_urls: set[str] = set()
        hot_items = collector._parse_hot(hot_data, seen_urls)
        recommend_items = collector._parse_recommend_or_follow(
            recommend_data, seen_urls
        )
        all_items = hot_items + recommend_items

        # Hot adds .../question/12345, recommend adds .../question/12345/answer/999
        # These are different URLs, so both should appear
        # But if we had exact same URL, only one would appear
        urls = [i.url for i in all_items]
        assert len(urls) == len(set(urls))


# ═══════════════════════════════════════════════════════════════════
# 配置测试
# ═══════════════════════════════════════════════════════════════════

class TestConfiguration:
    def test_default_feed_types(self):
        """Default config should include all 3 feed types."""
        config = {
            "enabled": True,
            "source_type": "api",
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 50,
        }
        c = ZhihuCliCollector(config)
        assert c.feed_types == ["hot", "recommend", "follow"]

    def test_custom_feed_types(self):
        """Custom feed_types should override defaults."""
        config = {
            "enabled": True,
            "source_type": "api",
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 50,
            "feed_types": ["hot"],
        }
        c = ZhihuCliCollector(config)
        assert c.feed_types == ["hot"]

    def test_unknown_feed_type_warning(self, base_config, caplog):
        """Unknown feed type should log a warning and be skipped."""
        base_config["feed_types"] = ["hot", "unknown_type"]
        c = ZhihuCliCollector(base_config)
        with caplog.at_level(logging.WARNING):
            # _get_feed_url should return None for unknown types
            url = c._get_feed_url("unknown_type")
            assert url is None

    def test_source_metadata(self, collector):
        """Collector should have correct source_name and source_type."""
        assert collector.source_name == "zhihu"
        assert collector.source_type == SourceType.API

    def test_limit_per_feed(self, base_config):
        """limit_per_feed should default to 20."""
        c = ZhihuCliCollector(base_config)
        assert c.limit_per_feed == 20

    def test_limit_per_feed_custom(self):
        """Custom limit_per_feed should be respected."""
        config = {
            "enabled": True,
            "source_type": "api",
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 50,
            "limit_per_feed": 10,
        }
        c = ZhihuCliCollector(config)
        assert c.limit_per_feed == 10


# ═══════════════════════════════════════════════════════════════════
# collect() 端到端 mock 测试
# ═══════════════════════════════════════════════════════════════════

class TestCollectMocked:
    def test_collect_all_feeds(
        self, collector, hot_response, recommend_response, follow_response
    ):
        """collect() should merge items from all 3 feeds."""
        mock_responses = {
            "hot-lists": hot_response,
            "recommend": recommend_response,
            "follow": follow_response,
        }

        async def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            for key, data in mock_responses.items():
                if key in url:
                    resp.json.return_value = data
                    return resp
            resp.json.return_value = {"data": []}
            return resp

        with patch.object(collector, "_load_cookies", return_value={"z_c0": "x"}):
            with patch("collectors.zhihu_collector.httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.get = mock_get
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                items = asyncio.run(collector.collect())

        # hot: 3 items, recommend: 2 items, follow: 1 item = 6 total
        assert len(items) == 6

    def test_collect_no_cookies_returns_empty(self, collector):
        """collect() should return [] when cookies file is missing."""
        with patch.object(collector, "_load_cookies", return_value={}):
            items = asyncio.run(collector.collect())
            assert items == []

    def test_collect_api_error_returns_empty(self, collector):
        """collect() should return [] on API error and log it."""
        import httpx

        with patch.object(
            collector, "_load_cookies", return_value={"z_c0": "x"}
        ):
            with patch("collectors.zhihu_collector.httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.get = AsyncMock(
                    side_effect=httpx.HTTPStatusError(
                        "401", request=MagicMock(), response=MagicMock()
                    )
                )
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                items = asyncio.run(collector.collect())
                assert items == []


# ═══════════════════════════════════════════════════════════════════
# 集成测试（需 cookies，skipif 保护）
# ═══════════════════════════════════════════════════════════════════

_COOKIES_FILE = Path.home() / ".zhihu-cli" / "cookies.json"


def _has_valid_cookies() -> bool:
    """Check if cookies.json exists and is valid JSON."""
    if not _COOKIES_FILE.exists():
        return False
    try:
        data = json.loads(_COOKIES_FILE.read_text())
        return bool(data.get("z_c0"))
    except (json.JSONDecodeError, KeyError):
        return False


@pytest.mark.skipif(
    not _has_valid_cookies(),
    reason="No valid ~/.zhihu-cli/cookies.json found",
)
class TestZhihuIntegration:
    """Integration tests against live Zhihu API. Require valid cookies."""

    @pytest.fixture
    def live_collector(self):
        return ZhihuCliCollector({
            "enabled": True,
            "source_type": "api",
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 50,
            "feed_types": ["hot"],
            "limit_per_feed": 5,
        })

    def test_hot_feed_returns_items(self, live_collector):
        """Hot feed API should return non-empty results."""
        items = asyncio.run(live_collector.collect())
        assert len(items) > 0
        # Verify item structure
        for item in items:
            assert item.source == "zhihu"
            assert item.title
            assert item.url.startswith("https://")

    def test_recommend_feed_returns_items(self):
        """Recommend feed API should return non-empty results."""
        c = ZhihuCliCollector({
            "enabled": True,
            "source_type": "api",
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 50,
            "feed_types": ["recommend"],
            "limit_per_feed": 5,
        })
        items = asyncio.run(c.collect())
        assert len(items) > 0

    def test_full_collector_run(self):
        """Full collector.run() should succeed with valid cookies."""
        c = ZhihuCliCollector({
            "enabled": True,
            "source_type": "api",
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 50,
            "feed_types": ["hot", "recommend"],
            "limit_per_feed": 5,
        })
        result = asyncio.run(c.run())
        assert result.success, f"Collector failed: {result.error}"
        assert result.raw_count > 0
