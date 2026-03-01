"""Product Hunt top products via official GraphQL API."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dateutil import parser as dateutil_parser

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

GRAPHQL_ENDPOINT = "https://api.producthunt.com/v2/api/graphql"

QUERY = """
query GetTopProducts($after: DateTime, $before: DateTime, $first: Int!) {
  posts(order: VOTES, postedAfter: $after, postedBefore: $before, first: $first) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        votesCount
        createdAt
        topics { edges { node { name } } }
        user { name }
        thumbnail { url }
      }
    }
  }
}
"""

_PACIFIC = ZoneInfo("America/Los_Angeles")


class ProductHuntCollector(BaseCollector):
    """Collect top products from Product Hunt via GraphQL API.

    Uses Pacific Time (America/Los_Angeles) day boundaries to match
    Product Hunt's native day definition. Requires PRODUCTHUNT_API_TOKEN
    environment variable.
    """

    source_name = "producthunt"
    source_type = SourceType.API

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.token = os.environ.get("PRODUCTHUNT_API_TOKEN", "")

    def _get_ph_day_range(self) -> tuple[str, str]:
        """Return ISO8601 boundaries for the current Pacific Time calendar day."""
        now_pt = datetime.now(_PACIFIC)
        today_start = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)
        return today_start.isoformat(), tomorrow_start.isoformat()

    async def collect(self) -> list[ContentItem]:
        if not self.token:
            logger.warning("[producthunt] PRODUCTHUNT_API_TOKEN not set — skipping")
            return []

        after, before = self._get_ph_day_range()
        variables = {"after": after, "before": before, "first": self.max_items}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await self._request_with_retry(
                client,
                GRAPHQL_ENDPOINT,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json={"query": QUERY, "variables": variables},
            )
            resp.raise_for_status()
            data = resp.json()

        edges = data.get("data", {}).get("posts", {}).get("edges", [])
        items = []
        for edge in edges:
            item = self._parse_post(edge.get("node", {}))
            if item is not None:
                items.append(item)
        return items

    def _parse_post(self, node: dict[str, Any]) -> ContentItem | None:
        """Convert a GraphQL post node to a ContentItem."""
        if not node.get("name"):
            return None

        topics = [
            e["node"]["name"]
            for e in node.get("topics", {}).get("edges", [])
            if e.get("node", {}).get("name")
        ]

        created_at_str = node.get("createdAt", "")
        if created_at_str:
            published_at = dateutil_parser.parse(created_at_str)
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
        else:
            published_at = datetime.now(timezone.utc)

        return ContentItem(
            source="producthunt",
            source_type=SourceType.API,
            title=node["name"],
            url=node.get("url", ""),
            author=node.get("user", {}).get("name", ""),
            content=node.get("description") or node.get("tagline", ""),
            published_at=published_at,
            score=float(node.get("votesCount", 0)),
            tags=topics,
            extra={
                "tagline": node.get("tagline", ""),
                "thumbnail": (node.get("thumbnail") or {}).get("url", ""),
                "ph_id": node.get("id", ""),
            },
        )
