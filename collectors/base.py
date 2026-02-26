"""Base collector ABC and registry for pluggable data sources."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from aggregator.ad_filter import AdFilter
from schema import CollectorResult, ContentItem, SourceType

logger = logging.getLogger(__name__)


class CollectorRegistry:
    """Registry for auto-discovering and instantiating collectors.

    New sources only need to:
    1. Subclass BaseCollector
    2. Set `source_name` class var
    3. Import the module (auto-registered via __init_subclass__)
    """

    _registry: dict[str, type[BaseCollector]] = {}

    @classmethod
    def register(cls, collector_cls: type[BaseCollector]) -> None:
        name = collector_cls.source_name
        if name in cls._registry:
            logger.warning("Overwriting collector for source: %s", name)
        cls._registry[name] = collector_cls
        logger.debug("Registered collector: %s", name)

    @classmethod
    def get(cls, name: str) -> type[BaseCollector] | None:
        return cls._registry.get(name)

    @classmethod
    def all_names(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def create_all(cls, config: dict[str, Any]) -> list[BaseCollector]:
        """Create instances for all enabled sources in config."""
        sources_config = config.get("sources", {})
        ad_filter_cfg = config.get("ad_filter", {})
        ad_filter = AdFilter(ad_filter_cfg) if ad_filter_cfg.get("enabled", False) else None
        instances = []
        for name, src_cfg in sources_config.items():
            if not src_cfg.get("enabled", True):
                continue
            collector_cls = cls._registry.get(name)
            if collector_cls is None:
                logger.warning("No collector registered for source: %s", name)
                continue
            try:
                instances.append(collector_cls(src_cfg, ad_filter=ad_filter))
            except TypeError:
                # Older collectors without ad_filter parameter
                instance = collector_cls(src_cfg)
                instance._ad_filter = ad_filter
                instances.append(instance)
        return instances


class BaseCollector(ABC):
    """Abstract base for all data source collectors.

    Subclasses must:
    - Set `source_name` (str) and `source_type` (SourceType)
    - Implement `collect()` returning list[ContentItem]

    Provides:
    - Auto-registration via __init_subclass__
    - Configurable thresholds from config.yaml
    - Time window filtering (past 24h by default)
    - Score-based filtering with configurable threshold
    """

    source_name: ClassVar[str] = ""
    source_type: ClassVar[SourceType] = SourceType.API

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.source_name:
            CollectorRegistry.register(cls)

    def __init__(self, config: dict[str, Any], ad_filter: AdFilter | None = None) -> None:
        self.config = config
        self.score_threshold: float = config.get("score_threshold", 0)
        self.lookback_hours: int = config.get("lookback_hours", 24)
        self.max_items: int = config.get("max_items", 100)
        self._ad_filter: AdFilter | None = ad_filter

    @property
    def cutoff_time(self) -> datetime:
        """Items older than this are filtered out."""
        return datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)

    @abstractmethod
    async def collect(self) -> list[ContentItem]:
        """Fetch and return items from this source.

        Implementations should:
        - Fetch raw data from the source
        - Convert to ContentItem instances
        - Apply time window filter (use self.cutoff_time)
        - NOT apply score filter (handled by run())
        """
        ...

    async def run(self) -> CollectorResult:
        """Execute collection with error handling, timing, and filtering."""
        start = time.monotonic()
        try:
            raw_items = await self.collect()
            raw_count = len(raw_items)

            # Apply score threshold filter
            filtered = [
                item for item in raw_items
                if item.score >= self.score_threshold
            ]

            # Apply ad filter (after score threshold, before max_items)
            ad_blocked_count = 0
            if self._ad_filter is not None:
                filtered, blocked = self._ad_filter.filter(filtered)
                ad_blocked_count = len(blocked)
                if blocked:
                    logger.info(
                        "[%s] Blocked %d items as ads",
                        self.source_name, ad_blocked_count,
                    )

            # Apply max items limit (highest score first)
            filtered.sort(key=lambda x: x.score, reverse=True)
            filtered = filtered[: self.max_items]

            duration = time.monotonic() - start
            logger.info(
                "[%s] Collected %d items (raw=%d, threshold=%.0f)",
                self.source_name, len(filtered), raw_count, self.score_threshold
            )
            return CollectorResult(
                source=self.source_name,
                success=True,
                items=filtered,
                duration_seconds=round(duration, 2),
                raw_count=raw_count,
                filtered_count=len(filtered),
            )
        except Exception as e:
            duration = time.monotonic() - start
            logger.error("[%s] Collection failed: %s", self.source_name, e, exc_info=True)
            return CollectorResult(
                source=self.source_name,
                success=False,
                error=str(e),
                duration_seconds=round(duration, 2),
            )
