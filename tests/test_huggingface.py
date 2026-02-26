"""Tests for HuggingFace Daily Papers collector.

Verifies that the collector correctly uses submittedOnDailyAt for time filtering
and extracts upvotes from the API response.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

# Ensure project root is importable
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from collectors.huggingface_papers import HuggingFacePapersCollector


@pytest.fixture
def hf_config():
    return {
        "enabled": True,
        "source_type": "http_scrape",
        "score_threshold": 0,
        "lookback_hours": 24,
        "max_items": 50,
    }


class TestHuggingFaceDateParsing:
    """submittedOnDailyAt should be used for cutoff filtering, not publishedAt."""

    def test_parse_paper_uses_submitted_on_daily_date(self, hf_config):
        """Papers submitted today but published weeks ago should be included."""
        collector = HuggingFacePapersCollector(hf_config)
        now = datetime.now(timezone.utc)

        paper_data = {
            "publishedAt": "2026-01-01T00:00:00.000Z",  # Old ArXiv date
            "paper": {
                "id": "2602.12345",
                "title": "Test Paper",
                "summary": "A test paper",
                "authors": [{"name": "Author A"}],
                "publishedAt": "2026-01-01T00:00:00.000Z",
                "submittedOnDailyAt": now.isoformat(),  # Submitted today
                "upvotes": 42,
            },
        }

        item = collector._parse_paper(paper_data)
        assert item is not None
        # The collected item's published_at should reflect submittedOnDailyAt
        # so it passes the cutoff_time filter in collect()
        assert item.published_at >= collector.cutoff_time, (
            f"Item published_at {item.published_at} should be >= cutoff {collector.cutoff_time}"
        )

    def test_parse_paper_old_submission_filtered_out(self, hf_config):
        """Papers submitted more than lookback_hours ago should be filtered."""
        collector = HuggingFacePapersCollector(hf_config)
        old_date = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()

        paper_data = {
            "publishedAt": "2026-01-01T00:00:00.000Z",
            "paper": {
                "id": "2602.00001",
                "title": "Old Paper",
                "summary": "Old",
                "authors": [],
                "publishedAt": "2026-01-01T00:00:00.000Z",
                "submittedOnDailyAt": old_date,
                "upvotes": 100,
            },
        }

        item = collector._parse_paper(paper_data)
        assert item is not None
        assert item.published_at < collector.cutoff_time


class TestHuggingFaceUpvotes:
    """Upvote extraction should find the correct field."""

    def test_upvotes_from_paper_field(self, hf_config):
        collector = HuggingFacePapersCollector(hf_config)
        paper_data = {
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "submittedOnDailyAt": datetime.now(timezone.utc).isoformat(),
            "paper": {
                "id": "2602.99999",
                "title": "Popular Paper",
                "summary": "Very popular",
                "authors": [],
                "publishedAt": datetime.now(timezone.utc).isoformat(),
                "upvotes": 150,
            },
        }
        item = collector._parse_paper(paper_data)
        assert item is not None
        assert item.score == 150.0


class TestHuggingFaceLiveAPI:
    """Integration test: actually hit the HuggingFace API."""

    @pytest.mark.asyncio
    async def test_live_collect_returns_items(self, hf_config):
        """With threshold=0, we should get some papers from the live API."""
        collector = HuggingFacePapersCollector(hf_config)
        result = await collector.run()
        assert result.success is True
        assert result.raw_count > 0, "HuggingFace daily papers API should return papers"
