"""Tests for report/push.py — RSS Worker push dedup."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from schema import DigestReport


@pytest.fixture
def rss_config():
    return {
        "distribution": {
            "rss_worker": {
                "enabled": True,
                "url": "https://rss-worker.example.com",
            }
        }
    }


def _make_report(date: str, generated_at: datetime) -> DigestReport:
    return DigestReport(
        date=date,
        generated_at=generated_at,
        full_markdown="# Test digest",
        items_count=5,
        sources_count=3,
    )


class TestRSSWorkerPushDedup:
    """Verify same-day multiple pushes produce unique link/title."""

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"RSS_API_KEY": "test-key"})
    async def test_same_day_different_times_produce_unique_links(self, rss_config):
        """Core bug test: two pushes on the same day must have different links."""
        from report.push import push_to_rss_worker

        captured_payloads = []

        async def mock_post(url, json=None, headers=None):
            captured_payloads.append(json)
            resp = httpx.Response(201, json={"action": "created"})
            return resp

        report_morning = _make_report(
            "2026-02-28",
            datetime(2026, 2, 28, 6, 30, 0, tzinfo=timezone.utc),
        )
        report_evening = _make_report(
            "2026-02-28",
            datetime(2026, 2, 28, 18, 0, 0, tzinfo=timezone.utc),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await push_to_rss_worker(report_morning, rss_config)
            await push_to_rss_worker(report_evening, rss_config)

        assert len(captured_payloads) == 2
        link_morning = captured_payloads[0]["link"]
        link_evening = captured_payloads[1]["link"]
        assert link_morning != link_evening, (
            f"Same-day pushes must have unique links, got: {link_morning}"
        )

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"RSS_API_KEY": "test-key"})
    async def test_link_contains_date(self, rss_config):
        """Link should still contain the date for readability."""
        from report.push import push_to_rss_worker

        captured = []

        async def mock_post(url, json=None, headers=None):
            captured.append(json)
            return httpx.Response(201, json={"action": "created"})

        report = _make_report(
            "2026-02-28",
            datetime(2026, 2, 28, 10, 0, 0, tzinfo=timezone.utc),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await push_to_rss_worker(report, rss_config)

        assert "2026-02-28" in captured[0]["link"]

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"RSS_API_KEY": "test-key"})
    async def test_title_distinguishes_same_day_pushes(self, rss_config):
        """Same-day pushes should have distinguishable titles."""
        from report.push import push_to_rss_worker

        captured = []

        async def mock_post(url, json=None, headers=None):
            captured.append(json)
            return httpx.Response(201, json={"action": "created"})

        report_1 = _make_report(
            "2026-02-28",
            datetime(2026, 2, 28, 6, 30, 0, tzinfo=timezone.utc),
        )
        report_2 = _make_report(
            "2026-02-28",
            datetime(2026, 2, 28, 18, 0, 0, tzinfo=timezone.utc),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await push_to_rss_worker(report_1, rss_config)
            await push_to_rss_worker(report_2, rss_config)

        title_1 = captured[0]["title"]
        title_2 = captured[1]["title"]
        assert title_1 != title_2, (
            f"Same-day pushes must have unique titles, got: {title_1}"
        )
