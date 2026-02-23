"""CoolPaper (papers.cool) collector via HTTP scraping."""

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


class CoolPaperCollector(BaseCollector):
    """Collect trending papers from papers.cool."""
    source_name = "coolpaper"
    source_type = SourceType.HTTP_SCRAPE

    async def collect(self) -> list[ContentItem]:
        items = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            try:
                resp = await client.get(
                    COOLPAPER_URL,
                    headers={"User-Agent": "daily-digest/0.1.0"},
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error("[coolpaper] Failed to fetch: %s", e)
                return []

        soup = BeautifulSoup(resp.text, "lxml")

        # papers.cool lists papers with title, link, and star counts
        for paper_el in soup.select("div.paper-item, article.paper, .paper-card"):
            item = self._parse_paper_element(paper_el)
            if item and item.published_at >= self.cutoff_time:
                items.append(item)

        # Fallback: try to parse from script/JSON data
        if not items:
            items = await self._try_api_endpoint(httpx.AsyncClient(timeout=30))

        return items

    def _parse_paper_element(self, el) -> ContentItem | None:
        """Parse a paper element from the HTML."""
        title_el = el.select_one("h2, h3, .title, a.paper-title")
        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        link = ""
        a_tag = title_el.find("a") if title_el.name != "a" else title_el
        if a_tag and a_tag.get("href"):
            href = a_tag["href"]
            if href.startswith("/"):
                link = f"{COOLPAPER_URL}{href}"
            elif href.startswith("http"):
                link = href

        # Extract ArXiv ID from URL or text
        arxiv_id = None
        match = ARXIV_PATTERN.search(link or title)
        if match:
            arxiv_id = match.group(1)

        # Try to find score/stars
        score = 0.0
        for score_el in el.select(".stars, .score, .reading-count, .count"):
            text = score_el.get_text(strip=True)
            nums = re.findall(r'\d+', text)
            if nums:
                score = float(nums[0])
                break

        # Author
        author = ""
        author_el = el.select_one(".author, .authors")
        if author_el:
            author = author_el.get_text(strip=True)[:200]

        # Summary
        summary = ""
        summary_el = el.select_one(".summary, .abstract, .description")
        if summary_el:
            summary = summary_el.get_text(strip=True)[:500]

        return ContentItem(
            source="coolpaper",
            source_type=SourceType.HTTP_SCRAPE,
            title=title,
            url=link,
            author=author,
            content=summary,
            published_at=datetime.now(timezone.utc),  # papers.cool doesn't always show date
            score=score,
            arxiv_id=arxiv_id,
            tags=["paper"],
        )

    async def _try_api_endpoint(self, client: httpx.AsyncClient) -> list[ContentItem]:
        """Try papers.cool API endpoint if HTML parsing fails."""
        try:
            async with client:
                resp = await client.get(
                    f"{COOLPAPER_URL}/api/papers",
                    headers={"User-Agent": "daily-digest/0.1.0"},
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception:
            return []

        items = []
        for paper in data if isinstance(data, list) else data.get("papers", []):
            arxiv_id = None
            paper_id = paper.get("id", "")
            match = ARXIV_PATTERN.search(paper_id)
            if match:
                arxiv_id = match.group(1)

            items.append(ContentItem(
                source="coolpaper",
                source_type=SourceType.HTTP_SCRAPE,
                title=paper.get("title", ""),
                url=f"{COOLPAPER_URL}/arxiv/{paper_id}" if paper_id else "",
                author=paper.get("authors", ""),
                content=paper.get("abstract", "")[:500],
                published_at=datetime.now(timezone.utc),
                score=float(paper.get("stars", 0)),
                arxiv_id=arxiv_id,
                tags=["paper"],
            ))

        return items
