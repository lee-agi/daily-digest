from __future__ import annotations

from datetime import datetime, timezone

from aggregator.merger import split_ai_digest_items_with_relevance
from schema import ContentItem, SourceType
from scripts.evaluate_ai_digest_quality import evaluate_items, summarize_history
from scripts.record_ai_digest_feedback import build_feedback_entry, parse_action_value, summarize


def _item(source: str, title: str, score: float = 10.0, tags: list[str] | None = None) -> ContentItem:
    return ContentItem(
        source=source,
        source_type=SourceType.RSS,
        title=title,
        url=f"https://example.com/{source}/{abs(hash(title))}",
        content=title,
        published_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
        score=score,
        tags=tags or [],
    )


def test_evaluate_items_reports_routing_metrics(tmp_path):
    items = [
        _item("x_twitter", "OpenAI launches a new agent coding workflow", 100, ["ai"]),
        _item("finance", "S&P 500 rises after broad earnings", 80),
        _item("hackernews", "Show HN: CSS color picker", 70),
        _item("weread", "A book worth reading", 5),
    ]
    result = evaluate_items(items, target_date="2026-05-10", source_file=tmp_path / "collected.json")
    assert result["counts"]["raw_items"] == 4
    assert result["counts"]["investment_inputs"] == 1
    assert result["counts"]["low_relevance_filtered"] == 1
    assert result["counts"]["raw_duplicate_items"] >= 0
    assert "event_folded_items" in result["counts"]
    assert result["metrics"]["pre_llm_reduction_ratio"] > 0
    assert "precision_at_10_proxy" in result["metrics"]
    assert "event_fold_ratio" in result["metrics"]
    assert result["negative_labels"]["counts"]["non_ai_generic_finance"] == 1
    assert result["negative_labels"]["counts"]["single_source_hn_launch"] == 1
    assert result["metrics"]["negative_label_rate"] > 0
    assert result["cost_time"]["source"] == "not_available"


def test_evaluate_items_reports_explicit_negative_proxy_labels(tmp_path):
    items = [
        _item("finance", "Personal finance ETF portfolio ideas after Fed rate cuts", 90),
        _item("producthunt", "Launch: AI meeting notes helper", 3),
        _item("youtube", "AI coding tutorial for beginners", 1000),
        _item("x_twitter", "Hot take: OpenAI agents are overhyped", 500),
        _item("openai", "OpenAI Codex adds role-specific plugins, Sites and annotations", 10, ["ai"]),
    ]

    result = evaluate_items(items, target_date="2026-05-10", source_file=tmp_path / "collected.json")
    labels = result["negative_labels"]["counts"]

    assert labels["non_ai_personal_finance"] == 1
    assert labels["producthunt_low_adoption"] == 1
    assert labels["generic_video_course"] == 1
    assert labels["single_social_opinion"] == 1
    assert result["counts"]["negative_labeled_items"] >= 4
    assert result["negative_labels"]["examples"]["producthunt_low_adoption"][0]["route"] == "ai_digest"


def test_history_summary_averages_metrics():
    history = summarize_history([
        {"date": "2026-05-09", "counts": {"raw_items": 10}, "metrics": {"precision_at_10_proxy": 0.8}, "cost_time": {"estimated_cost_usd": 0.01}, "feedback": {"count": 1, "labels": {"good": 1}}},
        {"date": "2026-05-10", "counts": {"raw_items": 20}, "metrics": {"precision_at_10_proxy": 1.0}, "cost_time": {"estimated_cost_usd": 0.02}, "feedback": {"count": 2, "issue_labels": {"噪音太多": 2}}},
    ], end_date="2026-05-10", days=2)
    assert history["evaluated_days"] == 2
    assert history["count_totals"]["raw_items"] == 30
    assert history["metric_averages"]["precision_at_10_proxy"] == 0.9
    assert history["cost_time_totals"]["estimated_cost_usd"] == 0.03
    assert history["feedback_totals"]["count"] == 3
    assert history["feedback_totals"]["labels"]["good"] == 1
    assert history["feedback_totals"]["issue_labels"]["噪音太多"] == 2


def test_feedback_summary_groups_by_date_and_label():
    summary = summarize([
        {"date": "2026-05-10", "rating": 1, "label": "good"},
        {"date": "2026-05-10", "rating": -1, "label": "too_noisy"},
        {"date": "2026-05-09", "rating": 2, "label": "good"},
    ])
    assert summary["total_feedback"] == 3
    assert summary["by_date"]["2026-05-10"]["count"] == 2
    assert summary["by_date"]["2026-05-10"]["rating_avg"] == 0.0
    assert summary["by_date"]["2026-05-10"]["labels"]["good"] == 1


def test_structured_feedback_entry_preserves_legacy_fields():
    entry = build_feedback_entry(
        feedback_date="2026-05-17",
        rating=1,
        label="good",
        note="old note",
        source="manual",
        report_quality="基本可用",
        issue_labels=["噪音太多,漏重要信息", "噪音太多"],
        source_name="hackernews",
        source_action="demote",
        source_quality="noisy",
        event_id="evt-123",
        sample_action="降权",
        comment="too many generic posts",
        recorded_at="2026-05-17T00:00:00+00:00",
    )

    assert entry["schema_version"] == "ai_digest_feedback.v2"
    assert entry["rating"] == 1
    assert entry["label"] == "good"
    assert entry["note"] == "old note"
    assert entry["source"] == "manual"
    assert entry["feedback_source"] == "manual"
    assert entry["report_quality"] == "基本可用"
    assert entry["issue_labels"] == ["噪音太多", "漏重要信息"]
    assert entry["source_quality"] == {
        "source": "hackernews",
        "action": "demote",
        "quality": "noisy",
    }
    assert entry["event_action"] == {"event_id": "evt-123", "action": "降权"}
    assert entry["sample_action"] == {"event_id": "evt-123", "action": "降权"}
    assert entry["comment"] == "too many generic posts"


def test_feedback_summary_counts_structured_fields():
    summary = summarize([
        build_feedback_entry(
            feedback_date="2026-05-17",
            report_quality="基本可用",
            issue_labels=["噪音太多"],
            source_name="x_twitter",
            source_action="inspect",
            event_id="evt-1",
            sample_action="升权",
            recorded_at="2026-05-17T00:00:00+00:00",
        ),
        build_feedback_entry(
            feedback_date="2026-05-17",
            report_quality="基本可用",
            issue_labels=["噪音太多", "路由错误"],
            source_name="x_twitter",
            source_action="demote",
            event_id="evt-2",
            sample_action="降权",
            recorded_at="2026-05-17T00:00:01+00:00",
        ),
    ])

    bucket = summary["by_date"]["2026-05-17"]
    assert bucket["report_quality"]["基本可用"] == 2
    assert bucket["issue_labels"]["噪音太多"] == 2
    assert bucket["issue_labels"]["路由错误"] == 1
    assert bucket["source_actions"]["inspect"] == 1
    assert bucket["source_actions"]["demote"] == 1
    assert bucket["event_actions"]["升权"] == 1
    assert bucket["event_actions"]["降权"] == 1
    assert bucket["sample_actions"]["升权"] == 1
    assert bucket["sample_actions"]["降权"] == 1


def test_parse_action_value_builds_report_quality_entry():
    entry = parse_action_value(
        "ai_digest_feedback|date=2026-05-17|report_quality=基本可用",
        recorded_at="2026-05-17T00:00:00+00:00",
    )

    assert entry["schema_version"] == "ai_digest_feedback.v2"
    assert entry["date"] == "2026-05-17"
    assert entry["feedback_source"] == "card"
    assert entry["source"] == "card"
    assert entry["report_quality"] == "基本可用"
    assert entry["issue_labels"] == []
    assert "sample_action" not in entry


def test_parse_action_value_builds_issue_label_entry():
    entry = parse_action_value(
        "ai_digest_feedback|date=2026-05-17|issue_label=噪音太多",
        recorded_at="2026-05-17T00:00:00+00:00",
    )

    assert entry["date"] == "2026-05-17"
    assert entry["issue_labels"] == ["噪音太多"]
    assert entry["report_quality"] == ""


def test_parse_action_value_builds_sample_action_entry():
    entry = parse_action_value(
        "ai_digest_feedback|date=2026-05-17|event_id=evt-1|sample_action=降权",
        recorded_at="2026-05-17T00:00:00+00:00",
    )

    assert entry["date"] == "2026-05-17"
    assert entry["event_action"] == {"event_id": "evt-1", "action": "降权"}
    assert entry["sample_action"] == {"event_id": "evt-1", "action": "降权"}


def test_parse_action_value_builds_topic_event_and_item_entries():
    topic = parse_action_value(
        "ai_digest_feedback|date=2026-05-17|topic=Agent Tools|topic_action=降权",
        recorded_at="2026-05-17T00:00:00+00:00",
    )
    event = parse_action_value(
        "ai_digest_feedback|date=2026-05-17|event_id=evt-1|event_action=升权",
        recorded_at="2026-05-17T00:00:00+00:00",
    )
    item = parse_action_value(
        "ai_digest_feedback|date=2026-05-17|item_ref=evt-1:main|item_action=删除",
        recorded_at="2026-05-17T00:00:00+00:00",
    )

    assert topic["topic_action"] == {"topic": "Agent Tools", "action": "降权"}
    assert event["event_action"] == {"event_id": "evt-1", "action": "升权"}
    assert item["item_action"] == {"item_ref": "evt-1:main", "action": "删除"}


def test_parse_action_value_rejects_invalid_payloads():
    for value in [
        "wrong|date=2026-05-17|report_quality=基本可用",
        "ai_digest_feedback|report_quality=基本可用",
        "ai_digest_feedback|date=2026-05-17|unknown=x",
        "ai_digest_feedback|date=2026-05-17|event_id=evt-1",
        "ai_digest_feedback|date=2026-05-17|event_id=evt-1|sample_action=drop",
        "ai_digest_feedback|date=2026-05-17|topic=Agent Tools",
        "ai_digest_feedback|date=2026-05-17|topic_action=降权",
        "ai_digest_feedback|date=2026-05-17|item_ref=evt-1:main",
        "ai_digest_feedback|date=2026-05-17|item_action=删除",
    ]:
        try:
            parse_action_value(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid action value: {value}")
