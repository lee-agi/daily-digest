from __future__ import annotations

import json
from pathlib import Path

from schema import ContentItem, SourceType
from aggregator.event_cluster import (
    cluster_content_items,
    coerce_dimension_scores,
    event_cluster_payload,
    objective_dimension_schema,
    select_top_event_clusters,
)
from report.source_validation import source_tier, source_tier_weight


def _item(source: str, title: str, url: str, content: str = "", score: float = 1.0) -> ContentItem:
    return ContentItem(
        source=source,
        source_type=SourceType.RSS,
        title=title,
        url=url,
        content=content,
        score=score,
    )


def test_source_tiers_prefer_primary_sources() -> None:
    assert source_tier("anthropic", "https://www.anthropic.com/news/example") == "T1"
    assert source_tier("x_twitter", "https://x.com/claudeai/status/1") == "T1.5"
    assert source_tier("reddit", "https://reddit.com/r/LocalLLaMA/comments/1") == "T2"
    assert source_tier_weight("anthropic", "https://www.anthropic.com/news/example") > source_tier_weight("reddit", "https://reddit.com/r/LocalLLaMA/comments/1")


def test_event_cluster_merges_same_event_but_not_same_company_noise() -> None:
    items = [
        _item(
            "hackernews",
            "Higher usage limits for Claude and a compute deal with SpaceX",
            "https://www.anthropic.com/news/higher-limits-spacex",
            "Anthropic announced a SpaceX compute partnership and higher Claude Code usage limits.",
            500,
        ),
        _item(
            "x_twitter",
            "We’ve agreed to a partnership with @SpaceX that will substantially increase our compute capacity.",
            "https://x.com/claudeai/status/2052060691893227611",
            "The SpaceX compute deal increases Claude capacity and usage limits.",
            1000,
        ),
        _item(
            "x_twitter",
            "Effective today, doubling Claude Code’s 5-hour rate limits for Pro, Max, and Team plans",
            "https://x.com/claudeai/status/2052060693269008586",
            "Claude Code rate limits increase after the SpaceX compute capacity deal.",
            700,
        ),
        _item(
            "x_twitter",
            "Live from Code with Claude: we're launching dreaming in Claude Managed Agents",
            "https://x.com/claudeai/status/2052067399088664981",
            "Dreaming extracts patterns from agent sessions and is unrelated to the SpaceX compute deal.",
            600,
        ),
    ]

    clusters = cluster_content_items(items)
    spacex = next(c for c in clusters if "spacex" in c.title.lower())

    assert len(spacex.items) == 3
    assert spacex.source_tier == "T1"
    assert spacex.url == "https://www.anthropic.com/news/higher-limits-spacex"
    assert all("dreaming" not in item.title.lower() for item in spacex.items)


def test_event_payload_exposes_dimension_scores_and_supporting_sources() -> None:
    clusters = cluster_content_items([
        _item("anthropic", "Higher usage limits for Claude and a compute deal with SpaceX", "https://www.anthropic.com/news/higher-limits-spacex", "Compute capacity and rate limits.", 100),
        _item("x_twitter", "SpaceX compute capacity increases Claude usage limits", "https://x.com/claudeai/status/1", "SpaceX capacity and Claude usage limits.", 50),
    ])

    payload = event_cluster_payload(clusters[0])

    assert payload["event_id"]
    assert payload["event_score"] > 0
    assert payload["source_tier"] == "T1"
    assert payload["dimension_scores"]["lee_relevance"] > 0
    assert payload["cluster_size"] == 2
    assert "supporting_sources" in payload
    assert "supporting_sources_markdown" in payload


def test_objective_dimension_schema_and_fallback_are_stable() -> None:
    schema = objective_dimension_schema()
    fallback = {
        "technical_importance": 1.0,
        "product_impact": 2.0,
        "business_impact": 3.0,
        "novelty": 4.0,
        "lee_relevance": 5.0,
        "confidence": 6.0,
    }

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(fallback)
    exported = json.loads(Path("config/schema/ai_event_dimensions.schema.json").read_text(encoding="utf-8"))
    assert exported["additionalProperties"] is False
    assert set(exported["required"]) == set(schema["required"])
    assert set(exported["properties"]) == set(schema["properties"])
    assert coerce_dimension_scores({"technical_importance": "bad"}, fallback) == fallback
    assert coerce_dimension_scores({**fallback, "confidence": 12}, fallback)["confidence"] == 10.0


def test_event_ranker_weights_are_configurable_but_deterministic() -> None:
    items = [
        _item("anthropic", "Claude Code usage limits increase after SpaceX compute deal", "https://www.anthropic.com/news/limits", "Claude Code usage limits", 100),
        _item("x_twitter", "SpaceX compute capacity increases Claude usage limits", "https://x.com/claudeai/status/1", "Claude Code usage limits", 80),
    ]

    base = cluster_content_items(items)[0].event_score
    tuned = cluster_content_items(items, config={"ai_daily": {"event_ranking": {"heat_weight": 0.0}}})[0].event_score

    assert tuned != base
    assert cluster_content_items(items, config={"ai_daily": {"event_ranking": {"heat_weight": 0.0}}})[0].event_score == tuned


def test_event_ranker_topic_weights_are_configurable() -> None:
    items = [
        _item("github", "Release coding agent runtime", "https://github.com/example/agent/releases/v1", "coding agent runtime release", 100),
    ]

    base = cluster_content_items(items)[0]
    tuned = cluster_content_items(items, config={"ai_daily": {"event_ranking": {"topic_weights": {"Developer Tools": 0.5}}}})[0]

    assert base.category == "Developer Tools"
    assert tuned.topic_weight == 0.5
    assert tuned.event_score < base.event_score


def test_github_release_prerelease_is_strongly_downweighted() -> None:
    cluster = cluster_content_items([
        _item(
            "github",
            "[Release] ollama/ollama v0.30.0-rc20",
            "https://github.com/ollama/ollama/releases/tag/v0.30.0-rc20",
            "Ollama release candidate update for local LLM runtime.",
            100000,
        ),
    ])[0]

    assert cluster.score_breakdown["release_update_kind"] == "prerelease"
    assert cluster.score_breakdown["release_update_multiplier"] == 0.25
    assert cluster.event_score == cluster.score_breakdown["final_score"]


def test_github_release_major_is_discounted_less_than_patch() -> None:
    major = cluster_content_items([
        _item(
            "github",
            "[Release] example/agent v2.0.0",
            "https://github.com/example/agent/releases/tag/v2.0.0",
            "Major AI coding agent runtime release with breaking architecture changes.",
            1000,
        ),
    ])[0]
    patch = cluster_content_items([
        _item(
            "github",
            "[Release] example/agent v2.0.1",
            "https://github.com/example/agent/releases/tag/v2.0.1",
            "Patch AI coding agent runtime release with bug fixes.",
            1000,
        ),
    ])[0]

    assert major.score_breakdown["release_update_kind"] == "major"
    assert patch.score_breakdown["release_update_kind"] == "patch"
    assert major.score_breakdown["release_update_multiplier"] > patch.score_breakdown["release_update_multiplier"]
    assert major.event_score > patch.event_score


def test_github_evergreen_repo_stars_do_not_dominate_daily_rank() -> None:
    tensorflow = ContentItem(
        source="github",
        source_type=SourceType.API,
        title="tensorflow/tensorflow: An Open Source Machine Learning Framework for Everyone",
        url="https://github.com/tensorflow/tensorflow",
        content="An Open Source Machine Learning Framework for Everyone",
        score=195199,
        tags=["machine-learning", "deep-learning", "tensorflow"],
        extra={"stars": 195199, "forks": 75320, "language": "C++"},
    )

    discounted = cluster_content_items([tensorflow])[0]
    undiscounted = cluster_content_items(
        [tensorflow],
        config={"ai_daily": {"event_ranking": {"github_evergreen_repo_star_threshold": 999999999}}},
    )[0]

    assert discounted.score_breakdown["heat"] < undiscounted.score_breakdown["heat"]
    assert discounted.score_breakdown["github_evergreen_repo_multiplier"] == 0.55
    assert discounted.event_score < undiscounted.event_score


def test_github_generic_ml_framework_repos_do_not_cluster_together() -> None:
    clusters = cluster_content_items([
        ContentItem(
            source="github",
            source_type=SourceType.API,
            title="tensorflow/tensorflow: An Open Source Machine Learning Framework for Everyone",
            url="https://github.com/tensorflow/tensorflow",
            content="An Open Source Machine Learning Framework for Everyone",
            score=195199,
            tags=["machine-learning", "deep-learning", "tensorflow"],
            extra={"stars": 195199},
        ),
        ContentItem(
            source="github",
            source_type=SourceType.API,
            title="huggingface/transformers: 🤗 Transformers: the model-definition framework for state-of-the-art machine learning models in text, vision, audio, and multimodal models, for both inference and training. ",
            url="https://github.com/huggingface/transformers",
            content="🤗 Transformers: the model-definition framework for state-of-the-art machine learning models in text, vision, audio, and multimodal models, for both inference and training. ",
            score=160793,
            tags=["llm", "machine-learning", "transformer", "pytorch"],
            extra={"stars": 160793},
        ),
    ])

    titles = {cluster.title for cluster in clusters}
    assert len(clusters) == 2
    assert any("tensorflow/tensorflow" in title for title in titles)
    assert any("huggingface/transformers" in title for title in titles)


def test_same_non_social_url_merges_even_with_different_titles() -> None:
    clusters = cluster_content_items([
        _item(
            "google_blog",
            "Gemini 3.5: frontier intelligence with action",
            "https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5/",
            "Official Gemini 3.5 launch post.",
            0,
        ),
        _item(
            "hackernews",
            "Gemini 3.5 Flash",
            "https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5/",
            "HN discussion of the same Gemini 3.5 launch.",
            1000,
        ),
    ])

    assert len(clusters) == 1
    assert clusters[0].score_breakdown["cluster_size_multiplier"] > 1.0


def test_single_source_show_hn_claim_is_downweighted() -> None:
    forge = _item(
        "hackernews",
        "Show HN: Forge – Guardrails take an 8B model from 53% to 99% on agentic tasks",
        "https://github.com/antoinezambelli/forge",
        "Self-reported guardrails benchmark for local LLM agentic workflows.",
        1547.38,
    )

    discounted = cluster_content_items([forge])[0]
    undiscounted = cluster_content_items(
        [forge],
        config={"ai_daily": {"event_ranking": {"single_source_show_hn_multiplier": 1.0}}},
    )[0]

    assert discounted.score_breakdown["single_source_show_hn_multiplier"] == 0.85
    assert discounted.event_score < undiscounted.event_score



def test_top_headlines_source_quota_selects_alternatives_when_available() -> None:
    clusters = cluster_content_items([
        _item("github", "alpha/agent-runtime: coding agent workflow", "https://github.com/alpha/agent-runtime", "coding agent workflow", 1000),
        _item("github", "beta/agent-runtime: coding agent workflow", "https://github.com/beta/agent-runtime", "coding agent workflow", 900),
        _item("anthropic", "Claude coding agent product update", "https://www.anthropic.com/news/claude-agent-update", "Claude coding agent product update", 800),
        _item("openai", "OpenAI agent product update", "https://openai.com/index/agent-product-update/", "OpenAI agent product update", 700),
    ])

    selected = select_top_event_clusters(
        clusters,
        3,
        config={"ai_daily": {"event_ranking": {"top_headlines_quota": {"max_per_source": 1, "max_per_umbrella": 0, "max_per_topic": {}}}}},
    )

    assert len(selected) == 3
    assert sum(1 for cluster in selected if cluster.source == "github") == 1
    assert {cluster.source for cluster in selected} >= {"anthropic", "openai"}


def test_top_headlines_topic_quota_and_required_lanes() -> None:
    clusters = cluster_content_items([
        _item("x_twitter", "Google Gemini launch reaction", "https://x.com/a/status/1", "Google Gemini launch reaction", 1000),
        _item("x_twitter", "Meta Llama community debate", "https://x.com/b/status/2", "Meta Llama community debate unrelated to Google", 900),
        _item("github", "agent-tools/mcp-runtime: Developer workflow automation", "https://github.com/agent-tools/mcp-runtime", "coding agent developer tool", 200),
        _item("github", "workflow-kit/agent-cli: Developer automation", "https://github.com/workflow-kit/agent-cli", "coding agent developer CLI", 150),
    ])

    selected = select_top_event_clusters(
        clusters,
        3,
        config={"ai_daily": {"event_ranking": {"top_headlines_quota": {
            "max_per_source": 3,
            "max_per_umbrella": 0,
            "max_per_topic": {"Social & Community": 1},
            "min_required_if_available": {"Developer Tools": 1},
        }}}},
    )

    assert len(selected) == 3
    assert sum(1 for cluster in selected if cluster.category == "Social & Community") == 1
    assert any(cluster.category == "Developer Tools" for cluster in selected)


def test_google_io_gemini_umbrella_only_gets_one_top_headline_slot() -> None:
    clusters = cluster_content_items([
        _item(
            "google_blog",
            "Gemini 3.5 Flash",
            "https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5/",
            "Google I/O 2026 launches Gemini 3.5 Flash for agents and coding.",
            1000,
        ),
        _item(
            "x_twitter",
            "We’re dropping Gemini Omni for Google I/O 2026",
            "https://x.com/GoogleDeepMind/status/2056786446636212467",
            "Gemini Omni is a new multimodal world model from Google DeepMind.",
            900,
        ),
        _item(
            "hackernews",
            "Google changes its search box",
            "https://blog.google/products-and-platforms/products/search/search-io-2026/",
            "Google I/O 2026 search box launch uses Gemini 3.5 Flash.",
            800,
        ),
        _item("anthropic", "Claude Code usage limits increase", "https://www.anthropic.com/news/limits", "Claude Code usage limits", 700),
        _item("github", "agent-tools/mcp-runtime", "https://github.com/agent-tools/mcp-runtime", "MCP developer tool", 600),
    ])

    google_io = [cluster for cluster in clusters if cluster.canonical_event_key.startswith("umbrella:google-io-gemini")]
    assert len(google_io) >= 3

    selected = select_top_event_clusters(
        clusters,
        3,
        config={"ai_daily": {"event_ranking": {"top_headlines_quota": {"max_per_source": 3, "max_per_umbrella": 1, "max_per_topic": {}}}}},
    )

    assert sum(1 for cluster in selected if cluster.canonical_event_key.startswith("umbrella:google-io-gemini")) == 1
    assert any(cluster.source == "anthropic" for cluster in selected)
