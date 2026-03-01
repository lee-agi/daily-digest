"""CoolPaper (papers.cool) collector via HTTP scraping.

papers.cool organizes papers by arXiv category. We scrape AI-related
category pages (/arxiv/cs.AI, /arxiv/cs.CL, /arxiv/cs.LG) to get the
latest papers. The page lists papers ranked by position; reading stars
are loaded asynchronously via JS and not available in the HTML.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

COOLPAPER_URL = "https://papers.cool"
ARXIV_PATTERN = re.compile(r'(\d{4}\.\d{4,5})')

# AI-related arXiv categories to scrape
DEFAULT_CATEGORIES = ["cs.AI", "cs.CL", "cs.LG"]


class CoolPaperCollector(BaseCollector):
    """Collect trending papers from papers.cool."""
    source_name = "coolpaper"
    source_type = SourceType.HTTP_SCRAPE

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.categories = config.get("categories", DEFAULT_CATEGORIES)

    async def collect(self) -> list[ContentItem]:
        items = []
        seen_ids: set[str] = set()

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for category in self.categories:
                url = f"{COOLPAPER_URL}/arxiv/{category}"
                try:
                    resp = await self._request_with_retry(
                        client, url,
                        headers={"User-Agent": "daily-digest/0.1.0"},
                    )
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    logger.error("[coolpaper] Failed to fetch %s: %s", url, e)
                    continue

                soup = BeautifulSoup(resp.text, "lxml")
                for paper_el in soup.select("div.paper"):
                    item = self._parse_paper_element(paper_el, category)
                    if item and item.arxiv_id and item.arxiv_id not in seen_ids:
                        seen_ids.add(item.arxiv_id)
                        items.append(item)

        logger.info("[coolpaper] Parsed %d papers from %d categories",
                    len(items), len(self.categories))
        return items

    def _parse_paper_element(self, el, category: str) -> ContentItem | None:
        """Parse a paper element from the HTML."""
        # Title is in a.title-link inside h2.title
        title_link = el.select_one("a.title-link")
        if not title_link:
            return None

        title = title_link.get_text(strip=True)
        href = title_link.get("href", "")
        link = f"{COOLPAPER_URL}{href}" if href.startswith("/") else href

        # Extract ArXiv ID from the element's id attribute or link
        arxiv_id = None
        el_id = el.get("id", "")
        match = ARXIV_PATTERN.search(el_id or href)
        if match:
            arxiv_id = match.group(1)

        # Position in ranking as a proxy for score (higher rank = lower number)
        index_el = el.select_one("span.index")
        rank = 0
        if index_el:
            rank_text = index_el.get_text(strip=True).lstrip("#")
            try:
                rank = int(rank_text)
            except ValueError:
                pass
        # Invert rank to score: #1 gets highest score
        score = max(0.0, 100.0 - rank) if rank > 0 else 0.0

        # papers.cool doesn't show dates in HTML, the category page shows
        # today's papers, so we use current time
        return ContentItem(
            source="coolpaper",
            source_type=SourceType.HTTP_SCRAPE,
            title=title,
            url=link,
            content="",
            published_at=datetime.now(timezone.utc),
            score=score,
            arxiv_id=arxiv_id,
            tags=["paper", category],
        )
