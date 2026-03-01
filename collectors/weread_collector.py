"""WeRead (微信读书) collector via Browser Relay."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

BROWSER_CDP_URL = "http://localhost:18792"
WEREAD_URL = "https://weread.qq.com"


class WeReadCollector(BaseCollector):
    """Collect reading recommendations from WeRead."""
    source_name = "weread"
    source_type = SourceType.BROWSER_RELAY

    async def collect(self) -> list[ContentItem]:
        """Fetch recommended books from WeRead discovery page.

        NOTE: Full implementation requires browser automation via CDP.
        This method provides the interface.
        """
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                resp = await self._request_with_retry(
                    client, f"{BROWSER_CDP_URL}/json", max_retries=1,
                )
                resp.raise_for_status()
            except httpx.HTTPError:
                logger.warning("[weread] Browser relay not available")
                return []

        logger.info("[weread] Browser relay connected, fetching recommendations")
        # Placeholder: browser relay integration for WeRead
        return []
