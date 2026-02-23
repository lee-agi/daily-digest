"""SQLite state management for dedup and run history."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from schema import ContentItem, RunRecord

DB_PATH = Path(__file__).parent / "state.db"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS seen_items (
            content_hash TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            url TEXT,
            title TEXT,
            arxiv_id TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_seen_source ON seen_items(source);
        CREATE INDEX IF NOT EXISTS idx_seen_arxiv ON seen_items(arxiv_id)
            WHERE arxiv_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS run_history (
            run_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            phase TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            result_json TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_run_date ON run_history(date);
    """)
    conn.commit()


def is_seen(conn: sqlite3.Connection, item: ContentItem) -> bool:
    """Check if item was already pushed (by hash or arxiv_id)."""
    row = conn.execute(
        "SELECT 1 FROM seen_items WHERE content_hash = ?",
        (item.content_hash,)
    ).fetchone()
    if row:
        return True

    if item.arxiv_id:
        row = conn.execute(
            "SELECT 1 FROM seen_items WHERE arxiv_id = ?",
            (item.arxiv_id,)
        ).fetchone()
        if row:
            return True

    return False


def mark_seen(conn: sqlite3.Connection, items: list[ContentItem]) -> int:
    """Mark items as seen. Returns number of newly marked items."""
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for item in items:
        try:
            conn.execute(
                """INSERT INTO seen_items
                   (content_hash, source, url, title, arxiv_id, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(content_hash) DO UPDATE SET last_seen_at = ?""",
                (item.content_hash, item.source, item.url, item.title,
                 item.arxiv_id, now, now, now)
            )
            count += 1
        except sqlite3.Error:
            pass
    conn.commit()
    return count


def filter_unseen(conn: sqlite3.Connection, items: list[ContentItem]) -> list[ContentItem]:
    """Return only items not previously seen."""
    return [item for item in items if not is_seen(conn, item)]


def save_run(conn: sqlite3.Connection, record: RunRecord) -> None:
    """Save or update a run record."""
    conn.execute(
        """INSERT INTO run_history
           (run_id, date, phase, started_at, finished_at, status, result_json, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(run_id) DO UPDATE SET
             finished_at = excluded.finished_at,
             status = excluded.status,
             result_json = excluded.result_json,
             error = excluded.error""",
        (
            record.run_id,
            record.date,
            record.phase,
            record.started_at.isoformat(),
            record.finished_at.isoformat() if record.finished_at else None,
            record.status,
            json.dumps([r.model_dump() for r in record.collector_results], default=str),
            record.error,
        )
    )
    conn.commit()


def get_latest_run(conn: sqlite3.Connection, date: str, phase: str) -> dict | None:
    """Get latest run record for a date and phase."""
    row = conn.execute(
        "SELECT * FROM run_history WHERE date = ? AND phase = ? ORDER BY started_at DESC LIMIT 1",
        (date, phase)
    ).fetchone()
    if not row:
        return None
    cols = ["run_id", "date", "phase", "started_at", "finished_at", "status", "result_json", "error"]
    return dict(zip(cols, row))
