"""Tests for schema data models."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from schema import CollectorResult, ContentItem, DigestReport, SourceType


class TestContentItem:
    def test_create_basic_item(self):
        item = ContentItem(
            source="github",
            source_type=SourceType.API,
            title="Test repo release v1.0",
            url="https://github.com/test/repo/releases/v1.0",
            score=42.0,
        )
        assert item.source == "github"
        assert item.title == "Test repo release v1.0"
        assert item.score == 42.0
        assert item.content_hash  # Should be auto-computed

    def test_content_hash_url_based(self):
        item1 = ContentItem(
            source="github",
            source_type=SourceType.API,
            title="Release v1.0",
            url="https://github.com/test/repo",
        )
        item2 = ContentItem(
            source="reddit",
            source_type=SourceType.API,
            title="Different title",
            url="https://github.com/test/repo",
        )
        # Same URL => same hash
        assert item1.content_hash == item2.content_hash

    def test_content_hash_title_based(self):
        item1 = ContentItem(
            source="zhihu",
            source_type=SourceType.RSS,
            title="Same title here",
        )
        item2 = ContentItem(
            source="zhihu",
            source_type=SourceType.RSS,
            title="Same title here",
        )
        assert item1.content_hash == item2.content_hash

    def test_content_hash_different_no_url(self):
        item1 = ContentItem(
            source="zhihu",
            source_type=SourceType.RSS,
            title="Title A",
        )
        item2 = ContentItem(
            source="jike",
            source_type=SourceType.RSS,
            title="Title A",
        )
        # Different source + same title => different hash (source:title)
        assert item1.content_hash != item2.content_hash

    def test_arxiv_id_field(self):
        item = ContentItem(
            source="huggingface",
            source_type=SourceType.HTTP_SCRAPE,
            title="New paper",
            arxiv_id="2401.12345",
        )
        assert item.arxiv_id == "2401.12345"

    def test_extra_metadata(self):
        item = ContentItem(
            source="reddit",
            source_type=SourceType.API,
            title="Test",
            extra={"subreddit": "LocalLLaMA", "num_comments": 100},
        )
        assert item.extra["subreddit"] == "LocalLLaMA"

    def test_serialization_roundtrip(self):
        item = ContentItem(
            source="github",
            source_type=SourceType.API,
            title="Test",
            url="https://example.com",
            score=10.0,
            tags=["ai", "ml"],
        )
        data = item.model_dump(mode="json")
        restored = ContentItem.model_validate(data)
        assert restored.title == item.title
        assert restored.content_hash == item.content_hash


class TestCollectorResult:
    def test_success_result(self):
        result = CollectorResult(
            source="github",
            success=True,
            items=[
                ContentItem(source="github", source_type=SourceType.API, title="item1"),
            ],
            raw_count=5,
            filtered_count=1,
            duration_seconds=1.23,
        )
        assert result.success
        assert len(result.items) == 1
        assert result.raw_count == 5

    def test_failure_result(self):
        result = CollectorResult(
            source="reddit",
            success=False,
            error="Connection timeout",
            duration_seconds=30.0,
        )
        assert not result.success
        assert "timeout" in result.error.lower()


class TestDigestReport:
    def test_create_report(self):
        report = DigestReport(
            date="2026-02-23",
            full_markdown="# Test Report\n\nContent here",
            items_count=50,
            sources_count=5,
        )
        assert report.date == "2026-02-23"
        assert report.items_count == 50
