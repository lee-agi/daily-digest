from __future__ import annotations

import json
from datetime import datetime, timezone

from aggregator.event_cluster import cluster_content_items
from report.event_ledger import SCHEMA_VERSION, build_event_ledger, write_event_ledger
from schema import ContentItem, SourceType


def _item(source: str, title: str, url: str, score: float = 10.0) -> ContentItem:
    return ContentItem(
        source=source,
        source_type=SourceType.RSS,
        title=title,
        url=url,
        content=title,
        score=score,
        tags=["ai"],
    )


def test_build_event_ledger_serializes_ranked_events_and_source_stats() -> None:
    items = [
        _item("anthropic", "Claude Code usage limits increase after SpaceX compute deal", "https://www.anthropic.com/news/limits", 100),
        _item("x_twitter", "SpaceX compute capacity increases Claude usage limits", "https://x.com/claudeai/status/1", 80),
        _item("github", "New MCP developer tool release", "https://github.com/example/mcp", 50),
    ]
    clusters = cluster_content_items(items)
    ledger = build_event_ledger(
        date="2026-05-17",
        generated_at=datetime(2026, 5, 17, 1, 2, 3, tzinfo=timezone.utc),
        event_clusters=clusters,
        raw_items=items,
        kept_items=items,
        categories=["Developer Tools", "Social & Community"],
        top_n=1,
        routing_stats={"ai_kept": 3, "investment_routed": 0, "low_relevance_filtered": 0},
        link_health={"checked": 3},
    )

    assert ledger["schema_version"] == SCHEMA_VERSION
    assert ledger["date"] == "2026-05-17"
    assert len(ledger["events"]) == len(clusters)
    assert ledger["events"][0]["rank"] == 1
    assert ledger["events"][0]["selected"] is True
    assert ledger["events"][0]["main_item"]["title"]
    assert ledger["events"][0]["event_score"] == ledger["events"][0]["final_score"]
    assert "dimension_scores" in ledger["events"][0]
    assert ledger["source_stats"]["anthropic"]["raw"] == 1
    assert ledger["source_stats"]["anthropic"]["selected"] == 1
    assert ledger["routing_stats"]["ai_kept"] == 3


def test_write_event_ledger_writes_dated_and_latest_json(tmp_path) -> None:
    generated_at = datetime(2026, 5, 17, 1, 2, 3, 456, tzinfo=timezone.utc)
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "date": "2026-05-17",
        "events": [],
        "source_stats": {},
        "routing_stats": {},
    }

    path = write_event_ledger(ledger, data_dir=tmp_path, generated_at=generated_at)

    assert path.name == "event-ledger-2026-05-17-010203-000456.json"
    assert path.exists()
    latest = tmp_path / "event-ledger-latest.json"
    assert latest.exists()
    assert json.loads(latest.read_text(encoding="utf-8"))["schema_version"] == SCHEMA_VERSION
