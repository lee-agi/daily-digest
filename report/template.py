"""Markdown report template utilities."""

from __future__ import annotations

from schema import ContentItem


def format_item_markdown(item: ContentItem) -> str:
    """Format a single item as markdown list entry."""
    parts = [f"**{item.title}**"]
    if item.author:
        parts.append(f"by {item.author}")
    if item.url:
        parts.append(f"[link]({item.url})")
    if item.score > 0:
        parts.append(f"({item.score:.0f} pts)")
    return " | ".join(parts)


def format_category_section(
    category: str,
    items: list[ContentItem],
    max_items: int = 20,
) -> str:
    """Format a category section with items."""
    if not items:
        return ""

    lines = [f"## {category}\n"]
    for item in items[:max_items]:
        lines.append(f"- {format_item_markdown(item)}")
        if item.content:
            # Show first 200 chars of content as preview
            preview = item.content[:200].replace("\n", " ")
            if len(item.content) > 200:
                preview += "..."
            lines.append(f"  > {preview}")
        lines.append("")

    return "\n".join(lines)
