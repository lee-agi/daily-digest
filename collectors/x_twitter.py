"""X/Twitter collector via Browser Relay.

This collector uses the Chrome browser extension relay to fetch tweets
from curated lists. It requires the browser to be running with the
user logged into X/Twitter.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

BROWSER_CDP_URL = "http://localhost:18792"


class XTwitterCollector(BaseCollector):
    """Collect tweets from X/Twitter lists via browser relay."""
    source_name = "x_twitter"
    source_type = SourceType.BROWSER_RELAY

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.lists = config.get("lists", {})

    async def collect(self) -> list[ContentItem]:
        """Fetch tweets from configured lists.

        This is a placeholder that requires the OpenClaw browser
        infrastructure to be running. The actual implementation
        navigates to each list URL and extracts tweet data.
        """
        if not self.lists:
            logger.info("[x_twitter] No lists configured")
            return []

        # Check if browser is available
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                resp = await client.get(f"{BROWSER_CDP_URL}/json")
                resp.raise_for_status()
            except httpx.HTTPError:
                logger.warning("[x_twitter] Browser relay not available at %s", BROWSER_CDP_URL)
                return []

        items = []
        for list_name, list_id in self.lists.items():
            list_items = await self._fetch_list(list_name, list_id)
            items.extend(list_items)

        return items

    async def _fetch_list(self, list_name: str, list_id: str) -> list[ContentItem]:
        """Fetch tweets from a single list.

        NOTE: Full implementation requires browser automation via CDP.
        This method provides the interface; actual scraping is delegated
        to the OpenClaw agent at runtime.
        """
        logger.info("[x_twitter] Fetching list %s (%s)", list_name, list_id)
        # Placeholder: browser relay integration will be added in Phase 4
        return []
