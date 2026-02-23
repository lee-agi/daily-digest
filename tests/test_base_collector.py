"""Tests for BaseCollector and CollectorRegistry."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.base import BaseCollector, CollectorRegistry
from schema import ContentItem, SourceType


# Test collector implementation
class MockCollector(BaseCollector):
    source_name = "mock_test"
    source_type = SourceType.API

    def __init__(self, config):
        super().__init__(config)
        self._items = config.get("_test_items", [])

    async def collect(self) -> list[ContentItem]:
        return self._items


class FailingCollector(BaseCollector):
    source_name = "failing_test"
    source_type = SourceType.API

    async def collect(self) -> list[ContentItem]:
        raise ConnectionError("Simulated network failure")


class TestCollectorRegistry:
    def test_auto_registration(self):
        assert CollectorRegistry.get("mock_test") is MockCollector
        assert "mock_test" in CollectorRegistry.all_names()

    def test_create_from_config(self):
        config = {
            "sources": {
                "mock_test": {"enabled": True, "score_threshold": 10},
                "nonexistent": {"enabled": True},
                "failing_test": {"enabled": False},
            }
        }
        instances = CollectorRegistry.create_all(config)
        names = [c.source_name for c in instances]
        assert "mock_test" in names
        assert "nonexistent" not in names  # No registered collector
        assert "failing_test" not in names  # Disabled


class TestBaseCollector:
    @pytest.mark.asyncio
    async def test_run_success(self):
        items = [
            ContentItem(
                source="mock_test",
                source_type=SourceType.API,
                title=f"Item {i}",
                score=float(i * 10),
            )
            for i in range(5)
        ]
        collector = MockCollector({"score_threshold": 15, "_test_items": items})
        result = await collector.run()
        assert result.success
        assert result.raw_count == 5
        # Items with score < 15 are filtered: only 20, 30, 40 pass
        assert result.filtered_count == 3
        # Sorted by score desc
        assert result.items[0].score == 40.0

    @pytest.mark.asyncio
    async def test_run_failure(self):
        collector = FailingCollector({"score_threshold": 0})
        result = await collector.run()
        assert not result.success
        assert "network failure" in result.error.lower()
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_max_items_limit(self):
        items = [
            ContentItem(
                source="mock_test",
                source_type=SourceType.API,
                title=f"Item {i}",
                score=float(i),
            )
            for i in range(50)
        ]
        collector = MockCollector({
            "score_threshold": 0,
            "max_items": 10,
            "_test_items": items,
        })
        result = await collector.run()
        assert result.filtered_count == 10
        assert result.items[0].score == 49.0  # Highest score first

    def test_cutoff_time(self):
        collector = MockCollector({"lookback_hours": 12})
        cutoff = collector.cutoff_time
        expected = datetime.now(timezone.utc) - timedelta(hours=12)
        # Within 1 second tolerance
        assert abs((cutoff - expected).total_seconds()) < 1
