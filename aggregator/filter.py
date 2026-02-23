"""Threshold-based filtering for content items."""

from __future__ import annotations

from schema import ContentItem


def filter_by_score(items: list[ContentItem], threshold: float) -> list[ContentItem]:
    """Filter items below the score threshold."""
    return [item for item in items if item.score >= threshold]


def filter_by_source(items: list[ContentItem], source: str) -> list[ContentItem]:
    """Get items from a specific source."""
    return [item for item in items if item.source == source]


def sort_by_score(items: list[ContentItem], descending: bool = True) -> list[ContentItem]:
    """Sort items by engagement score."""
    return sorted(items, key=lambda x: x.score, reverse=descending)
