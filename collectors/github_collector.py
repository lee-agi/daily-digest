"""GitHub trending repos and starred repo activity via REST API."""

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

GITHUB_API = "https://api.github.com"


class GitHubCollector(BaseCollector):
    """Collect GitHub trending repos and releases from starred repos."""
    source_name = "github"
    source_type = SourceType.API

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.token = os.environ.get("GITHUB_TOKEN", "")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def collect(self) -> list[ContentItem]:
        items = []
        async with httpx.AsyncClient(timeout=30, headers=self._headers()) as client:
            # 1. Search trending repos (created/pushed recently, high stars)
            trending = await self._fetch_trending(client)
            items.extend(trending)

            # 2. Fetch releases from repos user has starred
            if self.token:
                releases = await self._fetch_starred_releases(client)
                items.extend(releases)

        return items

    async def _fetch_trending(self, client: httpx.AsyncClient) -> list[ContentItem]:
        """Fetch trending repos created or pushed in the last 24h."""
        cutoff_str = self.cutoff_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        query = f"pushed:>{cutoff_str} stars:>5"
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": min(self.max_items, 30),
        }

        try:
            resp = await client.get(f"{GITHUB_API}/search/repositories", params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.error("[github] Trending search failed: %s", e)
            return []

        items = []
        for repo in data.get("items", []):
            pushed_at = dateutil_parser.parse(repo["pushed_at"])
            if pushed_at.tzinfo is None:
                pushed_at = pushed_at.replace(tzinfo=timezone.utc)

            items.append(ContentItem(
                source="github",
                source_type=SourceType.API,
                title=f"{repo['full_name']}: {repo.get('description', '')}",
                url=repo["html_url"],
                author=repo["owner"]["login"],
                content=repo.get("description", ""),
                published_at=pushed_at,
                score=float(repo.get("stargazers_count", 0)),
                tags=repo.get("topics", []),
                language=repo.get("language") or "en",
                extra={
                    "stars": repo.get("stargazers_count", 0),
                    "forks": repo.get("forks_count", 0),
                    "language": repo.get("language"),
                },
            ))
        return items

    async def _fetch_starred_releases(self, client: httpx.AsyncClient) -> list[ContentItem]:
        """Fetch recent releases from user's starred repos."""
        # Get user's starred repos (limited to recent activity)
        try:
            resp = await client.get(
                f"{GITHUB_API}/user/starred",
                params={"per_page": 50, "sort": "updated"},
            )
            resp.raise_for_status()
            starred_repos = resp.json()
        except httpx.HTTPError as e:
            logger.error("[github] Failed to fetch starred repos: %s", e)
            return []

        items = []
        for repo in starred_repos[:30]:  # Limit API calls
            try:
                resp = await client.get(
                    f"{GITHUB_API}/repos/{repo['full_name']}/releases",
                    params={"per_page": 3},
                )
                if resp.status_code != 200:
                    continue
                releases = resp.json()
            except httpx.HTTPError:
                continue

            for release in releases:
                published = dateutil_parser.parse(release["published_at"])
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)

                if published < self.cutoff_time:
                    continue

                items.append(ContentItem(
                    source="github",
                    source_type=SourceType.API,
                    title=f"[Release] {repo['full_name']} {release['tag_name']}",
                    url=release["html_url"],
                    author=release.get("author", {}).get("login", ""),
                    content=release.get("body", "")[:1000],
                    published_at=published,
                    score=float(repo.get("stargazers_count", 0)),
                    tags=["release"],
                    extra={"tag": release["tag_name"]},
                ))

        return items
