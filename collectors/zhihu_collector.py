"""Zhihu collector using direct API calls.

Replaces the RSSHub-based ZhihuCollector. Supports hot/recommend/follow feeds
and reuses cookies from zhihu-cli (~/.zhihu-cli/cookies.json).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

_COOKIES_PATH = Path.home() / ".zhihu-cli" / "cookies.json"

_FEED_URLS = {
    "hot": "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit={limit}&desktop=true",
    "recommend": "https://www.zhihu.com/api/v3/feed/topstory/recommend?page_number={page}&limit={limit}&desktop=true",
    "follow": "https://www.zhihu.com/api/v3/feed/topstory/follow?limit={limit}&desktop=true",
}

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Regex to extract heat score from detail_text like "1234 万热度" or "890234 热度"
_HEAT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万?\s*热度")


class ZhihuCliCollector(BaseCollector):
    """Collect content from Zhihu via direct API, using zhihu-cli cookies."""

    source_name = "zhihu"
    source_type = SourceType.API

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.feed_types: list[str] = config.get(
            "feed_types", ["hot", "recommend", "follow"]
        )
        self.limit_per_feed: int = config.get("limit_per_feed", 20)
        self.feed_filters: dict[str, dict[str, float]] = config.get("feed_filters", {})
        self._cookies_path: Path = _COOKIES_PATH

    # ── Cookie management ────────────────────────────────────────

    def _load_cookies(self) -> dict[str, str]:
        """Load cookies from zhihu-cli cookies.json."""
        try:
            text = self._cookies_path.read_text(encoding="utf-8")
            return json.loads(text)
        except FileNotFoundError:
            logger.error(
                "[zhihu] Cookies file not found: %s. Run 'zhihu login' first.",
                self._cookies_path,
            )
            return {}
        except (json.JSONDecodeError, OSError) as e:
            logger.error("[zhihu] Failed to load cookies: %s", e)
            return {}

    # ── Header building ──────────────────────────────────────────

    def _build_headers(self, cookies: dict[str, str]) -> dict[str, str]:
        """Build request headers matching zhihu-cli."""
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept-Encoding": "identity",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "referer": "https://www.zhihu.com/",
            "x-api-version": "3.0.53",
            "x-requested-with": "fetch",
            "Cookie": cookie_str,
        }
        xsrf = cookies.get("_xsrf")
        if xsrf:
            headers["x-xsrftoken"] = xsrf
        return headers

    # ── Feed URL ─────────────────────────────────────────────────

    def _get_feed_url(self, feed_type: str, page: int = 1) -> str | None:
        """Return the API URL for a feed type, or None if unknown."""
        template = _FEED_URLS.get(feed_type)
        if template is None:
            logger.warning("[zhihu] Unknown feed type: %s", feed_type)
            return None
        return template.format(limit=self.limit_per_feed, page=page)

    # ── Parsing ──────────────────────────────────────────────────

    def _parse_hot(
        self,
        data: dict[str, Any],
        seen_urls: set[str] | None = None,
    ) -> list[ContentItem]:
        """Parse hot feed response into ContentItems."""
        if seen_urls is None:
            seen_urls = set()

        items: list[ContentItem] = []
        for entry in data.get("data", []):
            target = entry.get("target", {})
            if not target:
                continue

            title = target.get("title") or ""
            if not title:
                title_area = target.get("title_area", {})
                title = title_area.get("text", "") if title_area else ""
            if not title:
                continue

            # URL: try target.url first, then target.link.url (legacy)
            url = target.get("url", "")
            if not url:
                link = target.get("link", {})
                url = (link.get("url", "") if isinstance(link, dict) else "")
            # Rewrite api.zhihu.com → www.zhihu.com
            url = url.replace(
                "https://api.zhihu.com/questions/",
                "https://www.zhihu.com/question/",
            )

            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Score from detail_text: "1234 万热度" → 12340000
            detail = entry.get("detail_text", "")
            score = self._parse_heat_score(detail)

            content = target.get("excerpt", "")

            items.append(ContentItem(
                source="zhihu",
                source_type=SourceType.API,
                title=title,
                url=url,
                content=content,
                score=score,
                language="zh",
                extra={"feed_type": "hot", "detail_text": detail},
            ))

        return items

    def _parse_recommend_or_follow(
        self,
        data: dict[str, Any],
        seen_urls: set[str] | None = None,
    ) -> list[ContentItem]:
        """Parse recommend/follow feed response into ContentItems."""
        if seen_urls is None:
            seen_urls = set()

        items: list[ContentItem] = []
        for entry in data.get("data", []):
            # Filter ads
            if entry.get("type") == "feed_advert":
                continue

            target = entry.get("target", {})
            if not target or not target.get("type"):
                continue

            is_article = target.get("type") == "article"

            if is_article:
                title = target.get("title", "")
                url = f"https://zhuanlan.zhihu.com/p/{target.get('id', '')}"
            else:
                question = target.get("question", {})
                title = question.get("title") or target.get("title", "")
                qid = question.get("id", "")
                aid = target.get("id", "")
                url = f"https://www.zhihu.com/question/{qid}/answer/{aid}"

            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)

            author_info = target.get("author", {})
            author = author_info.get("name", "") if author_info else ""
            score = float(target.get("voteup_count", 0))
            favorite_count = float(target.get("favorite_count", 0))
            content = target.get("excerpt_new") or target.get("excerpt", "")

            # Parse created_time (Unix timestamp)
            created_time = target.get("created_time")
            if created_time:
                published_at = datetime.fromtimestamp(created_time, tz=timezone.utc)
            else:
                published_at = datetime.now(timezone.utc)

            items.append(ContentItem(
                source="zhihu",
                source_type=SourceType.API,
                title=title,
                url=url,
                author=author,
                content=content,
                published_at=published_at,
                score=score,
                language="zh",
                extra={"feed_type": entry.get("type", "unknown"), "voteup_count": score, "favorite_count": favorite_count},
            ))

        return items

    @staticmethod
    def _parse_heat_score(detail_text: str) -> float:
        """Extract numeric score from heat text like '1234 万热度'."""
        match = _HEAT_RE.search(detail_text)
        if not match:
            return 0.0
        value = float(match.group(1))
        if "万" in detail_text:
            value *= 10000
        return value

    # ── Per-feed filtering ──────────────────────────────────────

    def _apply_feed_filter(
        self,
        items: list[ContentItem],
        feed_filter: dict[str, float],
        feed_type: str,
    ) -> list[ContentItem]:
        """Filter items by per-feed min_voteup / min_favorite thresholds."""
        min_voteup = feed_filter.get("min_voteup", 0)
        min_favorite = feed_filter.get("min_favorite", 0)
        filtered = [
            item for item in items
            if item.extra.get("voteup_count", 0) >= min_voteup
            and item.extra.get("favorite_count", 0) >= min_favorite
        ]
        logger.info(
            "[zhihu] %s filter: %d -> %d", feed_type, len(items), len(filtered)
        )
        return filtered

    # ── Paginated fetch methods ─────────────────────────────────

    async def _fetch_recommend_pages(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        seen_urls: set[str],
    ) -> list[ContentItem]:
        """Fetch recommend feed with page_number pagination."""
        all_items: list[ContentItem] = []
        max_pages = 5

        for page in range(1, max_pages + 1):
            url = self._get_feed_url("recommend", page=page)
            if url is None:
                break

            try:
                resp = await self._request_with_retry(client, url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                logger.error("[zhihu] Failed to fetch recommend page %d: %s", page, e)
                break
            except (json.JSONDecodeError, ValueError) as e:
                logger.error("[zhihu] Failed to parse recommend page %d: %s", page, e)
                break

            items = self._parse_recommend_or_follow(data, seen_urls)
            all_items.extend(items)

            # Stop conditions
            paging = data.get("paging", {})
            if paging.get("is_end", False):
                break
            if not data.get("data"):
                break
            if len(all_items) >= self.limit_per_feed:
                break

            if page < max_pages:
                await asyncio.sleep(0.5)

        return all_items

    async def _fetch_follow_pages(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        seen_urls: set[str],
    ) -> list[ContentItem]:
        """Fetch follow feed with paging.next URL pagination."""
        all_items: list[ContentItem] = []
        max_pages = 5
        url = self._get_feed_url("follow")
        if url is None:
            return []

        for page in range(1, max_pages + 1):
            try:
                resp = await self._request_with_retry(client, url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                logger.error("[zhihu] Failed to fetch follow page %d: %s", page, e)
                break
            except (json.JSONDecodeError, ValueError) as e:
                logger.error("[zhihu] Failed to parse follow page %d: %s", page, e)
                break

            items = self._parse_recommend_or_follow(data, seen_urls)
            all_items.extend(items)

            # Stop conditions
            paging = data.get("paging", {})
            if paging.get("is_end", False):
                break
            if not data.get("data"):
                break
            if len(all_items) >= self.limit_per_feed:
                break

            next_url = paging.get("next")
            if not next_url:
                break
            url = next_url

            if page < max_pages:
                await asyncio.sleep(0.5)

        return all_items

    async def _fetch_single_page(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        feed_type: str,
        seen_urls: set[str],
    ) -> list[ContentItem]:
        """Fetch a single page for hot or unknown feed types."""
        url = self._get_feed_url(feed_type)
        if url is None:
            return []

        try:
            resp = await self._request_with_retry(client, url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.error("[zhihu] Failed to fetch %s feed: %s", feed_type, e)
            return []
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("[zhihu] Failed to parse %s response: %s", feed_type, e)
            return []

        if feed_type == "hot":
            return self._parse_hot(data, seen_urls)
        return self._parse_recommend_or_follow(data, seen_urls)

    # ── Main collect ─────────────────────────────────────────────

    async def collect(self) -> list[ContentItem]:
        """Fetch items from all configured feed types."""
        cookies = self._load_cookies()
        if not cookies:
            return []

        headers = self._build_headers(cookies)
        seen_urls: set[str] = set()
        all_items: list[ContentItem] = []

        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True,
        ) as client:
            for feed_type in self.feed_types:
                if feed_type == "recommend":
                    items = await self._fetch_recommend_pages(
                        client, headers, seen_urls,
                    )
                elif feed_type == "follow":
                    items = await self._fetch_follow_pages(
                        client, headers, seen_urls,
                    )
                else:
                    items = await self._fetch_single_page(
                        client, headers, feed_type, seen_urls,
                    )

                feed_filter = self.feed_filters.get(feed_type)
                if feed_filter:
                    items = self._apply_feed_filter(items, feed_filter, feed_type)

                all_items.extend(items)
                logger.info(
                    "[zhihu] %s feed: %d items", feed_type, len(items)
                )

        return all_items
