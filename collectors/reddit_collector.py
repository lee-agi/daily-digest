"""Reddit subreddit collector via OAuth2 API."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

REDDIT_AUTH_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"


class RedditCollector(BaseCollector):
    """Collect hot/top posts from configured subreddits."""
    source_name = "reddit"
    source_type = SourceType.API

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.client_id = os.environ.get("REDDIT_CLIENT_ID", "")
        self.client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        self.subreddits = config.get("subreddits", [])
        self._access_token: str | None = None

    async def _authenticate(self, client: httpx.AsyncClient) -> str | None:
        """Get OAuth2 access token using client credentials flow."""
        if not self.client_id or not self.client_secret:
            logger.warning("[reddit] No client credentials configured")
            return None

        try:
            resp = await self._request_with_retry(
                client,
                REDDIT_AUTH_URL,
                method="POST",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": "daily-digest/0.1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("access_token")
        except httpx.HTTPError as e:
            logger.error("[reddit] Auth failed: %s", e)
            return None

    async def collect(self) -> list[ContentItem]:
        if not self.subreddits:
            logger.info("[reddit] No subreddits configured")
            return []

        async with httpx.AsyncClient(timeout=30) as client:
            token = await self._authenticate(client)
            if not token:
                # Fallback to public JSON API (no auth)
                return await self._collect_public(client)

            return await self._collect_oauth(client, token)

    async def _collect_oauth(
        self, client: httpx.AsyncClient, token: str
    ) -> list[ContentItem]:
        """Fetch posts using OAuth2 API with cursor pagination."""
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "daily-digest/0.1.0",
        }
        items = []
        max_pages = 3

        for sub in self.subreddits:
            after = None
            for _ in range(max_pages):
                params: dict[str, Any] = {"limit": 50, "t": "day"}
                if after:
                    params["after"] = after

                try:
                    resp = await self._request_with_retry(
                        client,
                        f"{REDDIT_API}/r/{sub}/hot",
                        headers=headers,
                        params=params,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPError as e:
                    logger.error("[reddit] Failed to fetch r/%s: %s", sub, e)
                    break

                for post_data in data.get("data", {}).get("children", []):
                    post = post_data.get("data", {})
                    item = self._parse_post(post, sub)
                    if item and item.published_at >= self.cutoff_time:
                        items.append(item)

                after = data.get("data", {}).get("after")
                if not after:
                    break
                await asyncio.sleep(0.3)

        return items

    async def _collect_public(self, client: httpx.AsyncClient) -> list[ContentItem]:
        """Fallback: fetch posts using public JSON API with cursor pagination."""
        items = []
        headers = {"User-Agent": "daily-digest/0.1.0"}
        max_pages = 3

        for sub in self.subreddits:
            after = None
            for _ in range(max_pages):
                params: dict[str, Any] = {"limit": 50, "t": "day"}
                if after:
                    params["after"] = after

                try:
                    resp = await self._request_with_retry(
                        client,
                        f"https://www.reddit.com/r/{sub}/hot.json",
                        headers=headers,
                        params=params,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPError as e:
                    logger.error("[reddit] Public API failed for r/%s: %s", sub, e)
                    break

                for post_data in data.get("data", {}).get("children", []):
                    post = post_data.get("data", {})
                    item = self._parse_post(post, sub)
                    if item and item.published_at >= self.cutoff_time:
                        items.append(item)

                after = data.get("data", {}).get("after")
                if not after:
                    break
                await asyncio.sleep(0.3)

        return items

    def _parse_post(self, post: dict, subreddit: str) -> ContentItem | None:
        """Parse a Reddit post into ContentItem."""
        if not post.get("title"):
            return None

        created_utc = post.get("created_utc", 0)
        published = datetime.fromtimestamp(created_utc, tz=timezone.utc)

        content = post.get("selftext", "")[:1000]
        if not content and post.get("url"):
            content = f"Link: {post['url']}"

        return ContentItem(
            source="reddit",
            source_type=SourceType.API,
            title=post["title"],
            url=f"https://reddit.com{post.get('permalink', '')}",
            author=post.get("author", ""),
            content=content,
            published_at=published,
            score=float(post.get("score", 0)),
            tags=[subreddit],
            extra={
                "subreddit": subreddit,
                "num_comments": post.get("num_comments", 0),
                "upvote_ratio": post.get("upvote_ratio", 0),
            },
        )
