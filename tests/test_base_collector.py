"""Tests for BaseCollector and CollectorRegistry."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
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


class TestRequestWithRetry:
    """Tests for BaseCollector._request_with_retry()."""

    def _make_response(self, status_code: int) -> httpx.Response:
        """Create a mock httpx.Response with the given status code."""
        request = httpx.Request("GET", "https://example.com/test")
        return httpx.Response(status_code=status_code, request=request)

    @pytest.mark.asyncio
    async def test_retry_on_500(self):
        """500 on first attempt, 200 on second → should succeed."""
        collector = MockCollector({"score_threshold": 0})
        resp_500 = self._make_response(500)
        resp_200 = self._make_response(200)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(side_effect=[resp_500, resp_200])

        result = await collector._request_with_retry(
            client, "https://example.com/test", base_delay=0.01,
        )
        assert result.status_code == 200
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_429(self):
        """429 on first attempt, 200 on second → should succeed."""
        collector = MockCollector({"score_threshold": 0})
        resp_429 = self._make_response(429)
        resp_200 = self._make_response(200)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(side_effect=[resp_429, resp_200])

        result = await collector._request_with_retry(
            client, "https://example.com/test", base_delay=0.01,
        )
        assert result.status_code == 200
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self):
        """TimeoutException on first attempt, 200 on second → should succeed."""
        collector = MockCollector({"score_threshold": 0})
        resp_200 = self._make_response(200)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(side_effect=[
            httpx.TimeoutException("timed out"),
            resp_200,
        ])

        result = await collector._request_with_retry(
            client, "https://example.com/test", base_delay=0.01,
        )
        assert result.status_code == 200
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_401(self):
        """401 should raise immediately without retrying."""
        collector = MockCollector({"score_threshold": 0})
        resp_401 = self._make_response(401)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(return_value=resp_401)

        # 401 is a 4xx (not 429), so it should return the response directly
        # (the caller is responsible for calling raise_for_status if needed)
        result = await collector._request_with_retry(
            client, "https://example.com/test", base_delay=0.01,
        )
        assert result.status_code == 401
        assert client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_remote_protocol_error(self):
        """RemoteProtocolError on first attempt, 200 on second → should succeed."""
        collector = MockCollector({"score_threshold": 0})
        resp_200 = self._make_response(200)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(side_effect=[
            httpx.RemoteProtocolError("incomplete chunked read"),
            resp_200,
        ])

        result = await collector._request_with_retry(
            client, "https://example.com/test", base_delay=0.01,
        )
        assert result.status_code == 200
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_connect_error(self):
        """ConnectError on first attempt, 200 on second → should succeed."""
        collector = MockCollector({"score_threshold": 0})
        resp_200 = self._make_response(200)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(side_effect=[
            httpx.ConnectError("connection refused"),
            resp_200,
        ])

        result = await collector._request_with_retry(
            client, "https://example.com/test", base_delay=0.01,
        )
        assert result.status_code == 200
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_read_error(self):
        """ReadError on first attempt, 200 on second → should succeed."""
        collector = MockCollector({"score_threshold": 0})
        resp_200 = self._make_response(200)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(side_effect=[
            httpx.ReadError("read error"),
            resp_200,
        ])

        result = await collector._request_with_retry(
            client, "https://example.com/test", base_delay=0.01,
        )
        assert result.status_code == 200
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_transport_error_retry_exhausted(self):
        """TransportError retries exhausted → should raise."""
        collector = MockCollector({"score_threshold": 0})

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(side_effect=httpx.RemoteProtocolError(
            "incomplete chunked read",
        ))

        with pytest.raises(httpx.RemoteProtocolError):
            await collector._request_with_retry(
                client, "https://example.com/test",
                max_retries=3, base_delay=0.01,
            )
        assert client.request.call_count == 4

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        """4 consecutive 500s (max_retries=3) should raise HTTPStatusError."""
        collector = MockCollector({"score_threshold": 0})
        responses = [self._make_response(500) for _ in range(4)]

        client = AsyncMock(spec=httpx.AsyncClient)
        client.request = AsyncMock(side_effect=responses)

        with pytest.raises(httpx.HTTPStatusError):
            await collector._request_with_retry(
                client, "https://example.com/test",
                max_retries=3, base_delay=0.01,
            )
        assert client.request.call_count == 4
