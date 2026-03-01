"""Tests for collectors.manual_url_collector."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from collectors.manual_url_collector import ManualURLCollector
from schema import ContentItem


# ---------- fixtures ----------

@pytest.fixture
def pending_file(tmp_path: Path) -> Path:
    return tmp_path / "pending_urls.yaml"


@pytest.fixture
def collector(pending_file: Path) -> ManualURLCollector:
    config = {
        "enabled": True,
        "max_items": 20,
        "score_threshold": 0,
        "lookback_hours": 720,
        "file": str(pending_file),
        "reminders_list": "T5T",
        "reminders_enabled": False,
    }
    return ManualURLCollector(config)


# ---------- collect: empty / no file ----------

@pytest.mark.asyncio
async def test_collect_empty_pending_yaml(collector: ManualURLCollector):
    """No pending file → returns empty list."""
    items = await collector.collect()
    assert items == []


@pytest.mark.asyncio
async def test_collect_empty_pending_list(
    collector: ManualURLCollector, pending_file: Path,
):
    """Pending file with empty pending list → returns empty."""
    pending_file.write_text(
        yaml.dump({"pending": [], "processed_archive": []}),
        encoding="utf-8",
    )
    items = await collector.collect()
    assert items == []


# ---------- collect: processes pending URLs ----------

@pytest.mark.asyncio
async def test_collect_processes_pending(
    collector: ManualURLCollector, pending_file: Path,
):
    """Pending URLs are processed via any2summary and become ContentItems."""
    pending_file.write_text(yaml.dump({
        "pending": [
            {"url": "https://example.com/article1", "added_at": "2026-02-28", "processed": False},
        ],
        "processed_archive": [],
    }), encoding="utf-8")

    mock_data = {
        "summary": "This is a rich summary of article 1. " * 5,
        "article_metadata": {"title": "Example Article 1"},
    }
    with patch.object(
        collector._enricher, "call_any2summary", return_value=mock_data,
    ):
        items = await collector.collect()

    assert len(items) == 1
    assert items[0].source == "manual_urls"
    assert items[0].score == 100.0
    assert "rich summary" in items[0].content
    assert items[0].title == "Example Article 1"

    # Check YAML was updated
    updated = yaml.safe_load(pending_file.read_text(encoding="utf-8"))
    assert len(updated["pending"]) == 0
    assert len(updated["processed_archive"]) == 1
    assert updated["processed_archive"][0]["processed"] is True


# ---------- collect: skips already processed ----------

@pytest.mark.asyncio
async def test_collect_skips_processed(
    collector: ManualURLCollector, pending_file: Path,
):
    """Already processed entries are not reprocessed."""
    pending_file.write_text(yaml.dump({
        "pending": [
            {
                "url": "https://example.com/old",
                "added_at": "2026-02-27",
                "processed": True,
                "processed_at": "2026-02-27T12:00:00Z",
            },
        ],
        "processed_archive": [],
    }), encoding="utf-8")

    items = await collector.collect()
    assert items == []


# ---------- collect: marks processed after success ----------

@pytest.mark.asyncio
async def test_marks_processed_after_collect(
    collector: ManualURLCollector, pending_file: Path,
):
    """After collection, entries move from pending to processed_archive."""
    pending_file.write_text(yaml.dump({
        "pending": [
            {"url": "https://example.com/a", "added_at": "2026-02-28", "processed": False},
            {"url": "https://example.com/b", "added_at": "2026-02-28", "processed": False},
        ],
        "processed_archive": [],
    }), encoding="utf-8")

    with patch.object(collector._enricher, "call_any2summary", return_value=None):
        items = await collector.collect()

    assert len(items) == 2  # Items created even without enrichment
    updated = yaml.safe_load(pending_file.read_text(encoding="utf-8"))
    assert len(updated["pending"]) == 0
    assert len(updated["processed_archive"]) == 2


# ---------- Reminders import ----------

class TestImportFromReminders:
    def test_osascript_success(self, collector: ManualURLCollector):
        """Parse osascript output to extract URLs."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "Check this https://openai.com/blog/gpt-5|||Some notes, "
            "Read https://arxiv.org/abs/2401.12345|||"
        )
        with patch("subprocess.run", return_value=mock_result):
            urls = collector._import_from_reminders()

        assert len(urls) == 2
        assert "https://openai.com/blog/gpt-5" in urls
        assert "https://arxiv.org/abs/2401.12345" in urls

    def test_osascript_no_list(self, collector: ManualURLCollector):
        """Missing T5T list → empty result (no crash)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            urls = collector._import_from_reminders()
        assert urls == []

    def test_osascript_not_available(self, collector: ManualURLCollector):
        """Non-macOS → FileNotFoundError → graceful empty."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            urls = collector._import_from_reminders()
        assert urls == []

    def test_osascript_timeout(self, collector: ManualURLCollector):
        """Timeout → graceful empty."""
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            urls = collector._import_from_reminders()
        assert urls == []


# ---------- URL dedup in _append_urls ----------

class TestAppendUrls:
    def test_dedup_existing_pending(self, collector: ManualURLCollector):
        data = {
            "pending": [{"url": "https://example.com/a"}],
            "processed_archive": [],
        }
        added = collector._append_urls(["https://example.com/a", "https://example.com/b"], data)
        assert added == 1
        urls = [e["url"] for e in data["pending"]]
        assert "https://example.com/b" in urls

    def test_dedup_processed_archive(self, collector: ManualURLCollector):
        data = {
            "pending": [],
            "processed_archive": [{"url": "https://example.com/old"}],
        }
        added = collector._append_urls(["https://example.com/old"], data)
        assert added == 0


# ---------- _guess_title ----------

class TestGuessTitle:
    def test_from_metadata(self):
        result = {"article_metadata": {"title": "My Great Article"}}
        assert ManualURLCollector._guess_title("https://example.com", result) == "My Great Article"

    def test_from_url(self):
        title = ManualURLCollector._guess_title("https://example.com/blog/hello", None)
        assert "example.com" in title
        assert "hello" in title

    def test_bare_domain(self):
        title = ManualURLCollector._guess_title("https://example.com", None)
        assert title == "example.com"
