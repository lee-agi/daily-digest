"""Cross-source topic clustering and categorization."""

from __future__ import annotations

from schema import ContentItem

# Default category mapping based on source + keywords
CATEGORY_RULES: dict[str, list[str]] = {
    "AI Models & Research": ["huggingface", "coolpaper"],
    "Developer Tools": ["github"],
    "Research Papers": [],  # Matched by arxiv_id presence
    "Videos & Podcasts": ["youtube", "apple_podcast", "xiaoyuzhou"],
    "Books & Reading": ["weread"],
    "Social & Community": ["x_twitter", "reddit", "zhihu", "jike"],
}

# Invert: source -> category
_SOURCE_TO_CATEGORY: dict[str, str] = {}
for cat, sources in CATEGORY_RULES.items():
    for src in sources:
        _SOURCE_TO_CATEGORY[src] = cat


def categorize_item(item: ContentItem) -> str:
    """Assign a category to an item based on source and content."""
    # Papers with ArXiv IDs go to Research Papers
    if item.arxiv_id:
        return "Research Papers"

    return _SOURCE_TO_CATEGORY.get(item.source, "Social & Community")


def group_by_category(
    items: list[ContentItem],
    categories: list[str] | None = None,
) -> dict[str, list[ContentItem]]:
    """Group items by category.

    Args:
        items: All content items.
        categories: Ordered list of category names. If None, uses CATEGORY_RULES keys.

    Returns:
        Dict of category -> items, ordered by the categories list.
    """
    if categories is None:
        categories = list(CATEGORY_RULES.keys())

    grouped: dict[str, list[ContentItem]] = {cat: [] for cat in categories}

    for item in items:
        cat = categorize_item(item)
        if cat in grouped:
            grouped[cat].append(item)
        else:
            grouped.setdefault("Social & Community", []).append(item)

    # Sort each category by score
    for cat in grouped:
        grouped[cat].sort(key=lambda x: x.score, reverse=True)

    return grouped
