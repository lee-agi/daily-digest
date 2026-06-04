#!/usr/bin/env python3
"""Lightweight AI Daily quality/evaluation snapshot.

This is intentionally deterministic and cheap: it evaluates routing, relevance,
source/category balance and duplicate pressure from collected JSON without
calling an LLM. The output is an audit signal for Lee-facing refactors, not a
perfect judgment of editorial quality.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aggregator.dedup import deduplicate
from aggregator.event_cluster import cluster_content_items
from aggregator.merger import ai_relevance_score, prefilter_policy_metadata, split_ai_digest_items_with_relevance
from schema import ContentItem

DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "reports" / "eval"
FEEDBACK_PATH = DATA_DIR / "feedback" / "ai-digest-feedback.jsonl"


def latest_collected_path(target_date: str) -> Path:
    candidates = sorted(
        [p for p in DATA_DIR.glob(f"collected-{target_date}-*.json")],
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        raise FileNotFoundError(f"No collected JSON found for {target_date} under {DATA_DIR}")
    return candidates[-1]


def available_collected_dates(*, end_date: str, days: int) -> list[str]:
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    start = end - timedelta(days=max(1, days) - 1)
    dates: set[str] = set()
    for path in DATA_DIR.glob("collected-????-??-??-*.json"):
        parts = path.name.split("-")
        if len(parts) < 4:
            continue
        day = "-".join(parts[1:4])
        try:
            parsed = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            continue
        if start <= parsed <= end:
            dates.add(day)
    return sorted(dates)


def load_items(path: Path) -> list[ContentItem]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ContentItem.model_validate(row) for row in data]


def _source_counts(items: list[ContentItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.source] = counts.get(item.source, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _item_text(item: ContentItem) -> str:
    return " ".join(
        str(part or "")
        for part in (item.title, item.content, item.author, " ".join(item.tags or []), item.url)
    ).lower()


_FINANCE_GENERIC_TERMS = {
    "s&p", "s&p 500", "nasdaq", "dow", "index", "indices", "market", "markets",
    "macro", "inflation", "fed", "rate cut", "treasury", "yield", "earnings",
    "stocks", "shares", "bonds", "etf", "portfolio", "personal finance",
}
_SOCIAL_OPINION_TERMS = {
    "i think", "my take", "hot take", "thread", "opinion", "thoughts", "reaction",
    "讨论", "观点", "吐槽", "随想",
}
_PRODUCT_LAUNCH_TERMS = {"launch", "launched", "introducing", "show hn", "beta", "waitlist", "上线", "发布"}
_GENERIC_LEARNING_MEDIA_TERMS = {
    "tutorial", "course", "lesson", "walkthrough", "beginner", "intro", "getting started",
    "productivity routines", "startup lessons", "paper club", "recap", "interview",
}


def _negative_labels_for_item(item: ContentItem, *, route: str) -> list[str]:
    """Return deterministic negative/audit labels for AI Daily eval.

    These labels are not a replacement for editorial judgment. They make known
    bad cases visible in eval snapshots so regressions are countable: generic
    finance should feed investment, single-source social/Product Hunt heat should
    be treated conservatively, and generic learning media should not crowd out
    official hard evidence.
    """
    source = (item.source or "").lower()
    text = _item_text(item)
    labels: list[str] = []

    if route == "investment" and (source == "finance" or "finance" in {t.lower() for t in item.tags or []}):
        if "personal finance" in text or "portfolio" in text or "etf" in text:
            labels.append("non_ai_personal_finance")
        elif any(term in text for term in _FINANCE_GENERIC_TERMS):
            labels.append("non_ai_generic_finance")
        else:
            labels.append("non_ai_finance")

    if source in {"x_twitter", "reddit", "zhihu", "jike"} and any(term in text for term in _SOCIAL_OPINION_TERMS):
        labels.append("single_social_opinion")

    if source == "hackernews" and "show hn" in text:
        labels.append("single_source_hn_launch")

    if source == "producthunt" and any(term in text for term in _PRODUCT_LAUNCH_TERMS):
        if float(item.score or 0) < 100:
            labels.append("producthunt_low_adoption")
        else:
            labels.append("single_source_product_launch")

    if source in {"youtube", "apple_podcast", "ai_exec_podcast", "xiaoyuzhou"} and any(term in text for term in _GENERIC_LEARNING_MEDIA_TERMS):
        labels.append("generic_video_course" if source == "youtube" else "generic_podcast_interview")

    if route == "filtered" and not labels:
        labels.append("low_ai_relevance")

    return labels


def _negative_label_audit(
    *,
    ai_items: list[ContentItem],
    investment_inputs: list[ContentItem],
    low_relevance: list[ContentItem],
    max_examples_per_label: int = 5,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    examples: dict[str, list[dict[str, Any]]] = {}
    for route, bucket in (
        ("ai_digest", ai_items),
        ("investment", investment_inputs),
        ("filtered", low_relevance),
    ):
        for item in bucket:
            for label in _negative_labels_for_item(item, route=route):
                counts[label] = counts.get(label, 0) + 1
                label_examples = examples.setdefault(label, [])
                if len(label_examples) < max_examples_per_label:
                    label_examples.append({
                        "title": item.title,
                        "source": item.source,
                        "url": item.url,
                        "route": route,
                        "score": item.score,
                        "ai_relevance_score": ai_relevance_score(item),
                    })
    return {
        "counts": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "examples": dict(sorted(examples.items())),
    }


def _load_feedback_entries(target_date: str) -> list[dict[str, Any]]:
    if not FEEDBACK_PATH.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in FEEDBACK_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("date") == target_date:
            entries.append(row)
    return entries


def _feedback_summary(target_date: str) -> dict[str, Any]:
    entries = _load_feedback_entries(target_date)
    ratings = [float(e["rating"]) for e in entries if isinstance(e.get("rating"), (int, float))]
    labels: dict[str, int] = {}
    issue_labels: dict[str, int] = {}
    report_quality: dict[str, int] = {}
    source_actions: dict[str, int] = {}
    event_actions: dict[str, int] = {}
    item_actions: dict[str, int] = {}
    topic_actions: dict[str, int] = {}
    for entry in entries:
        label = str(entry.get("label") or "").strip()
        if label:
            labels[label] = labels.get(label, 0) + 1
        quality = str(entry.get("report_quality") or "").strip()
        if quality:
            report_quality[quality] = report_quality.get(quality, 0) + 1
        for issue in entry.get("issue_labels") or []:
            issue = str(issue or "").strip()
            if issue:
                issue_labels[issue] = issue_labels.get(issue, 0) + 1
        for field, bucket in (
            ("source_quality", source_actions),
            ("event_action", event_actions),
            ("sample_action", event_actions),
            ("item_action", item_actions),
            ("topic_action", topic_actions),
        ):
            action_obj = entry.get(field) or {}
            if isinstance(action_obj, dict):
                action = str(action_obj.get("action") or "").strip()
                if action:
                    bucket[action] = bucket.get(action, 0) + 1
    return {
        "count": len(entries),
        "rating_avg": round(statistics.mean(ratings), 4) if ratings else None,
        "labels": dict(sorted(labels.items(), key=lambda kv: (-kv[1], kv[0]))),
        "issue_labels": dict(sorted(issue_labels.items(), key=lambda kv: (-kv[1], kv[0]))),
        "report_quality": dict(sorted(report_quality.items(), key=lambda kv: (-kv[1], kv[0]))),
        "source_actions": dict(sorted(source_actions.items(), key=lambda kv: (-kv[1], kv[0]))),
        "event_actions": dict(sorted(event_actions.items(), key=lambda kv: (-kv[1], kv[0]))),
        "item_actions": dict(sorted(item_actions.items(), key=lambda kv: (-kv[1], kv[0]))),
        "topic_actions": dict(sorted(topic_actions.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def evaluate_items(
    items: list[ContentItem],
    *,
    target_date: str,
    source_file: Path,
    max_cluster_items: int = 300,
    config: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    ai_items, investment_inputs, low_relevance = split_ai_digest_items_with_relevance(items, config=config)
    raw_deduped = deduplicate(items)
    deduped_ai = deduplicate(ai_items)
    cluster_input = sorted(deduped_ai, key=lambda x: float(x.score or 0), reverse=True)[:max_cluster_items]
    clusters = cluster_content_items(cluster_input, config=config)
    top_clusters = clusters[:10]
    top_scores = [float(c.event_score or 0) for c in top_clusters]
    top_lee = [float((c.dimension_scores or {}).get("lee_relevance") or 0) for c in top_clusters]
    top_relevance = [ai_relevance_score(c.main_item) for c in top_clusters]

    # Proxy metrics: deterministic guardrails that can be trended over time.
    precision_at_10_proxy = sum(1 for s in top_relevance if s >= 0.35) / max(1, len(top_relevance))
    duplicate_rate_proxy = 1.0 - (len(clusters) / max(1, len(deduped_ai)))
    event_fold_ratio = 1.0 - (len(clusters) / max(1, len(cluster_input)))
    reduction_ratio = 1.0 - (len(ai_items) / max(1, len(items)))
    important_raw = sorted(items, key=lambda x: float(x.score or 0), reverse=True)[:30]
    important_kept = sum(1 for item in important_raw if item in ai_items or item in investment_inputs)
    important_event_recall_proxy = important_kept / max(1, len(important_raw))

    cluster_sizes = [len(c.items) for c in clusters]
    negative_label_audit = _negative_label_audit(
        ai_items=ai_items,
        investment_inputs=investment_inputs,
        low_relevance=low_relevance,
    )
    usage = usage or {}

    return {
        "date": target_date,
        "source_file": str(source_file),
        "prefilter_policy": prefilter_policy_metadata(config),
        "counts": {
            "raw_items": len(items),
            "raw_deduped_items": len(raw_deduped),
            "raw_duplicate_items": max(0, len(items) - len(raw_deduped)),
            "ai_digest_items": len(ai_items),
            "investment_inputs": len(investment_inputs),
            "low_relevance_filtered": len(low_relevance),
            "negative_labeled_items": sum(negative_label_audit["counts"].values()),
            "deduped_ai_items": len(deduped_ai),
            "ai_duplicate_items": max(0, len(ai_items) - len(deduped_ai)),
            "eval_cluster_input_items": len(cluster_input),
            "event_clusters": len(clusters),
            "event_folded_items": max(0, len(cluster_input) - len(clusters)),
        },
        "metrics": {
            "pre_llm_reduction_ratio": round(reduction_ratio, 4),
            "precision_at_10_proxy": round(precision_at_10_proxy, 4),
            "important_event_recall_proxy": round(important_event_recall_proxy, 4),
            "duplicate_rate_proxy": round(max(0.0, duplicate_rate_proxy), 4),
            "event_fold_ratio": round(max(0.0, event_fold_ratio), 4),
            "negative_label_rate": round(sum(negative_label_audit["counts"].values()) / max(1, len(items)), 4),
            "avg_cluster_size": round(statistics.mean(cluster_sizes), 4) if cluster_sizes else 0.0,
            "max_cluster_size": max(cluster_sizes) if cluster_sizes else 0,
            "top10_event_score_avg": round(statistics.mean(top_scores), 4) if top_scores else 0.0,
            "top10_lee_relevance_avg": round(statistics.mean(top_lee), 4) if top_lee else 0.0,
        },
        "cost_time": {
            "eval_elapsed_seconds": round(time.monotonic() - started, 4),
            "task_llm_calls": int(usage.get("calls") or usage.get("task_llm_calls") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "estimated_cost_usd": round(float(usage.get("estimated_cost_usd") or 0.0), 8),
            "source": "provided" if usage else "not_available",
        },
        "source_counts": {
            "raw": _source_counts(items),
            "ai_digest": _source_counts(ai_items),
            "investment": _source_counts(investment_inputs),
            "filtered": _source_counts(low_relevance),
        },
        "negative_labels": negative_label_audit,
        "feedback": _feedback_summary(target_date),
        "top10_events": [
            {
                "title": c.title,
                "url": c.url,
                "source": c.source,
                "category": c.category,
                "source_tier": c.source_tier,
                "event_score": c.event_score,
                "dimension_scores": c.dimension_scores,
                "ai_relevance_score": ai_relevance_score(c.main_item),
                "cluster_size": len(c.items),
            }
            for c in top_clusters
        ],
    }


def write_report(result: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target_date = result["date"]
    path = OUT_DIR / f"ai-digest-eval-{target_date}.json"
    text = json.dumps(result, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    (OUT_DIR / "ai-digest-eval-latest.json").write_text(text, encoding="utf-8")
    return path


def summarize_history(results: list[dict[str, Any]], *, end_date: str, days: int) -> dict[str, Any]:
    metric_keys = sorted({k for r in results for k in (r.get("metrics") or {})})
    count_keys = sorted({k for r in results for k in (r.get("counts") or {})})
    cost_time_keys = sorted({k for r in results for k in (r.get("cost_time") or {}) if isinstance((r.get("cost_time") or {}).get(k), (int, float))})
    metric_avgs: dict[str, float] = {}
    count_totals: dict[str, int] = {}
    cost_time_totals: dict[str, float] = {}
    for key in metric_keys:
        values = [float((r.get("metrics") or {}).get(key, 0.0)) for r in results]
        metric_avgs[key] = round(statistics.mean(values), 4) if values else 0.0
    for key in count_keys:
        count_totals[key] = int(sum(int((r.get("counts") or {}).get(key, 0)) for r in results))
    for key in cost_time_keys:
        cost_time_totals[key] = round(sum(float((r.get("cost_time") or {}).get(key, 0.0)) for r in results), 8)
    return {
        "end_date": end_date,
        "requested_days": days,
        "evaluated_days": len(results),
        "dates": [r.get("date") for r in results],
        "metric_averages": metric_avgs,
        "count_totals": count_totals,
        "cost_time_totals": cost_time_totals,
        "feedback_totals": {
            "count": int(sum(int((r.get("feedback") or {}).get("count") or 0) for r in results)),
            "labels": _merge_count_maps([((r.get("feedback") or {}).get("labels") or {}) for r in results]),
            "issue_labels": _merge_count_maps([((r.get("feedback") or {}).get("issue_labels") or {}) for r in results]),
        },
        "latest": results[-1] if results else None,
    }


def _merge_count_maps(maps: list[dict[str, int]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for mapping in maps:
        for key, value in mapping.items():
            merged[str(key)] = merged.get(str(key), 0) + int(value)
    return dict(sorted(merged.items(), key=lambda kv: (-kv[1], kv[0])))


def write_history_report(history: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"ai-digest-eval-history-{history['end_date']}.json"
    text = json.dumps(history, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    (OUT_DIR / "ai-digest-eval-history-latest.json").write_text(text, encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate AI Daily routing/quality guardrails without LLM calls")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date, YYYY-MM-DD")
    parser.add_argument("--input", type=Path, default=None, help="Collected JSON path; defaults to latest for date")
    parser.add_argument("--days", type=int, default=1, help="Evaluate latest collected files for the previous N days")
    parser.add_argument("--max-cluster-items", type=int, default=300, help="Cap per-day event clustering to top scored AI items for fast historical eval")
    args = parser.parse_args()

    if args.input is not None or args.days <= 1:
        source_file = args.input or latest_collected_path(args.date)
        items = load_items(source_file)
        result = evaluate_items(items, target_date=args.date, source_file=source_file, max_cluster_items=args.max_cluster_items)
        path = write_report(result)
        print(json.dumps({"report": str(path), "counts": result["counts"], "metrics": result["metrics"], "feedback": result["feedback"], "cost_time": result["cost_time"]}, ensure_ascii=False, indent=2))
        return

    results = []
    for day in available_collected_dates(end_date=args.date, days=args.days):
        source_file = latest_collected_path(day)
        results.append(evaluate_items(load_items(source_file), target_date=day, source_file=source_file, max_cluster_items=args.max_cluster_items))
    history = summarize_history(results, end_date=args.date, days=args.days)
    path = write_history_report(history)
    print(json.dumps({"report": str(path), "evaluated_days": history["evaluated_days"], "metric_averages": history["metric_averages"], "count_totals": history["count_totals"], "cost_time_totals": history["cost_time_totals"], "feedback_totals": history["feedback_totals"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
