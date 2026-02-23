"""Cross-source deduplication by URL, title similarity, and ArXiv ID."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from schema import ContentItem

# ArXiv ID pattern: e.g., 2301.12345 or 2301.12345v2
ARXIV_PATTERN = re.compile(r'(\d{4}\.\d{4,5})(v\d+)?')


def extract_arxiv_id(text: str) -> str | None:
    """Extract ArXiv ID from URL or text."""
    match = ARXIV_PATTERN.search(text)
    return match.group(1) if match else None


def deduplicate(items: list[ContentItem], title_threshold: float = 0.85) -> list[ContentItem]:
    """Remove duplicates across sources.

    Priority: URL exact match > ArXiv ID > title similarity.
    Keeps the item with the highest score when duplicates are found.
    """
    seen_urls: dict[str, int] = {}   # url -> index in result
    seen_arxiv: dict[str, int] = {}  # arxiv_id -> index in result
    result: list[ContentItem] = []

    # Sort by score desc so higher-scored items get priority
    sorted_items = sorted(items, key=lambda x: x.score, reverse=True)

    for item in sorted_items:
        # 1) URL dedup
        if item.url and item.url in seen_urls:
            continue

        # 2) ArXiv ID dedup
        arxiv_id = item.arxiv_id or extract_arxiv_id(item.url or "")
        if arxiv_id:
            if arxiv_id in seen_arxiv:
                continue
            seen_arxiv[arxiv_id] = len(result)

        # 3) Title similarity dedup
        is_dup = False
        for existing in result:
            ratio = SequenceMatcher(None, item.title.lower(), existing.title.lower()).ratio()
            if ratio >= title_threshold:
                is_dup = True
                break

        if is_dup:
            continue

        if item.url:
            seen_urls[item.url] = len(result)
        result.append(item)

    return result
