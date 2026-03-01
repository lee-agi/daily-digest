"""Tests for XTwitterCollector.

Unit tests (no network):
  - Auto-registration
  - Missing credentials → empty result + warning
  - _build_item_from_twitterapiio: field mapping, score calculation
  - _build_item_from_twikit: field mapping, score calculation
  - Time window filter (lookback_hours cutoff)
  - TwitterAPI.io primary path: mock httpx, verify ContentItems
  - Fallback to twikit on HTTP 500

Integration tests (real API, skipped without TWITTER_API_IO_KEY):
  - Live collect returns ContentItems with required fields
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import collectors.x_twitter  # noqa: F401 — triggers registration
from collectors.base import CollectorRegistry
from collectors.x_twitter import XTwitterCollector
from schema import ContentItem, SourceType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_collector(
    key: str = "fake-key",
    token: str = "",
    ct0: str = "",
    lookback_hours: int = 24,
) -> XTwitterCollector:
    config = {
        "score_threshold": 0,
        "lookback_hours": lookback_hours,
        "max_items": 100,
        "lists": {
            "LLM": "1664234382868250624",
            "Product": "1934043689183256997",
        },
        "api_key_env": "TWITTER_API_IO_KEY",
        "auth_token_env": "X_AUTH_TOKEN",
        "ct0_env": "X_CT0",
    }
    env: dict[str, str] = {}
    if key:
        env["TWITTER_API_IO_KEY"] = key
    if token:
        env["X_AUTH_TOKEN"] = token
    if ct0:
        env["X_CT0"] = ct0
    # Clear the keys we don't want
    base_env = {k: v for k, v in os.environ.items()
                if k not in {"TWITTER_API_IO_KEY", "X_AUTH_TOKEN", "X_CT0"}}
    base_env.update(env)
    with patch.dict(os.environ, base_env, clear=True):
        return XTwitterCollector(config)


def _sample_apiio_tweet(
    tweet_id: str = "1234567890",
    text: str = "Hello world from Twitter",
    screen_name: str = "testuser",
    created_at: str = "",
    like_count: int = 50,
    retweet_count: int = 10,
    reply_count: int = 5,
) -> dict:
    if not created_at:
        # Default: 1 hour ago (within lookback window)
        dt = datetime.now(timezone.utc) - timedelta(hours=1)
        created_at = dt.isoformat()
    return {
        "id": tweet_id,
        "text": text,
        "author": {
            "userName": screen_name,
            "name": "Test User",
        },
        "createdAt": created_at,
        "likeCount": like_count,
        "retweetCount": retweet_count,
        "replyCount": reply_count,
        "bookmarkCount": 3,
    }


def _apiio_response(tweets: list[dict], has_next_page: bool = False) -> dict:
    return {
        "tweets": tweets,
        "has_next_page": has_next_page,
        "next_cursor": "",
    }


# ---------------------------------------------------------------------------
# Unit tests: Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_registered_as_x_twitter(self):
        assert "x_twitter" in CollectorRegistry.all_names()

    def test_registered_class_is_correct(self):
        assert CollectorRegistry.get("x_twitter") is XTwitterCollector

    def test_source_type_is_api(self):
        assert XTwitterCollector.source_type == SourceType.API


# ---------------------------------------------------------------------------
# Unit tests: Missing credentials
# ---------------------------------------------------------------------------

class TestMissingCredentials:
    @pytest.mark.asyncio
    async def test_no_credentials_returns_empty(self):
        config = {
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 100,
            "lists": {"LLM": "1664234382868250624"},
            "api_key_env": "TWITTER_API_IO_KEY",
            "auth_token_env": "X_AUTH_TOKEN",
            "ct0_env": "X_CT0",
        }
        # Ensure all three env vars are absent
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in {"TWITTER_API_IO_KEY", "X_AUTH_TOKEN", "X_CT0"}}
        with patch.dict(os.environ, clean_env, clear=True):
            collector = XTwitterCollector(config)
            items = await collector.collect()
        assert items == []

    @pytest.mark.asyncio
    async def test_no_credentials_logs_warning(self, caplog):
        config = {
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 100,
            "lists": {"LLM": "1664234382868250624"},
            "api_key_env": "TWITTER_API_IO_KEY",
            "auth_token_env": "X_AUTH_TOKEN",
            "ct0_env": "X_CT0",
        }
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in {"TWITTER_API_IO_KEY", "X_AUTH_TOKEN", "X_CT0"}}
        import logging
        with patch.dict(os.environ, clean_env, clear=True):
            collector = XTwitterCollector(config)
            with caplog.at_level(logging.WARNING):
                await collector.collect()
        assert any("No credentials" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_empty_lists_returns_empty(self):
        config = {
            "score_threshold": 0,
            "lookback_hours": 24,
            "max_items": 100,
            "lists": {},
        }
        with patch.dict(os.environ, {"TWITTER_API_IO_KEY": "fake-key"}):
            collector = XTwitterCollector(config)
            items = await collector.collect()
        assert items == []


# ---------------------------------------------------------------------------
# Unit tests: Score calculation
# ---------------------------------------------------------------------------

class TestScoreCalculation:
    def test_score_is_likes_plus_retweets_times_two(self):
        collector = _make_collector()
        tweet = _sample_apiio_tweet(like_count=10, retweet_count=5)
        item = collector._build_item_from_twitterapiio(tweet, "LLM")
        assert item is not None
        assert item.score == 10 + 5 * 2  # 20.0

    def test_score_zero_when_no_engagement(self):
        collector = _make_collector()
        tweet = _sample_apiio_tweet(like_count=0, retweet_count=0)
        item = collector._build_item_from_twitterapiio(tweet, "LLM")
        assert item is not None
        assert item.score == 0.0

    def test_score_is_float(self):
        collector = _make_collector()
        tweet = _sample_apiio_tweet(like_count=100, retweet_count=50)
        item = collector._build_item_from_twitterapiio(tweet, "LLM")
        assert isinstance(item.score, float)


# ---------------------------------------------------------------------------
# Unit tests: Field mapping (TwitterAPI.io)
# ---------------------------------------------------------------------------

class TestBuildItemFromTwitterapiio:
    def setup_method(self):
        self.collector = _make_collector()

    def test_url_format(self):
        tweet = _sample_apiio_tweet(tweet_id="9876", screen_name="alice")
        item = self.collector._build_item_from_twitterapiio(tweet, "LLM")
        assert item is not None
        assert item.url == "https://x.com/alice/status/9876"

    def test_source_fields(self):
        tweet = _sample_apiio_tweet()
        item = self.collector._build_item_from_twitterapiio(tweet, "LLM")
        assert item.source == "x_twitter"
        assert item.source_type == SourceType.API

    def test_author_is_screen_name(self):
        tweet = _sample_apiio_tweet(screen_name="bobsmith")
        item = self.collector._build_item_from_twitterapiio(tweet, "Product")
        assert item.author == "bobsmith"

    def test_title_truncated_to_200(self):
        long_text = "A" * 300
        tweet = _sample_apiio_tweet(text=long_text)
        item = self.collector._build_item_from_twitterapiio(tweet, "LLM")
        assert len(item.title) == 200

    def test_content_is_full_text(self):
        long_text = "B" * 300
        tweet = _sample_apiio_tweet(text=long_text)
        item = self.collector._build_item_from_twitterapiio(tweet, "LLM")
        assert item.content == long_text

    def test_extra_fields(self):
        tweet = _sample_apiio_tweet(
            tweet_id="111", like_count=10, retweet_count=5, reply_count=3
        )
        item = self.collector._build_item_from_twitterapiio(tweet, "BigV")
        assert item.extra["likes"] == 10
        assert item.extra["retweets"] == 5
        assert item.extra["replies"] == 3
        assert item.extra["list"] == "BigV"
        assert item.extra["tweet_id"] == "111"

    def test_missing_id_returns_none(self):
        tweet = _sample_apiio_tweet()
        tweet["id"] = ""
        assert self.collector._build_item_from_twitterapiio(tweet, "LLM") is None

    def test_missing_text_returns_none(self):
        tweet = _sample_apiio_tweet()
        tweet["text"] = ""
        assert self.collector._build_item_from_twitterapiio(tweet, "LLM") is None

    def test_published_at_is_aware_utc(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=2)
        tweet = _sample_apiio_tweet(created_at=dt.isoformat())
        item = self.collector._build_item_from_twitterapiio(tweet, "LLM")
        assert item is not None
        assert item.published_at.tzinfo is not None

    def test_null_like_count_treated_as_zero(self):
        tweet = _sample_apiio_tweet(like_count=0, retweet_count=0)
        tweet["likeCount"] = None
        tweet["retweetCount"] = None
        item = self.collector._build_item_from_twitterapiio(tweet, "LLM")
        assert item is not None
        assert item.score == 0.0

    def test_fallback_screen_name_key(self):
        tweet = _sample_apiio_tweet()
        # Replace userName with screen_name (alternate field name)
        tweet["author"] = {"screen_name": "charlie"}
        item = self.collector._build_item_from_twitterapiio(tweet, "LLM")
        assert item.author == "charlie"


# ---------------------------------------------------------------------------
# Unit tests: Field mapping (twikit)
# ---------------------------------------------------------------------------

class TestBuildItemFromTwikit:
    def setup_method(self):
        self.collector = _make_collector()

    def _mock_tweet(
        self,
        tweet_id: str = "42",
        text: str = "twikit tweet",
        screen_name: str = "twikituser",
        favorite_count: int = 20,
        retweet_count: int = 7,
        reply_count: int = 2,
        hours_ago: float = 1.0,
    ) -> MagicMock:
        tweet = MagicMock()
        tweet.id = tweet_id
        tweet.text = text
        tweet.user = MagicMock()
        tweet.user.screen_name = screen_name
        tweet.favorite_count = favorite_count
        tweet.retweet_count = retweet_count
        tweet.reply_count = reply_count
        dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        tweet.created_at_datetime = dt
        return tweet

    def test_score_calculation(self):
        tweet = self._mock_tweet(favorite_count=20, retweet_count=7)
        item = self.collector._build_item_from_twikit(tweet, "LLM")
        assert item is not None
        assert item.score == 20 + 7 * 2  # 34.0

    def test_url_format(self):
        tweet = self._mock_tweet(tweet_id="555", screen_name="dave")
        item = self.collector._build_item_from_twikit(tweet, "Product")
        assert item.url == "https://x.com/dave/status/555"

    def test_source_fields(self):
        tweet = self._mock_tweet()
        item = self.collector._build_item_from_twikit(tweet, "LLM")
        assert item.source == "x_twitter"
        assert item.source_type == SourceType.API

    def test_extra_fields(self):
        tweet = self._mock_tweet(tweet_id="99", favorite_count=5, retweet_count=3, reply_count=1)
        item = self.collector._build_item_from_twikit(tweet, "BigV")
        assert item.extra["likes"] == 5
        assert item.extra["retweets"] == 3
        assert item.extra["replies"] == 1
        assert item.extra["list"] == "BigV"
        assert item.extra["tweet_id"] == "99"

    def test_missing_text_returns_none(self):
        tweet = self._mock_tweet(text="")
        assert self.collector._build_item_from_twikit(tweet, "LLM") is None

    def test_missing_id_returns_none(self):
        tweet = self._mock_tweet(tweet_id="")
        assert self.collector._build_item_from_twikit(tweet, "LLM") is None


# ---------------------------------------------------------------------------
# Unit tests: Time window filter
# ---------------------------------------------------------------------------

class TestTimeWindowFilter:
    @pytest.mark.asyncio
    async def test_old_tweets_filtered_out(self):
        """Tweets older than lookback_hours should not be included."""
        collector = _make_collector(lookback_hours=24)
        old_time = datetime.now(timezone.utc) - timedelta(hours=48)
        recent_time = datetime.now(timezone.utc) - timedelta(hours=1)

        raw_tweets = [
            _sample_apiio_tweet(tweet_id="1", created_at=old_time.isoformat()),
            _sample_apiio_tweet(tweet_id="2", created_at=recent_time.isoformat()),
        ]
        response_data = _apiio_response(raw_tweets)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = response_data

        clean_env = {k: v for k, v in os.environ.items()
                     if k not in {"TWITTER_API_IO_KEY", "X_AUTH_TOKEN", "X_CT0"}}
        clean_env["TWITTER_API_IO_KEY"] = "fake-key"

        with patch.dict(os.environ, clean_env, clear=True):
            with patch("collectors.x_twitter.httpx.AsyncClient") as mock_cls:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_resp)
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                items = await collector._fetch_via_twitterapiio("LLM", "1234", "fake-key")

        tweet_ids = [i.extra["tweet_id"] for i in items]
        assert "1" not in tweet_ids  # old tweet filtered
        assert "2" in tweet_ids      # recent tweet kept


# ---------------------------------------------------------------------------
# Unit tests: TwitterAPI.io primary path (mocked httpx)
# ---------------------------------------------------------------------------

class TestTwitterapiioPath:
    @pytest.mark.asyncio
    async def test_collect_returns_items(self):
        """Primary path: mock httpx, verify ContentItems returned."""
        collector = _make_collector(key="fake-key")
        tweets = [
            _sample_apiio_tweet(tweet_id=str(i), text=f"Tweet {i}", like_count=10 * i)
            for i in range(1, 4)
        ]
        responses = {
            "LLM": _apiio_response(tweets[:2]),
            "Product": _apiio_response(tweets[2:]),
        }
        call_count = [0]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        def make_response(method, url, **kwargs):
            params = kwargs.get("params", {})
            list_id = params.get("listId", "")
            # Map list_id to list_name
            id_to_name = {"1664234382868250624": "LLM", "1934043689183256997": "Product"}
            name = id_to_name.get(list_id, "LLM")
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = responses[name]
            return resp

        clean_env = {k: v for k, v in os.environ.items()
                     if k not in {"TWITTER_API_IO_KEY", "X_AUTH_TOKEN", "X_CT0"}}
        clean_env["TWITTER_API_IO_KEY"] = "fake-key"

        with patch.dict(os.environ, clean_env, clear=True):
            with patch("collectors.x_twitter.httpx.AsyncClient") as mock_cls:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(side_effect=make_response)
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                items = await collector.collect()

        assert len(items) == 3
        assert all(isinstance(i, ContentItem) for i in items)
        assert all(i.source == "x_twitter" for i in items)
        assert all(i.source_type == SourceType.API for i in items)

    @pytest.mark.asyncio
    async def test_correct_headers_sent(self):
        """Verify X-API-Key header is passed to TwitterAPI.io."""
        collector = _make_collector(key="my-secret-key")
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = _apiio_response([])

        captured_headers = {}

        async def capture_request(method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            return resp

        clean_env = {k: v for k, v in os.environ.items()
                     if k not in {"TWITTER_API_IO_KEY", "X_AUTH_TOKEN", "X_CT0"}}
        clean_env["TWITTER_API_IO_KEY"] = "my-secret-key"

        with patch.dict(os.environ, clean_env, clear=True):
            with patch("collectors.x_twitter.httpx.AsyncClient") as mock_cls:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(side_effect=capture_request)
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                await collector._fetch_via_twitterapiio("LLM", "1234", "my-secret-key")

        assert captured_headers.get("X-API-Key") == "my-secret-key"


# ---------------------------------------------------------------------------
# Unit tests: Fallback to twikit on TwitterAPI.io failure
# ---------------------------------------------------------------------------

class TestFallbackToTwikit:
    @pytest.mark.asyncio
    async def test_fallback_on_http_error(self):
        """When TwitterAPI.io returns 500, should fall back to twikit."""
        collector = _make_collector(
            key="fake-key", token="fake-auth-token", ct0="fake-ct0"
        )

        # Mock twikit tweet
        mock_tweet = MagicMock()
        mock_tweet.id = "twikit-tweet-1"
        mock_tweet.text = "From twikit fallback"
        mock_tweet.user = MagicMock()
        mock_tweet.user.screen_name = "twikituser"
        mock_tweet.favorite_count = 15
        mock_tweet.retweet_count = 3
        mock_tweet.reply_count = 1
        mock_tweet.created_at_datetime = datetime.now(timezone.utc) - timedelta(hours=1)

        mock_twikit_result = [mock_tweet]

        clean_env = {k: v for k, v in os.environ.items()
                     if k not in {"TWITTER_API_IO_KEY", "X_AUTH_TOKEN", "X_CT0"}}
        clean_env.update({
            "TWITTER_API_IO_KEY": "fake-key",
            "X_AUTH_TOKEN": "fake-auth-token",
            "X_CT0": "fake-ct0",
        })

        with patch.dict(os.environ, clean_env, clear=True):
            with patch("collectors.x_twitter.httpx.AsyncClient") as mock_cls:
                # TwitterAPI.io returns 500 → _request_with_retry exhausts retries
                error_resp = MagicMock()
                error_resp.status_code = 500
                error_resp.raise_for_status = MagicMock(
                    side_effect=httpx.HTTPStatusError(
                        "500", request=MagicMock(), response=error_resp,
                    )
                )
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=error_resp)
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                with patch("asyncio.sleep", new_callable=AsyncMock), \
                     patch("twikit.Client") as mock_twikit_cls:
                    mock_twikit_client = MagicMock()
                    mock_twikit_client.set_cookies = MagicMock()
                    mock_twikit_client.get_list_tweets = AsyncMock(
                        return_value=mock_twikit_result
                    )
                    mock_twikit_cls.return_value = mock_twikit_client

                    items = await collector._collect_list(
                        "LLM", "1664234382868250624", "fake-key", "fake-auth-token", "fake-ct0"
                    )

        assert len(items) == 1
        assert items[0].extra["tweet_id"] == "twikit-tweet-1"

    @pytest.mark.asyncio
    async def test_twikit_cookies_set_correctly(self):
        """Verify twikit receives auth_token and ct0 cookies."""
        collector = _make_collector(
            key="", token="real-auth-token", ct0="real-ct0"
        )

        mock_tweet = MagicMock()
        mock_tweet.id = "111"
        mock_tweet.text = "test"
        mock_tweet.user = MagicMock()
        mock_tweet.user.screen_name = "testuser"
        mock_tweet.favorite_count = 5
        mock_tweet.retweet_count = 2
        mock_tweet.reply_count = 0
        mock_tweet.created_at_datetime = datetime.now(timezone.utc) - timedelta(hours=1)

        captured_cookies = {}

        with patch("twikit.Client") as mock_twikit_cls:
            mock_twikit_client = MagicMock()

            def capture_set_cookies(cookies):
                captured_cookies.update(cookies)

            mock_twikit_client.set_cookies = MagicMock(side_effect=capture_set_cookies)
            mock_twikit_client.get_list_tweets = AsyncMock(return_value=[mock_tweet])
            mock_twikit_cls.return_value = mock_twikit_client

            clean_env = {k: v for k, v in os.environ.items()
                         if k not in {"TWITTER_API_IO_KEY", "X_AUTH_TOKEN", "X_CT0"}}
            clean_env.update({"X_AUTH_TOKEN": "real-auth-token", "X_CT0": "real-ct0"})

            with patch.dict(os.environ, clean_env, clear=True):
                await collector._fetch_via_twikit("LLM", "1234", "real-auth-token", "real-ct0")

        assert captured_cookies.get("auth_token") == "real-auth-token"
        assert captured_cookies.get("ct0") == "real-ct0"


# ---------------------------------------------------------------------------
# Unit tests: _parse_datetime helper
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_iso_format(self):
        dt_str = "2026-02-26T10:00:00+00:00"
        result = XTwitterCollector._parse_datetime(dt_str)
        assert result is not None
        assert result.year == 2026
        assert result.tzinfo is not None

    def test_twitter_format(self):
        # Twitter timestamp format
        dt_str = "Thu Feb 26 10:00:00 +0000 2026"
        result = XTwitterCollector._parse_datetime(dt_str)
        assert result is not None
        assert result.tzinfo is not None

    def test_naive_datetime_gets_utc(self):
        dt_str = "2026-02-26T10:00:00"
        result = XTwitterCollector._parse_datetime(dt_str)
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_empty_string_returns_none(self):
        assert XTwitterCollector._parse_datetime("") is None

    def test_invalid_string_returns_none(self):
        assert XTwitterCollector._parse_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# Integration tests (real API — skipped without TWITTER_API_IO_KEY)
# ---------------------------------------------------------------------------

def _has_api_key() -> bool:
    return bool(os.environ.get("TWITTER_API_IO_KEY"))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _has_api_key(), reason="TWITTER_API_IO_KEY not set")
async def test_real_twitterapiio_collect():
    """Real API call: fetch tweets from LLM list via TwitterAPI.io."""
    config = {
        "score_threshold": 0,
        "lookback_hours": 24,
        "max_items": 20,
        "lists": {"LLM": "1664234382868250624"},
        "api_key_env": "TWITTER_API_IO_KEY",
        "auth_token_env": "X_AUTH_TOKEN",
        "ct0_env": "X_CT0",
    }
    collector = XTwitterCollector(config)
    result = await collector.run()

    assert result.success, f"Collection failed: {result.error}"
    for item in result.items:
        assert item.source == "x_twitter"
        assert item.source_type == SourceType.API
        assert item.url.startswith("https://x.com/")
        assert item.title
        assert item.published_at.tzinfo is not None
        assert item.score >= 0
        assert "list" in item.extra
        assert "tweet_id" in item.extra
