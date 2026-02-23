"""Tests for SQLite state management."""

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from schema import ContentItem, RunRecord, SourceType
from state import (
    filter_unseen,
    get_connection,
    get_latest_run,
    is_seen,
    mark_seen,
    save_run,
)


def _temp_db():
    """Create a temporary database for testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return get_connection(Path(tmp.name))


def _make_item(source="test", title="Test Item", url="https://example.com/1", arxiv_id=None):
    return ContentItem(
        source=source,
        source_type=SourceType.API,
        title=title,
        url=url,
        arxiv_id=arxiv_id,
    )


class TestSeenItems:
    def test_mark_and_check(self):
        conn = _temp_db()
        item = _make_item()
        assert not is_seen(conn, item)
        mark_seen(conn, [item])
        assert is_seen(conn, item)
        conn.close()

    def test_dedup_by_url(self):
        conn = _temp_db()
        item1 = _make_item(url="https://example.com/same")
        item2 = _make_item(source="other", title="Different", url="https://example.com/same")
        mark_seen(conn, [item1])
        assert is_seen(conn, item2)  # Same URL hash
        conn.close()

    def test_dedup_by_arxiv_id(self):
        conn = _temp_db()
        item1 = _make_item(url="https://hf.co/paper1", arxiv_id="2401.12345")
        item2 = _make_item(url="https://papers.cool/paper1", arxiv_id="2401.12345")
        mark_seen(conn, [item1])
        assert is_seen(conn, item2)  # Same ArXiv ID
        conn.close()

    def test_filter_unseen(self):
        conn = _temp_db()
        items = [
            _make_item(url="https://example.com/1"),
            _make_item(url="https://example.com/2"),
            _make_item(url="https://example.com/3"),
        ]
        mark_seen(conn, items[:1])  # Only first item seen
        unseen = filter_unseen(conn, items)
        assert len(unseen) == 2
        assert unseen[0].url == "https://example.com/2"
        conn.close()


class TestRunHistory:
    def test_save_and_retrieve(self):
        conn = _temp_db()
        record = RunRecord(
            run_id="test-001",
            date="2026-02-23",
            phase="collect",
            started_at=datetime.now(timezone.utc),
        )
        save_run(conn, record)
        latest = get_latest_run(conn, "2026-02-23", "collect")
        assert latest is not None
        assert latest["run_id"] == "test-001"
        assert latest["status"] == "running"
        conn.close()

    def test_update_run(self):
        conn = _temp_db()
        record = RunRecord(
            run_id="test-002",
            date="2026-02-23",
            phase="full",
            started_at=datetime.now(timezone.utc),
        )
        save_run(conn, record)
        record.status = "success"
        record.finished_at = datetime.now(timezone.utc)
        save_run(conn, record)
        latest = get_latest_run(conn, "2026-02-23", "full")
        assert latest["status"] == "success"
        conn.close()
