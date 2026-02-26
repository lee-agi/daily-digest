"""X/Twitter collector via TwitterAPI.io (primary) + twikit (fallback).

Fetches tweets from configured Twitter Lists using:
- Primary: TwitterAPI.io REST API (https://api.twitterapi.io)
- Fallback: twikit cookie-based client (auth_token + ct0)

Both paths produce identical ContentItem output.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from dateutil import parser as dateutil_parser

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

TWITTERAPIIO_BASE = "https://api.twitterapi.io"


class XTwitterCollector(BaseCollector):
    """Collect tweets from X/Twitter lists via TwitterAPI.io or twikit."""

    source_name = "x_twitter"
    source_type = SourceType.API

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.lists: dict[str, str] = config.get("lists", {})
        self.api_key_env: str = config.get("api_key_env", "TWITTER_API_IO_KEY")
        self.auth_token_env: str = config.get("auth_token_env", "X_AUTH_TOKEN")
        self.ct0_env: str = config.get("ct0_env", "X_CT0")

    async def collect(self) -> list[ContentItem]:
        """Fetch tweets from all configured lists.

        Tries TwitterAPI.io for each list; falls back to twikit on failure.
        Returns [] if no credentials are configured.
        """
        if not self.lists:
            logger.info("[x_twitter] No lists configured")
            return []

        api_key = os.environ.get(self.api_key_env, "")
        auth_token = os.environ.get(self.auth_token_env, "")
        ct0 = os.environ.get(self.ct0_env, "")

        if not api_key and not (auth_token and ct0):
            logger.warning(
                "[x_twitter] No credentials configured (%s or %s/%s)",
                self.api_key_env, self.auth_token_env, self.ct0_env,
            )
            return []

        items: list[ContentItem] = []
        for list_name, list_id in self.lists.items():
            list_items = await self._collect_list(
                list_name, list_id, api_key, auth_token, ct0
            )
            items.extend(list_items)

        return items

    async def _collect_list(
        self,
        list_name: str,
        list_id: str,
        api_key: str,
        auth_token: str,
        ct0: str,
    ) -> list[ContentItem]:
        """Collect tweets for a single list, with fallback logic."""
        if api_key:
            try:
                return await self._fetch_via_twitterapiio(list_name, list_id, api_key)
            except Exception as exc:
                logger.warning(
                    "[x_twitter] TwitterAPI.io failed for list %s: %s — falling back to twikit",
                    list_name, exc,
                )

        if auth_token and ct0:
            try:
                return await self._fetch_via_twikit(list_name, list_id, auth_token, ct0)
            except Exception as exc:
                logger.error(
                    "[x_twitter] twikit also failed for list %s: %s", list_name, exc
                )
        else:
            logger.warning(
                "[x_twitter] No fallback credentials available for list %s", list_name
            )

        return []

    # ------------------------------------------------------------------
    # TwitterAPI.io path
    # ------------------------------------------------------------------

    async def _fetch_via_twitterapiio(
        self, list_name: str, list_id: str, api_key: str
    ) -> list[ContentItem]:
        """Fetch list tweets via TwitterAPI.io REST API (with pagination)."""
        logger.info("[x_twitter] Fetching list %s via TwitterAPI.io", list_name)
        items: list[ContentItem] = []
        cursor: str | None = None

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                params: dict[str, Any] = {"listId": list_id, "count": 100}
                if cursor:
                    params["cursor"] = cursor

                resp = await client.get(
                    f"{TWITTERAPIIO_BASE}/twitter/list/tweets",
                    params=params,
                    headers={"X-API-Key": api_key},
                )
                resp.raise_for_status()
                data = resp.json()

                tweets = data.get("tweets", [])
                if not tweets:
                    break

                for raw in tweets:
                    item = self._build_item_from_twitterapiio(raw, list_name)
                    if item and item.published_at >= self.cutoff_time:
                        items.append(item)

                # Stop pagination when oldest tweet in page is beyond cutoff.
                oldest_time = self._parse_datetime(tweets[-1].get("createdAt", ""))
                if oldest_time and oldest_time < self.cutoff_time:
                    break

                if not data.get("has_next_page", False):
                    break
                cursor = data.get("next_cursor") or None
                if not cursor:
                    break

        logger.info(
            "[x_twitter] TwitterAPI.io: %d items from list %s", len(items), list_name
        )
        return items

    def _build_item_from_twitterapiio(
        self, tweet: dict[str, Any], list_name: str
    ) -> ContentItem | None:
        """Map a TwitterAPI.io tweet dict to ContentItem."""
        tweet_id = tweet.get("id", "")
        text = tweet.get("text", "")
        if not tweet_id or not text:
            return None

        author_obj = tweet.get("author", {})
        screen_name = (
            author_obj.get("userName")
            or author_obj.get("screen_name")
            or "unknown"
        )

        like_count = int(tweet.get("likeCount") or 0)
        retweet_count = int(tweet.get("retweetCount") or 0)
        reply_count = int(tweet.get("replyCount") or 0)

        published_at = self._parse_datetime(tweet.get("createdAt", ""))
        if published_at is None:
            published_at = datetime.now(timezone.utc)

        return ContentItem(
            source="x_twitter",
            source_type=SourceType.API,
            title=text[:200],
            url=f"https://x.com/{screen_name}/status/{tweet_id}",
            author=screen_name,
            content=text,
            published_at=published_at,
            score=float(like_count + retweet_count * 2),
            extra={
                "likes": like_count,
                "retweets": retweet_count,
                "replies": reply_count,
                "list": list_name,
                "tweet_id": tweet_id,
            },
        )

    # ------------------------------------------------------------------
    # twikit path
    # ------------------------------------------------------------------

    async def _fetch_via_twikit(
        self, list_name: str, list_id: str, auth_token: str, ct0: str
    ) -> list[ContentItem]:
        """Fetch list tweets via twikit cookie-based auth."""
        logger.info("[x_twitter] Fetching list %s via twikit", list_name)
        from twikit import Client  # lazy import to avoid hard dependency

        client = Client()
        client.set_cookies({"auth_token": auth_token, "ct0": ct0})

        result = await client.get_list_tweets(list_id, count=100)

        items: list[ContentItem] = []
        for tweet in result:
            item = self._build_item_from_twikit(tweet, list_name)
            if item and item.published_at >= self.cutoff_time:
                items.append(item)

        logger.info(
            "[x_twitter] twikit: %d items from list %s", len(items), list_name
        )
        return items

    def _build_item_from_twikit(self, tweet: Any, list_name: str) -> ContentItem | None:
        """Map a twikit Tweet object to ContentItem."""
        tweet_id = tweet.id
        text = tweet.text
        if not tweet_id or not text:
            return None

        screen_name = tweet.user.screen_name if tweet.user else "unknown"
        like_count = int(tweet.favorite_count or 0)
        retweet_count = int(tweet.retweet_count or 0)
        reply_count = int(tweet.reply_count or 0)

        try:
            published_at = tweet.created_at_datetime
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
        except Exception:
            published_at = datetime.now(timezone.utc)

        return ContentItem(
            source="x_twitter",
            source_type=SourceType.API,
            title=text[:200],
            url=f"https://x.com/{screen_name}/status/{tweet_id}",
            author=screen_name,
            content=text,
            published_at=published_at,
            score=float(like_count + retweet_count * 2),
            extra={
                "likes": like_count,
                "retweets": retweet_count,
                "replies": reply_count,
                "list": list_name,
                "tweet_id": tweet_id,
            },
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_datetime(value: str) -> datetime | None:
        """Parse a datetime string (ISO or Twitter format) to aware UTC datetime."""
        if not value:
            return None
        try:
            dt = dateutil_parser.parse(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
