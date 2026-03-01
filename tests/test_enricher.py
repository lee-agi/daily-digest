"""Tests for collectors.enricher — content enrichment via any2summary."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collectors.enricher import ContentEnricher, EnrichmentConfig
from schema import ContentItem, SourceType


# ---------- fixtures ----------

@pytest.fixture
def enricher() -> ContentEnricher:
    cfg = EnrichmentConfig(
        enabled=True,
        timeout_seconds=30,
        large_download_minutes=30,
        content_max_chars=500,
        article_auto_threshold=5000,
        youtube_top_n=3,
        podcast_top_n=2,
        article_top_n=5,
    )
    return ContentEnricher(cfg)


def _make_item(
    source: str = "youtube",
    source_type: SourceType = SourceType.RSS,
    title: str = "Test",
    url: str = "https://example.com/test",
    content: str = "",
    score: float = 0.0,
    tags: list[str] | None = None,
    extra: dict | None = None,
) -> ContentItem:
    return ContentItem(
        source=source,
        source_type=source_type,
        title=title,
        url=url,
        content=content,
        score=score,
        tags=tags or [],
        extra=extra or {},
    )


# ---------- parse_duration_seconds ----------

class TestParseDuration:
    def test_hms(self):
        assert ContentEnricher.parse_duration_seconds("01:23:45") == 5025

    def test_ms(self):
        assert ContentEnricher.parse_duration_seconds("23:45") == 1425

    def test_plain_seconds(self):
        assert ContentEnricher.parse_duration_seconds("3600") == 3600

    def test_empty(self):
        assert ContentEnricher.parse_duration_seconds("") == 0

    def test_none(self):
        assert ContentEnricher.parse_duration_seconds(None) == 0

    def test_invalid(self):
        assert ContentEnricher.parse_duration_seconds("abc") == 0

    def test_zero_padded(self):
        assert ContentEnricher.parse_duration_seconds("00:05:00") == 300


# ---------- is_large_download ----------

class TestIsLargeDownload:
    def test_over_threshold(self, enricher: ContentEnricher):
        item = _make_item(extra={"duration": "01:00:00"})
        assert enricher.is_large_download(item) is True

    def test_at_threshold(self, enricher: ContentEnricher):
        item = _make_item(extra={"duration": "30:00"})
        assert enricher.is_large_download(item) is False

    def test_under_threshold(self, enricher: ContentEnricher):
        item = _make_item(extra={"duration": "10:00"})
        assert enricher.is_large_download(item) is False

    def test_no_duration(self, enricher: ContentEnricher):
        item = _make_item(extra={})
        assert enricher.is_large_download(item) is False


# ---------- calculate_engagement_rate ----------

class TestEngagementRate:
    def test_with_youtube_stats(self):
        item = _make_item(
            score=1000.0,
            extra={
                "youtube_stats": {
                    "viewCount": 10000,
                    "likeCount": 500,
                    "commentCount": 100,
                }
            },
        )
        rate = ContentEnricher.calculate_engagement_rate(item)
        assert rate == pytest.approx(0.06)

    def test_without_stats_uses_score(self):
        item = _make_item(score=5000.0)
        rate = ContentEnricher.calculate_engagement_rate(item)
        assert rate == 5000.0

    def test_zero_views(self):
        item = _make_item(
            extra={"youtube_stats": {"viewCount": 0, "likeCount": 10, "commentCount": 5}}
        )
        rate = ContentEnricher.calculate_engagement_rate(item)
        assert rate == 15.0  # (10+5) / max(0,1) = 15


# ---------- enrich_item ----------

class TestEnrichItem:
    def test_success(self, enricher: ContentEnricher):
        item = _make_item(content="short")
        mock_data = {
            "summary": "This is a rich summary of the content " * 5,
            "total_words": 1000,
        }
        with patch.object(enricher, "call_any2summary", return_value=mock_data):
            result = enricher.enrich_item(item)
        assert result is True
        assert item.extra["enriched"] is True
        assert len(item.content) > 0
        assert len(item.content) <= enricher.config.content_max_chars

    def test_timeout_graceful(self, enricher: ContentEnricher):
        item = _make_item(content="original content")
        with patch.object(enricher, "call_any2summary", return_value=None):
            result = enricher.enrich_item(item)
        assert result is False
        assert item.content == "original content"

    def test_invalid_json_graceful(self, enricher: ContentEnricher):
        item = _make_item(content="original")
        with patch.object(enricher, "call_any2summary", return_value=None):
            result = enricher.enrich_item(item)
        assert result is False

    def test_empty_url(self, enricher: ContentEnricher):
        item = _make_item(url="")
        result = enricher.enrich_item(item)
        assert result is False

    def test_summary_from_path(self, enricher: ContentEnricher, tmp_path: Path):
        summary_file = tmp_path / "summary.md"
        summary_file.write_text("# Full article summary\nGreat content here.")
        mock_data = {"summary_path": str(summary_file)}
        item = _make_item()
        with patch.object(enricher, "call_any2summary", return_value=mock_data):
            result = enricher.enrich_item(item)
        assert result is True
        assert "Full article summary" in item.content

    def test_summary_from_segments(self, enricher: ContentEnricher):
        mock_data = {
            "segments": [
                {"text": "First segment."},
                {"text": "Second segment."},
            ]
        }
        item = _make_item()
        with patch.object(enricher, "call_any2summary", return_value=mock_data):
            result = enricher.enrich_item(item)
        assert result is True
        assert "First segment" in item.content


# ---------- call_any2summary (subprocess mock) ----------

class TestCallAny2Summary:
    def test_successful_call(self, enricher: ContentEnricher):
        payload = {"summary": "Hello world summary " * 10, "total_words": 50}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(payload)

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = enricher.call_any2summary("https://example.com/article")

        assert result is not None
        assert result["summary"] == payload["summary"]
        mock_run.assert_called_once()

    def test_timeout(self, enricher: ContentEnricher):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
            result = enricher.call_any2summary("https://example.com")
        assert result is None

    def test_non_zero_exit(self, enricher: ContentEnricher):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Some error"
        with patch("subprocess.run", return_value=mock_result):
            result = enricher.call_any2summary("https://example.com")
        assert result is None

    def test_invalid_json_output(self, enricher: ContentEnricher):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json at all"
        with patch("subprocess.run", return_value=mock_result):
            result = enricher.call_any2summary("https://example.com")
        assert result is None


# ---------- select_youtube_items ----------

class TestSelectYouTubeItems:
    def test_sort_by_engagement(self, enricher: ContentEnricher):
        items = [
            _make_item(
                title=f"Video {i}", score=float(i * 100),
                extra={"youtube_stats": {
                    "viewCount": 10000,
                    "likeCount": i * 100,
                    "commentCount": 0,
                }},
            )
            for i in range(5)
        ]
        enrichable, _ = enricher.select_youtube_items(items)
        assert len(enrichable) == 3  # top_n = 3
        # Highest engagement first
        assert enrichable[0].title == "Video 4"

    def test_viewcount_fallback(self, enricher: ContentEnricher):
        """Without youtube_stats, falls back to score (viewCount)."""
        items = [
            _make_item(title="A", score=500.0),
            _make_item(title="B", score=10000.0),
            _make_item(title="C", score=1000.0),
        ]
        enrichable, _ = enricher.select_youtube_items(items)
        assert enrichable[0].title == "B"

    def test_non_youtube_excluded(self, enricher: ContentEnricher):
        items = [_make_item(source="reddit")]
        enrichable, _ = enricher.select_youtube_items(items)
        assert len(enrichable) == 0


# ---------- select_podcast_items ----------

class TestSelectPodcastItems:
    def test_short_podcasts_enrichable(self, enricher: ContentEnricher):
        items = [
            _make_item(
                source="apple_podcast", tags=["podcast"],
                extra={"duration": "20:00"},
            ),
            _make_item(
                source="xiaoyuzhou", tags=["podcast"],
                extra={"duration": "15:00"},
            ),
        ]
        enrichable, manual = enricher.select_podcast_items(items)
        assert len(enrichable) == 2
        assert len(manual) == 0

    def test_long_podcast_manual(self, enricher: ContentEnricher):
        items = [
            _make_item(
                source="apple_podcast", tags=["podcast"],
                extra={"duration": "01:30:00"},
            ),
        ]
        enrichable, manual = enricher.select_podcast_items(items)
        assert len(enrichable) == 0
        assert len(manual) == 1

    def test_mixed(self, enricher: ContentEnricher):
        items = [
            _make_item(
                source="apple_podcast", tags=["podcast"],
                extra={"duration": "25:00"},
            ),
            _make_item(
                source="apple_podcast", tags=["podcast"],
                extra={"duration": "45:00"},
            ),
            _make_item(
                source="xiaoyuzhou", tags=["podcast"],
                extra={"duration": "10:00"},
            ),
        ]
        enrichable, manual = enricher.select_podcast_items(items)
        assert len(enrichable) == 2  # top_n = 2
        assert len(manual) == 1


# ---------- select_article_items ----------

class TestSelectArticleItems:
    def test_short_articles_all_selected(self, enricher: ContentEnricher):
        items = [
            _make_item(source="anthropic", source_type=SourceType.HTTP_SCRAPE, content="x" * 100),
            _make_item(source="openai", source_type=SourceType.RSS, content="y" * 200),
        ]
        selected = enricher.select_article_items(items)
        assert len(selected) == 2

    def test_long_articles_by_score(self, enricher: ContentEnricher):
        items = [
            _make_item(
                source="anthropic", source_type=SourceType.HTTP_SCRAPE,
                content="x" * 6000, score=10.0,
            ),
            _make_item(
                source="openai", source_type=SourceType.RSS,
                content="y" * 6000, score=50.0,
            ),
        ]
        selected = enricher.select_article_items(items)
        assert len(selected) == 2
        # Score 50 should come after auto-enriched ones (there are none)
        assert selected[0].score == 50.0

    def test_non_article_excluded(self, enricher: ContentEnricher):
        items = [_make_item(source="youtube", source_type=SourceType.RSS)]
        selected = enricher.select_article_items(items)
        assert len(selected) == 0


# ---------- process_batch ----------

class TestProcessBatch:
    def test_mixed_sources(self, enricher: ContentEnricher):
        items = [
            _make_item(source="anthropic", source_type=SourceType.HTTP_SCRAPE, content="short"),
            _make_item(source="youtube", score=1000.0, tags=["video"]),
            _make_item(
                source="apple_podcast", tags=["podcast"],
                extra={"duration": "01:00:00"},
            ),
        ]
        with patch.object(enricher, "enrich_item", return_value=True):
            updated, manual = enricher.process_batch(items)

        assert len(updated) == 3  # all items returned
        assert len(manual) == 1  # long podcast

    def test_circuit_breaker_skips_after_consecutive_failures(
        self, enricher: ContentEnricher,
    ):
        """After MAX_CONSECUTIVE_FAILURES consecutive failures, skip remaining items."""
        # Create 6 article items — first 3 fail, should skip remaining 3
        items = [
            _make_item(
                source="anthropic",
                source_type=SourceType.HTTP_SCRAPE,
                title=f"Article {i}",
                url=f"https://example.com/article-{i}",
                content="short",
            )
            for i in range(6)
        ]

        call_count = 0

        def fake_enrich(item: ContentItem) -> bool:
            nonlocal call_count
            call_count += 1
            return False  # Always fail

        with patch.object(enricher, "enrich_item", side_effect=fake_enrich):
            enricher.process_batch(items)

        # Circuit breaker triggers after 3 consecutive failures,
        # so only 3 items should be attempted (not all 6)
        assert call_count == enricher.MAX_CONSECUTIVE_FAILURES

    def test_circuit_breaker_resets_on_success(
        self, enricher: ContentEnricher,
    ):
        """A success resets the consecutive failure counter."""
        # 7 article items: fail, fail, success, fail, fail, fail, (skipped)
        items = [
            _make_item(
                source="anthropic",
                source_type=SourceType.HTTP_SCRAPE,
                title=f"Article {i}",
                url=f"https://example.com/article-{i}",
                content="short",
            )
            for i in range(7)
        ]

        results = [False, False, True, False, False, False, False]
        call_idx = 0

        def fake_enrich(item: ContentItem) -> bool:
            nonlocal call_idx
            idx = call_idx
            call_idx += 1
            return results[idx]

        with patch.object(enricher, "enrich_item", side_effect=fake_enrich):
            enricher.process_batch(items)

        # fail, fail, success(reset), fail, fail, fail(breaker triggers), skip last
        assert call_idx == 6


# ---------- EnrichmentConfig.from_config ----------

class TestEnrichmentConfigFromConfig:
    def test_defaults(self):
        cfg = EnrichmentConfig.from_config({"enabled": True})
        assert cfg.enabled is True
        assert cfg.timeout_seconds == 180
        assert cfg.youtube_top_n == 5

    def test_custom_values(self):
        cfg = EnrichmentConfig.from_config({
            "enabled": True,
            "timeout_seconds": 60,
            "sources": {
                "youtube": {"top_n": 10},
                "articles": {"top_n": 20},
            },
        })
        assert cfg.timeout_seconds == 60
        assert cfg.youtube_top_n == 10
        assert cfg.article_top_n == 20
