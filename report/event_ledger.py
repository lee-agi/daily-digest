"""Structured event ledger sidecar for AI Daily reports."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aggregator.event_cluster import EventCluster
from schema import ContentItem

SCHEMA_VERSION = "ai_event_ledger.v1"


def _isoformat(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _source_counts(items: list[ContentItem]) -> Counter[str]:
    return Counter(item.source or "unknown" for item in items)


def _safe_dimension_scores(cluster: EventCluster) -> dict[str, float]:
    return {
        str(key): float(value)
        for key, value in sorted((cluster.dimension_scores or {}).items())
    }


def _event_source_stats(
    *,
    raw_items: list[ContentItem],
    kept_items: list[ContentItem],
    event_clusters: list[EventCluster],
    selected_event_ids: set[str],
) -> dict[str, dict[str, int | float]]:
    raw = _source_counts(raw_items)
    kept = _source_counts(kept_items)
    events = Counter(cluster.source or "unknown" for cluster in event_clusters)
    selected = Counter(
        cluster.source or "unknown"
        for cluster in event_clusters
        if cluster.event_id in selected_event_ids
    )
    sources = sorted(set(raw) | set(kept) | set(events) | set(selected))
    stats: dict[str, dict[str, int | float]] = {}
    for source in sources:
        raw_count = int(raw.get(source, 0))
        selected_count = int(selected.get(source, 0))
        stats[source] = {
            "raw": raw_count,
            "kept": int(kept.get(source, 0)),
            "events": int(events.get(source, 0)),
            "selected": selected_count,
            "selected_yield": round(selected_count / raw_count, 4) if raw_count else 0.0,
        }
    return stats


def build_event_ledger(
    *,
    date: str,
    generated_at: datetime,
    event_clusters: list[EventCluster],
    raw_items: list[ContentItem],
    kept_items: list[ContentItem],
    categories: list[str],
    top_n: int,
    selected_event_ids: set[str] | None = None,
    selection_ranks: dict[str, int] | None = None,
    routing_stats: dict[str, Any] | None = None,
    link_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic JSON payload for the AI Daily event ledger.

    The ledger is intentionally sidecar-only: it mirrors the ranked event input
    used by the report generator, but does not change the rendered digest body.
    Missing upstream metadata is represented with null/empty values.
    """
    if selected_event_ids is None:
        selected_event_ids = {
            cluster.event_id for cluster in event_clusters[: max(0, int(top_n or 0))]
        }
    selection_ranks = selection_ranks or {
        cluster.event_id: idx
        for idx, cluster in enumerate([c for c in event_clusters if c.event_id in selected_event_ids], start=1)
    }
    events: list[dict[str, Any]] = []
    for rank, cluster in enumerate(event_clusters, start=1):
        selected = cluster.event_id in selected_event_ids
        dimensions = _safe_dimension_scores(cluster)
        supporting_sources = [
            {
                "title": item.title,
                "url": item.url,
                "source": item.source,
                "score": item.score,
            }
            for item in cluster.items
            if item is not cluster.main_item
        ]
        event_score = float(cluster.event_score or 0.0)
        events.append({
            "event_id": cluster.event_id,
            "rank": rank,
            "topic": cluster.category,
            "category": cluster.category,
            "selected": selected,
            "selection_rank": selection_ranks.get(cluster.event_id) if selected else None,
            "canonical_event_key": str(getattr(cluster, "canonical_event_key", "") or ""),
            "main_item": {
                "title": cluster.title,
                "url": cluster.url,
                "source": cluster.source,
                "source_tier": cluster.source_tier,
                "source_weight": float(cluster.source_weight or 0.0),
                "topic_weight": float(getattr(cluster, "topic_weight", 1.0) or 1.0),
                "topic_confidence": float(getattr(cluster, "topic_confidence", 0.0) or 0.0),
                "topic_method": str(getattr(cluster, "topic_method", "") or ""),
                "topic_reason": str(getattr(cluster, "topic_reason", "") or ""),
                "topic_model_used": bool(getattr(cluster, "topic_model_used", False)),
            },
            "supporting_sources": supporting_sources,
            "event_score": event_score,
            "final_score": event_score,
            "dimension_scores": dimensions,
            "reason": None,
            "topic_decision": {
                "topic": cluster.category,
                "confidence": float(getattr(cluster, "topic_confidence", 0.0) or 0.0),
                "method": str(getattr(cluster, "topic_method", "") or ""),
                "reason": str(getattr(cluster, "topic_reason", "") or ""),
                "model_used": bool(getattr(cluster, "topic_model_used", False)),
            },
            "score_breakdown": dict(getattr(cluster, "score_breakdown", {}) or {}),
            "rank_breakdown": {
                "rank": rank,
                "selected_top_n": int(top_n or 0),
                "selection_rank": selection_ranks.get(cluster.event_id) if selected else None,
                "canonical_event_key": str(getattr(cluster, "canonical_event_key", "") or ""),
                "cluster_size": len(cluster.items),
                "source_tier": cluster.source_tier,
                "source_weight": float(cluster.source_weight or 0.0),
                "topic_weight": float(getattr(cluster, "topic_weight", 1.0) or 1.0),
                "topic_confidence": float(getattr(cluster, "topic_confidence", 0.0) or 0.0),
                "topic_method": str(getattr(cluster, "topic_method", "") or ""),
                "dimension_scores": dimensions,
                "score_breakdown": dict(getattr(cluster, "score_breakdown", {}) or {}),
            },
        })

    routing = {
        "ai_kept": None,
        "investment_routed": None,
        "low_relevance_filtered": None,
    }
    if routing_stats:
        routing.update(routing_stats)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _isoformat(generated_at),
        "date": date,
        "categories": list(categories),
        "events": events,
        "source_stats": _event_source_stats(
            raw_items=raw_items,
            kept_items=kept_items,
            event_clusters=event_clusters,
            selected_event_ids=selected_event_ids,
        ),
        "routing_stats": routing,
        "link_validation": link_health or {},
    }


def write_event_ledger(
    ledger: dict[str, Any],
    *,
    data_dir: Path,
    generated_at: datetime,
) -> Path:
    """Write dated and latest event ledger JSON files."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    timestamp = generated_at.astimezone(timezone.utc).strftime("%H%M%S-%f")
    target_date = str(ledger.get("date") or generated_at.date().isoformat())
    text = json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True)
    path = data_dir / f"event-ledger-{target_date}-{timestamp}.json"
    latest_path = data_dir / "event-ledger-latest.json"
    path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    return path
