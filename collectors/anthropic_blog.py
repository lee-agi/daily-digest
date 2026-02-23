"""Anthropic Blog collector via HTTP scraping of Next.js RSC payload.

Anthropic has no RSS feed, so we scrape /engineering and /research pages,
extracting post metadata from the __next_f RSC (React Server Component)
payload embedded in script tags.

Fallback: if RSC parsing yields 0 results, degrade to sitemap.xml parsing.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx
from dateutil import parser as dateutil_parser

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

BASE_URL = "https://www.anthropic.com"
PAGES = ["/engineering", "/research"]

# Regex to extract post objects from RSC payload.
# Matches "title":"...", "slug":{"_type":"slug","current":"..."},
# "publishedOn":"..." patterns within __next_f chunks.
TITLE_RE = re.compile(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"')
SLUG_RE = re.compile(
    r'"slug"\s*:\s*\{\s*"_type"\s*:\s*"slug"\s*,\s*"current"\s*:\s*"((?:[^"\\]|\\.)*)"'
)
PUBLISHED_RE = re.compile(r'"publishedOn"\s*:\s*"((?:[^"\\]|\\.)*)"')


class AnthropicBlogCollector(BaseCollector):
    """Collect blog posts from Anthropic's engineering and research pages."""

    source_name = "anthropic"
    source_type = SourceType.HTTP_SCRAPE

    async def collect(self) -> list[ContentItem]:
        items: list[ContentItem] = []
        seen_slugs: set[str] = set()

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for page_path in PAGES:
                page_items = await self._scrape_page(client, page_path, seen_slugs)
                items.extend(page_items)

            # Fallback to sitemap if RSC parsing found nothing
            if not items:
                logger.warning(
                    "[anthropic] RSC parsing returned 0 items, falling back to sitemap"
                )
                items = await self._fallback_sitemap(client, seen_slugs)

        return [item for item in items if item.published_at >= self.cutoff_time]

    async def _scrape_page(
        self,
        client: httpx.AsyncClient,
        page_path: str,
        seen_slugs: set[str],
    ) -> list[ContentItem]:
        """Scrape a single page (/engineering or /research) for blog posts."""
        url = f"{BASE_URL}{page_path}"
        try:
            resp = await client.get(
                url, headers={"User-Agent": "daily-digest/0.2.0"}
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("[anthropic] Failed to fetch %s: %s", url, e)
            return []

        return self._parse_rsc_payload(resp.text, page_path, seen_slugs)

    def _parse_rsc_payload(
        self,
        html: str,
        page_path: str,
        seen_slugs: set[str],
    ) -> list[ContentItem]:
        """Extract blog posts from Next.js __next_f RSC payload in script tags."""
        items: list[ContentItem] = []
        section = page_path.lstrip("/")  # "engineering" or "research"

        # Find all __next_f script payload chunks
        # Pattern: self.__next_f.push([1,"..."])
        chunks = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html)

        # Unescape the JSON-encoded strings
        full_payload = ""
        for chunk in chunks:
            try:
                unescaped = chunk.encode().decode("unicode_escape")
            except (UnicodeDecodeError, ValueError):
                unescaped = chunk
            full_payload += unescaped

        # Extract all title/slug/publishedOn tuples by position.
        # Strategy: find slug matches, then look backwards/forwards for
        # the nearest title and publishedOn within a reasonable window.
        slug_matches = list(SLUG_RE.finditer(full_payload))
        title_matches = list(TITLE_RE.finditer(full_payload))
        published_matches = list(PUBLISHED_RE.finditer(full_payload))

        for slug_match in slug_matches:
            slug = slug_match.group(1)
            if not slug or slug in seen_slugs:
                continue

            slug_pos = slug_match.start()

            # Find nearest title before or near the slug (within 2000 chars)
            title = self._find_nearest(title_matches, slug_pos, window=2000)
            if not title:
                continue

            # Find nearest publishedOn after or near the slug (within 2000 chars)
            published_str = self._find_nearest(
                published_matches, slug_pos, window=2000
            )

            published = self._parse_date(published_str)

            seen_slugs.add(slug)
            items.append(ContentItem(
                source="anthropic",
                source_type=SourceType.HTTP_SCRAPE,
                title=title,
                url=f"{BASE_URL}/{section}/{slug}",
                author="Anthropic",
                content="",
                published_at=published,
                score=0.0,
                tags=[section, "blog"],
            ))

        logger.info(
            "[anthropic] Parsed %d items from %s", len(items), page_path
        )
        return items

    @staticmethod
    def _find_nearest(
        matches: list[re.Match], target_pos: int, window: int = 2000
    ) -> str | None:
        """Find the match group(1) closest to target_pos within window."""
        best = None
        best_dist = window + 1
        for m in matches:
            dist = abs(m.start() - target_pos)
            if dist < best_dist:
                best_dist = dist
                best = m.group(1)
        return best if best_dist <= window else None

    def _parse_date(self, date_str: str | None) -> datetime:
        """Parse ISO date string, fallback to now()."""
        if not date_str or date_str == "null":
            return datetime.now(timezone.utc)
        try:
            dt = dateutil_parser.parse(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    async def _fallback_sitemap(
        self,
        client: httpx.AsyncClient,
        seen_slugs: set[str],
    ) -> list[ContentItem]:
        """Fallback: parse sitemap.xml for /engineering/ and /research/ URLs."""
        try:
            resp = await client.get(
                f"{BASE_URL}/sitemap.xml",
                headers={"User-Agent": "daily-digest/0.2.0"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("[anthropic] Failed to fetch sitemap: %s", e)
            return []

        items: list[ContentItem] = []
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            logger.error("[anthropic] Failed to parse sitemap XML: %s", e)
            return []

        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        for url_el in root.findall(".//sm:url", ns):
            loc_el = url_el.find("sm:loc", ns)
            if loc_el is None or loc_el.text is None:
                continue

            loc = loc_el.text.strip()
            # Only include /engineering/ and /research/ posts
            section = None
            for s in ("engineering", "research"):
                if f"/{s}/" in loc:
                    section = s
                    break
            if section is None:
                continue

            # Extract slug from URL
            slug = loc.rstrip("/").split("/")[-1]
            if not slug or slug in seen_slugs or slug == section:
                continue

            # Try to get lastmod date
            lastmod_el = url_el.find("sm:lastmod", ns)
            published = datetime.now(timezone.utc)
            if lastmod_el is not None and lastmod_el.text:
                published = self._parse_date(lastmod_el.text.strip())

            seen_slugs.add(slug)
            # Create a readable title from slug
            title = slug.replace("-", " ").title()

            items.append(ContentItem(
                source="anthropic",
                source_type=SourceType.HTTP_SCRAPE,
                title=title,
                url=loc,
                author="Anthropic",
                content="",
                published_at=published,
                score=0.0,
                tags=[section, "blog"],
            ))

        logger.info("[anthropic] Sitemap fallback found %d items", len(items))
        return items
