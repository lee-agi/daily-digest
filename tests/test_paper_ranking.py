from __future__ import annotations

from schema import ContentItem, SourceType
from aggregator.event_cluster import cluster_content_items
from report.source_validation import source_tier, source_tier_weight


def _paper(title: str, content: str, score: float = 99.0, *, url: str = "https://arxiv.org/abs/2605.00001") -> ContentItem:
    return ContentItem(
        source="arxiv",
        source_type=SourceType.RSS,
        title=title,
        url=url,
        content=content,
        score=score,
        arxiv_id=url.rsplit("/", 1)[-1],
    )


def test_source_tier_confidence_multiplier_is_capped() -> None:
    assert source_tier("arxiv", "https://arxiv.org/abs/2605.00001") == "T1.5"
    assert source_tier("microsoft", "https://www.microsoft.com/research/example") == "T1.5"
    assert source_tier("youtube", "https://www.youtube.com/watch?v=abc") == "T1.5"
    assert source_tier_weight("arxiv", "https://arxiv.org/abs/2605.00001") <= 1.15
    assert source_tier_weight("arxiv", "https://arxiv.org/abs/2605.00001") > source_tier_weight("reddit", "https://reddit.com/r/LocalLLaMA/comments/1")


def test_openreview_oral_papers_are_promoted_to_t1_only_with_metadata() -> None:
    poster = ContentItem(
        source="openreview",
        source_type=SourceType.API,
        title="Accepted Poster Paper",
        url="https://openreview.net/forum?id=poster",
        content="Venue: ICLR 2026 Poster",
        score=65,
        tags=["openreview", "conference", "paper"],
        extra={"venue": "ICLR 2026 Poster"},
    )
    oral = ContentItem(
        source="openreview",
        source_type=SourceType.API,
        title="Oral Paper on Agentic Reasoning",
        url="https://openreview.net/forum?id=oral",
        content="Venue: ICLR 2026 Oral",
        score=100,
        tags=["openreview", "conference", "paper"],
        extra={"venue": "ICLR 2026 Oral"},
    )

    assert source_tier("openreview", "https://openreview.net/forum?id=poster") == "T1.5"
    assert cluster_content_items([poster])[0].source_tier == "T1.5"
    assert cluster_content_items([oral])[0].source_tier == "T1"


def test_ordinary_survey_paper_is_not_boosted_by_being_a_paper() -> None:
    cluster = cluster_content_items([
        _paper(
            "A Survey of Large Language Model Agent Applications",
            "This survey reviews recent LLM agent papers and provides an overview taxonomy.",
        )
    ])[0]

    assert cluster.category == "Research Papers"
    assert cluster.score_breakdown["topic_multiplier"] <= 1.0
    assert cluster.score_breakdown["paper_subtype"] == "survey"
    assert cluster.score_breakdown["paper_quality_multiplier"] < 1.0


def test_high_signal_reproducible_paper_beats_ordinary_paper() -> None:
    ordinary = cluster_content_items([
        _paper(
            "A Small Survey of Prompting for LLM Agents",
            "This survey provides a short overview of existing prompting methods.",
            score=99,
            url="https://arxiv.org/abs/2605.00002",
        )
    ])[0]
    strong = cluster_content_items([
        _paper(
            "Breakthrough Agentic Reasoning Benchmark with Open Source Code",
            (
                "Novel state-of-the-art benchmark and evaluation suite for coding agents. "
                "Includes ablation experiments, dataset, GitHub code, reproducible results, "
                "leaderboard, model weights, and real-world deployment analysis."
            ),
            score=99,
            url="https://arxiv.org/abs/2605.00003",
        )
    ])[0]

    assert strong.score_breakdown["paper_quality_score"] > ordinary.score_breakdown["paper_quality_score"]
    assert strong.score_breakdown["paper_quality_multiplier"] > 1.0
    assert strong.score_breakdown["paper_quality_multiplier"] > ordinary.score_breakdown["paper_quality_multiplier"]
    assert strong.event_score > ordinary.event_score
