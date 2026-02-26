"""Ad content detection and filtering module.

Three-layer filtering:
1. Keyword matching (title + content) — global defaults + per-source extensions
2. URL domain blocklist (item.url + links in content)
3. Per-source rules (e.g. Reddit upvote_ratio, YouTube #ad tags)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from schema import ContentItem

logger = logging.getLogger(__name__)

# Global default ad keyword patterns (case-insensitive)
GLOBAL_AD_KEYWORDS: list[str] = [
    # English
    r"\b(sponsored|promoted|advertisement)\b",
    r"#ad\b|#sponsored\b",
    r"\b(affiliate link|use my code|discount code|promo code)\b",
    r"\b(buy now|limited offer|exclusive deal|check out my)\b",
    # Chinese
    r"(广告|推广|赞助|优惠券|折扣码|限时优惠|种草|带货|恰饭)",
]

# URL patterns indicating affiliate/ad/e-commerce links
# Use :// or dot-boundary to avoid matching substrings (e.g. t.co in reddit.com)
BLOCKED_URL_PATTERNS: list[str] = [
    r"://(bit\.ly|tinyurl\.com|amzn\.to|shrinkme\.io)/",
    r"://t\.co/",
    r"://(www\.)?(shopify\.com|gumroad\.com|etsy\.com)/",
]


class AdFilter:
    """Rule-based ad content filter.

    Loads configuration from the `ad_filter` section of config.yaml.
    When disabled, all items pass through unchanged.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.enabled: bool = config.get("enabled", True)
        if not self.enabled:
            return

        # Build compiled keyword patterns
        extra_title_kw = config.get("title_keywords", [])
        extra_content_kw = config.get("content_keywords", [])
        all_keywords = GLOBAL_AD_KEYWORDS + extra_title_kw + extra_content_kw
        self._keyword_patterns: list[re.Pattern[str]] = [
            re.compile(p, re.IGNORECASE) for p in all_keywords
        ]

        # Build compiled URL blocklist patterns
        extra_urls = config.get("url_blocklist", [])
        all_url_patterns = BLOCKED_URL_PATTERNS + extra_urls
        self._url_patterns: list[re.Pattern[str]] = [
            re.compile(p, re.IGNORECASE) for p in all_url_patterns
        ]

        # Per-source configs
        self._per_source: dict[str, dict[str, Any]] = config.get("per_source", {})

    def filter(
        self, items: list[ContentItem]
    ) -> tuple[list[ContentItem], list[ContentItem]]:
        """Filter items, returning (clean_items, blocked_items)."""
        if not self.enabled:
            return items, []

        clean: list[ContentItem] = []
        blocked: list[ContentItem] = []
        for item in items:
            is_ad, reason = self.is_ad(item)
            if is_ad:
                logger.debug(
                    "Blocked ad [%s]: %s — %s", item.source, item.title[:60], reason
                )
                blocked.append(item)
            else:
                clean.append(item)
        return clean, blocked

    def is_ad(self, item: ContentItem) -> tuple[bool, str]:
        """Check if a single item is an ad.

        Returns:
            (is_ad, reason) — reason is empty string if not an ad.
        """
        if not self.enabled:
            return False, ""

        # Layer 1: Keyword matching on title + content
        text = f"{item.title} {item.content}"
        for pattern in self._keyword_patterns:
            match = pattern.search(text)
            if match:
                return True, f"keyword_match: {match.group()}"

        # Layer 2: URL blocklist on item.url
        if item.url:
            for pattern in self._url_patterns:
                match = pattern.search(item.url)
                if match:
                    return True, f"blocked_url: {match.group()}"

        # Layer 3: Per-source rules
        source_cfg = self._per_source.get(item.source, {})
        if source_cfg:
            result = self._check_source_rules(item, source_cfg)
            if result[0]:
                return result

        return False, ""

    def _check_source_rules(
        self, item: ContentItem, source_cfg: dict[str, Any]
    ) -> tuple[bool, str]:
        """Apply source-specific filtering rules."""
        # Per-source extra title keywords
        extra_kw = source_cfg.get("title_keywords", [])
        for kw in extra_kw:
            if re.search(kw, item.title, re.IGNORECASE):
                return True, f"source_keyword: {kw}"

        # Reddit: low upvote_ratio filter
        if item.source == "reddit":
            min_ratio = source_cfg.get("min_upvote_ratio")
            if min_ratio is not None:
                ratio = item.extra.get("upvote_ratio", 1.0)
                if ratio < min_ratio:
                    return True, f"reddit_low_ratio: {ratio} < {min_ratio}"

        return False, ""
