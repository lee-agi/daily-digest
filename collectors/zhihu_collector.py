"""Zhihu collector using direct API calls.

Replaces the RSSHub-based ZhihuCollector. Supports hot/recommend/follow feeds
and reuses cookies from zhihu-cli (~/.zhihu-cli/cookies.json).
"""

from __future__ import annotations

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
    "recommend": "https://www.zhihu.com/api/v3/feed/topstory/recommend?page_number=1&limit={limit}&desktop=true",
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

    def _get_feed_url(self, feed_type: str) -> str | None:
        """Return the API URL for a feed type, or None if unknown."""
        template = _FEED_URLS.get(feed_type)
        if template is None:
            logger.warning("[zhihu] Unknown feed type: %s", feed_type)
            return None
        return template.format(limit=self.limit_per_feed)

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
                extra={"feed_type": entry.get("type", "unknown")},
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
                url = self._get_feed_url(feed_type)
                if url is None:
                    continue

                try:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPError as e:
                    logger.error(
                        "[zhihu] Failed to fetch %s feed: %s", feed_type, e
                    )
                    continue
                except (json.JSONDecodeError, ValueError) as e:
                    logger.error(
                        "[zhihu] Failed to parse %s response: %s", feed_type, e
                    )
                    continue

                if feed_type == "hot":
                    items = self._parse_hot(data, seen_urls)
                else:
                    items = self._parse_recommend_or_follow(data, seen_urls)

                all_items.extend(items)
                logger.info(
                    "[zhihu] %s feed: %d items", feed_type, len(items)
                )

        return all_items
