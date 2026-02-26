"""HuggingFace Daily Papers collector via HTTP API."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from dateutil import parser as dateutil_parser

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

HF_PAPERS_API = "https://huggingface.co/api/daily_papers"
ARXIV_PATTERN = re.compile(r'(\d{4}\.\d{4,5})')


class HuggingFacePapersCollector(BaseCollector):
    """Collect daily trending papers from HuggingFace."""
    source_name = "huggingface"
    source_type = SourceType.HTTP_SCRAPE

    async def collect(self) -> list[ContentItem]:
        items = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            try:
                resp = await client.get(HF_PAPERS_API)
                resp.raise_for_status()
                papers = resp.json()
            except httpx.HTTPError as e:
                logger.error("[huggingface] Failed to fetch papers: %s", e)
                return []

        for paper_data in papers:
            item = self._parse_paper(paper_data)
            if item and item.published_at >= self.cutoff_time:
                items.append(item)

        return items

    def _parse_paper(self, data: dict) -> ContentItem | None:
        """Parse a HuggingFace paper entry."""
        paper = data.get("paper", {})
        if not paper:
            return None

        paper_id = paper.get("id", "")
        title = paper.get("title", "Untitled")
        summary = paper.get("summary", "")
        authors = paper.get("authors", [])
        author_names = ", ".join(a.get("name", "") for a in authors[:5])

        # Extract ArXiv ID
        arxiv_id = None
        match = ARXIV_PATTERN.search(paper_id)
        if match:
            arxiv_id = match.group(1)

        # Parse date — use submittedOnDailyAt (when the paper appeared on
        # HF daily papers) rather than publishedAt (ArXiv publication date),
        # so the lookback window filter works correctly.
        published_at = datetime.now(timezone.utc)
        daily_date = paper.get("submittedOnDailyAt") or data.get("submittedOnDailyAt")
        pub_date = daily_date or data.get("publishedAt") or paper.get("publishedAt")
        if pub_date:
            try:
                published_at = dateutil_parser.parse(pub_date)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

        upvotes = data.get("paper", {}).get("upvotes", 0)
        if not upvotes:
            upvotes = data.get("numLikes", 0)

        return ContentItem(
            source="huggingface",
            source_type=SourceType.HTTP_SCRAPE,
            title=title,
            url=f"https://huggingface.co/papers/{paper_id}",
            author=author_names,
            content=summary[:1000],
            published_at=published_at,
            score=float(upvotes),
            arxiv_id=arxiv_id,
            tags=["paper", "ml"],
            extra={"paper_id": paper_id},
        )
