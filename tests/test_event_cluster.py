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


def test_conference_playlist_sessions_do_not_merge_on_generic_agent_terms() -> None:
    items = [
        _item(
            "youtube",
            "AI Dev 26 x SF | William Imoh & Charlie Wood: Closing the Care Gap",
            "https://www.youtube.com/watch?v=0tTwDK_o9Oc",
            "AI agents in clinical workflows require privacy, retrieval accuracy, and deployment flexibility.",
            76923,
        ),
        _item(
            "youtube",
            "AI Dev 26 x SF | Eda Zhou & Mahdi Ghodsi: Building Personal AI Agents with Open Source Models",
            "https://www.youtube.com/watch?v=QVawxdMRZtk",
            "Workshop about building personal AI agents using open-source models on AMD GPUs.",
            57971,
        ),
        _item(
            "youtube",
            "AI Dev 26 x SF | Eli Schilling: Hands On Agent Context & Memory Engineering with Oracle AI Database",
            "https://www.youtube.com/watch?v=qEUxIvaIgsg",
            "Technical overview of memory and context systems for autonomous AI agents.",
            0,
        ),
    ]

    clusters = cluster_content_items(items)

    assert len(clusters) == 3
    assert all(cluster.score_breakdown["cluster_size_multiplier"] == 1.0 for cluster in clusters)
    assert all(cluster.score_breakdown["cross_source_multiplier"] == 1.0 for cluster in clusters)


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


def test_single_source_hn_project_page_does_not_get_full_ai_topic_boost() -> None:
    item = _item(
        "hackernews",
        "DeepSeek Reasonix: DeepSeek native coding agent with high caching and low cost",
        "https://esengine.github.io/DeepSeek-Reasonix/",
        "Project page for a native coding agent launch demo.",
        8000,
    )
    item.tags = ["hackernews", "ai"]

    cluster = cluster_content_items(
        [item],
        config={"ai_daily": {"event_ranking": {"topic_weights": {"AI Models & Agent": 1.4}}}},
    )[0]

    assert cluster.category == "AI Models & Agent"
    assert cluster.score_breakdown["topic_multiplier"] == 1.05
    assert cluster.score_breakdown["single_source_hn_launch_multiplier"] == 0.72


def test_hn_discovered_official_link_uses_canonical_source_tier_and_topic_boost() -> None:
    item = _item(
        "hackernews",
        "OpenAI announces new production agent platform",
        "https://openai.com/index/production-agent-platform/",
        "Official OpenAI launch with API, agent workflow, deployment, and enterprise details.",
        900,
    )
    item.tags = ["hackernews", "ai"]

    cluster = cluster_content_items(
        [item],
        config={"ai_daily": {"event_ranking": {"topic_weights": {"AI Models & Agent": 1.4}}}},
    )[0]

    assert cluster.source == "hackernews"
    assert cluster.source_tier == "T1"
    assert cluster.source_weight >= 1.5
    assert cluster.score_breakdown["topic_multiplier"] == 1.4
    assert cluster.score_breakdown["single_source_hn_launch_multiplier"] == 1.0


def test_shared_deepseek_entity_alone_does_not_merge_unrelated_events() -> None:
    reasonix = _item(
        "hackernews",
        "DeepSeek Reasonix: DeepSeek native coding agent with high caching and low cost",
        "https://esengine.github.io/DeepSeek-Reasonix/",
        "Project page for a native coding agent launch demo.",
        8000,
    )
    pricing = _item(
        "deepseek",
        "DeepSeek lowers off-peak API prices",
        "https://api-docs.deepseek.com/news/news250521",
        "DeepSeek announced discounted off-peak inference pricing for API users.",
        600,
    )

    clusters = cluster_content_items([reasonix, pricing])

    assert len(clusters) == 2
    titles = "\n".join(cluster.title for cluster in clusters).lower()
    assert "reasonix" in titles
    assert "prices" in titles or "pricing" in titles


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


def test_opencode_patch_release_with_controls_does_not_get_major_release_floor() -> None:
    patch = cluster_content_items([
        _item(
            "github",
            "[Release] sst/opencode v1.15.12",
            "https://github.com/sst/opencode/releases/tag/v1.15.12",
            (
                "Patch release adding ACP and WebSocket controls for the coding agent CLI, "
                "plus bug fixes and compatibility updates."
            ),
            5000,
        ),
    ])[0]

    assert patch.score_breakdown["release_update_kind"] == "patch"
    assert patch.score_breakdown["release_update_multiplier"] == 0.35
    assert patch.score_breakdown["top_headline_release_eligible"] is False
    assert patch.score_breakdown["architecture_critical_release_signal"] == 0.0
    assert patch.score_breakdown["architecture_critical_release_multiplier"] == 1.0


def test_opencode_release_top_headline_eligibility_by_semver_size() -> None:
    cases = [
        ("[Release] sst/opencode v1.16.0", "https://github.com/sst/opencode/releases/tag/v1.16.0", "minor", False),
        ("[Release] sst/opencode v1.15.12", "https://github.com/sst/opencode/releases/tag/v1.15.12", "patch", False),
        ("[Release] sst/opencode latest", "https://github.com/sst/opencode/releases/latest", "unknown", False),
        ("[Release] sst/opencode v2.0.0", "https://github.com/sst/opencode/releases/tag/v2.0.0", "major", True),
    ]

    for title, url, expected_kind, expected_eligible in cases:
        cluster = cluster_content_items([
            _item(
                "github",
                title,
                url,
                "OpenCode release update for the coding agent CLI.",
                5000,
            ),
        ])[0]

        assert cluster.score_breakdown["release_update_kind"] == expected_kind
        assert cluster.score_breakdown["top_headline_release_eligible"] is expected_eligible


def test_opencode_patch_release_is_not_selected_for_top_headlines_even_when_devtools_lane_is_required() -> None:
    clusters = cluster_content_items([
        _item(
            "github",
            "Release anomalyco/opencode v1.15.13",
            "https://github.com/anomalyco/opencode/releases/tag/v1.15.13",
            (
                "Small opencode patch release with ACP/WebSocket controls, session metadata, "
                "and adaptive reasoning bug fixes."
            ),
            100000,
        ),
        _item(
            "youtube",
            "Spec-Driven Testing for Agents With A Brain the Size of A Planet",
            "https://www.youtube.com/watch?v=agent-spec",
            "Production agent safety talk about spec-driven testing by an expert researcher.",
            5000,
        ),
        _item(
            "finance",
            "SoftBank plans 75 billion euros of AI investments in France",
            "https://www.cnbc.com/ai-investment-france",
            "AI data center investment with 5GW capacity and financing details.",
            4000,
        ),
    ])

    selected = select_top_event_clusters(
        clusters,
        3,
        config={
            "ai_daily": {
                "event_ranking": {
                    "top_headlines_quota": {
                        "enabled": True,
                        "min_required_if_available": {"Developer Tools": 1},
                        "max_per_topic": {},
                    }
                }
            }
        },
    )

    assert all("opencode" not in cluster.title.lower() for cluster in selected)


def test_opencode_patch_release_is_not_selected_when_quota_is_disabled() -> None:
    clusters = cluster_content_items([
        _item(
            "github",
            "[Release] opencode v1.15.14",
            "https://github.com/sst/opencode/releases/tag/v1.15.14",
            "Patch release with ACP controls, WebSocket session handling, and bug fixes.",
            100000,
        ),
        _item(
            "finance",
            "Nvidia reports record AI server demand",
            "https://www.nvidia.com/news/ai-server-demand",
            "Primary earnings data shows sustained AI server demand and revenue growth.",
            5000,
        ),
    ])

    selected = select_top_event_clusters(
        clusters,
        2,
        config={"ai_daily": {"event_ranking": {"top_headlines_quota": {"enabled": False}}}},
    )

    assert all("opencode" not in cluster.title.lower() for cluster in selected)
    assert any("nvidia" in cluster.title.lower() for cluster in selected)


def test_opencode_unknown_release_is_not_selected_during_quota_relaxation() -> None:
    clusters = cluster_content_items([
        _item(
            "github",
            "[Release] OpenCode latest update",
            "https://github.com/sst/opencode/releases/latest",
            "Unknown-version OpenCode release update with coding agent CLI fixes.",
            100000,
        ),
        _item(
            "youtube",
            "Production agent reliability interview",
            "https://www.youtube.com/watch?v=agent-reliability",
            "Expert interview on production agent reliability and evals.",
            4000,
        ),
    ])

    selected = select_top_event_clusters(
        clusters,
        2,
        config={
            "ai_daily": {
                "event_ranking": {
                    "top_headlines_quota": {
                        "enabled": True,
                        "max_per_source": 0,
                        "max_per_topic": {},
                    }
                }
            }
        },
    )

    assert all("opencode" not in cluster.title.lower() for cluster in selected)


def test_architecture_critical_major_release_beats_generic_interview() -> None:
    clusters = cluster_content_items([
        _item(
            "github",
            "[Release] example/agent-runtime v2.0.0",
            "https://github.com/example/agent-runtime/releases/tag/v2.0.0",
            (
                "Major v2.0.0 release with breaking architecture changes, a full runtime "
                "scheduler rewrite, new inference serving architecture, and migration guide."
            ),
            1200,
        ),
        _item(
            "youtube",
            "Founder interview about AI coding habits",
            "https://www.youtube.com/watch?v=generic-interview",
            "A broad interview about AI coding habits, startup lessons, and productivity.",
            1200,
        ),
    ])

    major = next(cluster for cluster in clusters if "v2.0.0" in cluster.title)
    interview = next(cluster for cluster in clusters if "interview" in cluster.title.lower())

    assert major.score_breakdown["release_update_kind"] in {"major", "architecture_critical"}
    assert major.score_breakdown["architecture_critical_release_signal"] > 0
    assert major.event_score > interview.event_score


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


def test_frontier_model_release_merges_official_social_and_outranks_single_paper() -> None:
    clusters = cluster_content_items([
        _item(
            "anthropic",
            "Claude Opus 4.8",
            "https://www.anthropic.com/news/claude-opus-4-8",
            "Official release: Claude Opus 4.8 is available today at the same price with Claude Code dynamic workflows, effort control, and cheaper fast mode.",
            250,
        ),
        _item(
            "x_twitter",
            "Introducing Claude Opus 4.8: it builds on Opus 4.7 with sharper judgment and longer independent work",
            "https://x.com/claudeai/status/2060042702150930686",
            "Official ClaudeAI launch post: Opus 4.8 is available today for agentic coding workflows.",
            900,
        ),
        _item(
            "alphaxiv",
            "Agent Explorative Policy Optimization for Multimodal Agentic Reasoning",
            "https://www.alphaxiv.org/abs/2605.28774",
            "Paper proposing AXPO for multimodal agentic reasoning and tool use; +1.8pp average over GRPO on nine benchmarks.",
            1200,
        ),
    ])

    opus = next(cluster for cluster in clusters if "opus 4.8" in cluster.title.lower())
    axpo = next(cluster for cluster in clusters if "explorative policy" in cluster.title.lower())

    assert len(opus.items) == 2
    assert opus.source == "anthropic"
    assert opus.score_breakdown["frontier_model_release_multiplier"] > 1.0
    assert opus.canonical_event_key.startswith("umbrella:frontier-release:anthropic:claude-opus-4-8")
    assert axpo.score_breakdown["paper_quality_multiplier"] <= 1.08

    selected = select_top_event_clusters(clusters, 2)
    assert selected[0].event_id == opus.event_id
    assert selected[1].event_id == axpo.event_id


def test_non_big3_major_model_release_from_social_text_still_routes_as_ai_models() -> None:
    cluster = cluster_content_items([
        _item(
            "x_twitter",
            "MiniMax M3 major model upgrade",
            "https://x.com/example/status/minimax-m3",
            "MiniMax M3 is a major model upgrade with 1M context, MSA sparse attention, native multimodal training, API pricing update, and broader availability.",
            38,
        ),
    ])[0]

    assert cluster.category == "AI Models & Agent"
    assert cluster.score_breakdown["frontier_model_release_multiplier"] > 1.0
    assert cluster.canonical_event_key.startswith("umbrella:frontier-release:minimax:minimax-m3")


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
        config={
            "ai_daily": {
                "event_ranking": {
                    "single_source_show_hn_multiplier": 1.0,
                    "single_source_hn_launch_multiplier": 1.0,
                }
            }
        },
    )[0]

    assert discounted.score_breakdown["single_source_hn_launch_multiplier"] == 0.72
    assert discounted.score_breakdown["single_source_show_hn_multiplier"] == 0.72
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

    google_campaign = [
        cluster
        for cluster in clusters
        if cluster.canonical_event_key.startswith("umbrella:google-io-gemini")
        or cluster.canonical_event_key.startswith("umbrella:frontier-release:google:gemini")
    ]
    assert len(google_campaign) >= 3

    selected = select_top_event_clusters(
        clusters,
        3,
        config={"ai_daily": {"event_ranking": {"top_headlines_quota": {"max_per_source": 3, "max_per_umbrella": 1, "max_per_topic": {}}}}},
    )

    assert sum(
        1
        for cluster in selected
        if cluster.canonical_event_key.startswith("umbrella:google-io-gemini")
        or cluster.canonical_event_key.startswith("umbrella:frontier-release:google:gemini")
    ) == 1
    assert any(cluster.source == "anthropic" for cluster in selected)


def test_paradigm_shift_route_merges_elf_cola_dlm_discussion_and_boosts() -> None:
    clusters = cluster_content_items([
        _item(
            "x_twitter",
            "ELF: Embedded Language Flows",
            "https://x.com/askalphaxiv/status/2055544284077211810",
            "MIT researchers including Kaiming He propose Embedded Language Flows, a flow matching alternative for language generation in continuous embeddings.",
            120,
        ),
        _item(
            "huggingface",
            "ByteDance-Seed/Cola-DLM · Hugging Face",
            "https://huggingface.co/ByteDance-Seed/Cola-DLM",
            "Cola DLM (Continuous Latent Diffusion Language Model) is a hierarchical continuous latent-space diffusion language model using Flow Matching.",
            90,
        ),
        _item(
            "zhihu",
            "何恺明推出ELF、字节开源Cola DLM，两者都放弃「预测下一个token」，能否开辟大模型新路径？",
            "https://www.zhihu.com/question/2055544284077211810",
            "讨论 non-autoregressive language model alternatives: continuous latent, diffusion language model, and not next-token prediction.",
            400,
        ),
        _item(
            "alphaxiv",
            "Continual learning for LLM agents without simply scaling model size",
            "https://www.alphaxiv.org/abs/2605.28774",
            "A separate paradigm-shift route: online learning and test-time adaptation as an alternative to scaling larger transformer models.",
            1200,
        ),
    ])

    route = next(cluster for cluster in clusters if "cola-dlm" in cluster.title.lower() or "embedded language flows" in cluster.title.lower())

    assert len(route.items) == 4
    assert route.category == "AI Models & Agent"
    assert route.canonical_event_key == "umbrella:route:paradigm-shift"
    assert route.score_breakdown["paradigm_shift_route_multiplier"] > 1.0
    assert route.score_breakdown["cross_source_multiplier"] > 1.0
    assert route.dimension_scores["technical_importance"] >= 5.0


def test_image_latent_diffusion_flow_matching_does_not_trigger_paradigm_shift_route() -> None:
    cluster = cluster_content_items([
        _item(
            "alphaxiv",
            "JLT: Clean-Latent Prediction for Latent Diffusion Transformers",
            "https://www.alphaxiv.org/abs/2605.27102",
            "Flow matching with clean-data prediction for images mapped into learned latent space. On ImageNet, a latent diffusion Transformer improves FID.",
            300,
        ),
    ])[0]

    assert cluster.canonical_event_key == ""
    assert cluster.score_breakdown["paradigm_shift_route_multiplier"] == 1.0


def test_different_alphaxiv_papers_do_not_cluster_on_generic_theme_tokens() -> None:
    clusters = cluster_content_items([
        _item(
            "alphaxiv",
            "Agent Reasoning Benchmark for Multimodal Models",
            "https://www.alphaxiv.org/abs/2605.10001",
            "Paper benchmark for LLM agent reasoning, multimodal tool use, and evaluation.",
            1500,
        ),
        _item(
            "alphaxiv",
            "Agent Reasoning Benchmark for Long Context Models",
            "https://www.alphaxiv.org/abs/2605.10002",
            "Paper benchmark for LLM agent reasoning, long context memory, and evaluation.",
            1400,
        ),
    ])

    assert len(clusters) == 2
    assert all(cluster.score_breakdown["cluster_size_multiplier"] == 1.0 for cluster in clusters)
    assert all(cluster.score_breakdown["cross_source_multiplier"] == 1.0 for cluster in clusters)


def test_different_arxiv_and_alphaxiv_ids_do_not_get_cross_source_bonus() -> None:
    arxiv = _item(
        "arxiv",
        "Scaling Agent Reasoning Benchmarks for Tool Use",
        "https://arxiv.org/abs/2605.20001",
        "Paper benchmark for LLM agent reasoning and tool use.",
        1200,
    )
    arxiv.arxiv_id = "2605.20001"
    alphaxiv = _item(
        "alphaxiv",
        "Scaling Agent Reasoning Benchmarks for Tool Use",
        "https://www.alphaxiv.org/abs/2605.20002",
        "Paper benchmark for LLM agent reasoning and tool use.",
        1100,
    )

    clusters = cluster_content_items([arxiv, alphaxiv])

    assert len(clusters) == 2
    assert all(cluster.score_breakdown["cluster_size_multiplier"] == 1.0 for cluster in clusters)
    assert all(cluster.score_breakdown["cross_source_multiplier"] == 1.0 for cluster in clusters)


def test_community_practice_benchmark_signal_is_visible_but_not_authoritative() -> None:
    cluster = cluster_content_items([
        _item(
            "reddit",
            "TurboQuant current status after the hype faded",
            "https://reddit.com/r/LocalLLaMA/comments/1sm6d2k/what_is_the_current_status_with_turbo_quant/",
            (
                "Community benchmark on real hardware using llama.cpp reports perplexity PPL, "
                "VRAM, tok/s throughput, and says TurboQuant does not scale well enough to be "
                "a production default for larger local inference setups."
            ),
            450,
        )
    ])[0]
    payload = event_cluster_payload(cluster)

    assert cluster.score_breakdown["community_practice_signal"] > 0
    assert cluster.dimension_scores["lee_relevance"] > 0
    assert payload["score_breakdown"]["community_practice_signal"] == cluster.score_breakdown["community_practice_signal"]
    assert cluster.score_breakdown.get("paper_subtype") == "none"


def test_expert_podcast_youtube_signal_beats_generic_learning_media() -> None:
    expert = cluster_content_items([
        _item(
            "youtube",
            "Devin’s 80% Moment: Background Agents, 7x PRs, and end of hand-held coding",
            "https://www.youtube.com/watch?v=devin80",
            "Cognition founders discuss production coding agents, pull requests, workflow, scale, and enterprise ROI.",
            900,
        )
    ])[0]
    generic = cluster_content_items([
        _item(
            "youtube",
            "LangChain Academy New Course: Introduction to LangSmith Deployment",
            "https://www.youtube.com/watch?v=course",
            "A course tutorial and introduction webinar for getting started with LangSmith deployment.",
            900,
        )
    ])[0]

    assert expert.score_breakdown["expert_media_signal"] >= 3.0
    assert expert.score_breakdown["expert_media_multiplier"] > 1.0
    assert generic.score_breakdown["generic_learning_media_signal"] > 0
    assert generic.score_breakdown["generic_learning_media_multiplier"] < 1.0
    assert expert.event_score > generic.event_score


def test_high_signal_youtube_is_not_hard_capped_by_source_quota() -> None:
    items = [
        _item(
            "youtube",
            "OpenAI CTO on production agents and token cost architecture",
            "https://www.youtube.com/watch?v=expert-openai",
            "OpenAI CTO discusses production agents, inference cost, scaling, roadmap, and enterprise workflow.",
            1000,
        ),
        _item(
            "youtube",
            "Anthropic researcher on Claude Code workflow and agent memory",
            "https://www.youtube.com/watch?v=expert-anthropic",
            "Anthropic researcher discusses Claude Code, agent memory, deployment, workflow, and enterprise ROI.",
            990,
        ),
        _item(
            "youtube",
            "NVIDIA architect on inference serving and GPU economics",
            "https://www.youtube.com/watch?v=expert-nvidia",
            "NVIDIA architect explains inference serving, scaling, token throughput, cost, and production deployment.",
            980,
        ),
        _item(
            "youtube",
            "Cursor founder on background coding agents and PR workflow",
            "https://www.youtube.com/watch?v=expert-cursor",
            "Cursor founder discusses background agents, pull requests, coding workflow, roadmap, and enterprise adoption.",
            970,
        ),
        _item("github", "Useful developer tool release", "https://github.com/example/tool/releases/v1.0.0", "developer tool release", 100),
    ]

    clusters = cluster_content_items(items)
    selected = select_top_event_clusters(
        clusters,
        4,
        config={
            "ai_daily": {
                "event_ranking": {
                    "top_headlines_quota": {
                        "enabled": True,
                        "max_per_source": 3,
                        "max_per_topic": {},
                        "min_required_if_available": {"Developer Tools": 0, "Infrastructure & Systems": 0},
                    }
                }
            }
        },
    )

    assert len(selected) == 4
    assert all(cluster.source == "youtube" for cluster in selected)


def test_videos_and_podcasts_have_no_fixed_topic_cap_but_generic_items_are_downranked() -> None:
    items = [
        _item(
            "youtube",
            "LangChain Academy Course: LangSmith Deployment tutorial",
            "https://www.youtube.com/watch?v=generic-course",
            "A course tutorial and webinar introduction for getting started with LangSmith deployment.",
            1200,
        ),
        _item(
            "youtube",
            "YC Paper Club discusses inference and diffusion papers",
            "https://www.youtube.com/watch?v=paper-club",
            "Paper club recap and learning discussion of recent papers, inference, and diffusion.",
            1180,
        ),
        _item(
            "youtube",
            "Beginner MCP Kubernetes podcast episode",
            "https://www.youtube.com/watch?v=generic-podcast",
            "Podcast tutorial for learning MCP on Kubernetes with introductory examples.",
            1160,
        ),
        _item(
            "youtube",
            "Devin 80% Moment: founders on background agents and 7x PRs",
            "https://www.youtube.com/watch?v=devin80",
            "Cognition founders disclose production coding agents, pull requests, workflow, scale, and enterprise ROI.",
            820,
        ),
        _item(
            "youtube",
            "OpenAI CTO on production agents and token cost architecture",
            "https://www.youtube.com/watch?v=expert-openai",
            "OpenAI CTO discusses production agents, inference cost, scaling, roadmap, and enterprise workflow.",
            800,
        ),
        _item("anthropic", "Claude Code enterprise deployment update", "https://www.anthropic.com/news/claude-code-enterprise", "Claude Code enterprise production deployment update for coding agents.", 780),
    ]

    selected = select_top_event_clusters(cluster_content_items(items), 5)
    selected_titles = [cluster.title.lower() for cluster in selected]

    assert len(selected) == 5
    assert sum(1 for cluster in selected if cluster.category == "Videos & Podcasts") >= 4
    assert any(
        cluster.score_breakdown["generic_learning_media_multiplier"] < 1.0
        for cluster in selected
        if cluster.category == "Videos & Podcasts"
    )
    assert any("devin 80%" in title for title in selected_titles)
    assert any("openai cto" in title for title in selected_titles)


def test_official_ai_funding_hard_evidence_beats_generic_podcast() -> None:
    clusters = cluster_content_items([
        _item(
            "anthropic",
            "Anthropic raises $4B to expand AI infrastructure capacity",
            "https://www.anthropic.com/news/series-f",
            "Official funding announcement: $4 billion financing for AI infrastructure, compute capacity, and model training.",
            500,
        ),
        _item(
            "apple_podcast",
            "AI founders discuss productivity routines",
            "https://podcasts.apple.com/us/podcast/generic-ai-productivity/id1?i=1001",
            "A general podcast interview about AI productivity routines and startup lessons.",
            2000,
        ),
    ])

    funding = next(cluster for cluster in clusters if "raises $4b" in cluster.title.lower())
    podcast = next(cluster for cluster in clusters if "podcast" in cluster.source or "routines" in cluster.title.lower())

    assert funding.score_breakdown["hard_evidence_signal"] > 0
    assert funding.event_score > podcast.event_score


def test_ai_revenue_hard_data_beats_generic_interview() -> None:
    clusters = cluster_content_items([
        _item(
            "finance",
            "NVIDIA reports AI server revenue and Blackwell demand above guidance",
            "https://www.nvidia.com/en-us/about-nvidia/investor-relations/results/",
            "Earnings report: AI server revenue, data center demand, Blackwell GPU backlog, and guidance all rose.",
            600,
        ),
        _item(
            "youtube",
            "Interview: how teams use AI tools",
            "https://www.youtube.com/watch?v=generic-ai-interview",
            "A general interview about AI tools, team habits, and productivity lessons.",
            1800,
        ),
    ])

    revenue = next(cluster for cluster in clusters if "revenue" in cluster.title.lower())
    interview = next(cluster for cluster in clusters if "interview" in cluster.title.lower())

    assert revenue.score_breakdown["hard_evidence_signal"] > 0
    assert revenue.event_score > interview.event_score


def test_research_papers_do_not_consume_more_than_two_top_headline_slots_when_alternatives_exist() -> None:
    clusters = cluster_content_items([
        _item("alphaxiv", "Agent Policy Optimization for Multimodal Agents", "https://www.alphaxiv.org/abs/2605.1", "Paper benchmark for multimodal agent reasoning and tool use.", 1400),
        _item("alphaxiv", "LongCat Video Avatar Technical Report", "https://www.alphaxiv.org/abs/2605.2", "Technical report with benchmark and deployment details for video avatars.", 1300),
        _item("alphaxiv", "MONA Optimizer for Scalable Language Model Training", "https://www.alphaxiv.org/abs/2605.3", "Paper benchmark for LLM training optimizer scaling.", 1200),
        _item("alphaxiv", "Edge Cloud Speech Translation", "https://www.alphaxiv.org/abs/2605.4", "Paper benchmark for many-to-many speech translation.", 1100),
        _item("anthropic", "Claude Code production deployment update", "https://www.anthropic.com/news/claude-code-production", "Official production update for coding agents and enterprise deployment.", 900),
        _item("github", "agent-runtime v1.0 major release", "https://github.com/example/agent-runtime/releases/tag/v1.0.0", "Major release for production agent runtime developer tools.", 850),
    ])

    selected = select_top_event_clusters(clusters, 4)

    assert sum(1 for cluster in selected if cluster.category == "Research Papers") <= 2
    assert any(cluster.source == "anthropic" for cluster in selected)
    assert any(cluster.source == "github" for cluster in selected)


def test_top10_prefers_official_primary_over_media_commentary_for_same_event() -> None:
    clusters = cluster_content_items([
        _item(
            "anthropic",
            "Anthropic confidentially submits draft S-1 to the SEC",
            "https://www.anthropic.com/news/confidential-draft-s-1",
            "Official company update: Anthropic confidentially submits a draft S-1 registration statement to the SEC for a possible IPO.",
            0,
        ),
        _item(
            "youtube",
            "Did Google Just Fall Behind Again? Anthropic IPO rapid reaction",
            "https://www.youtube.com/watch?v=anthropic-ipo-commentary",
            "Media commentary and rapid reaction about Anthropic's S-1, SEC filing, IPO path, and what it means for Google.",
            50000,
        ),
    ])

    assert len(clusters) == 1
    assert clusters[0].source == "anthropic"
    assert clusters[0].canonical_event_key == "umbrella:anthropic-s1-ipo"
    assert clusters[0].score_breakdown["official_hard_event_signal"] > 0


def test_openai_aws_official_enterprise_availability_enters_top3_when_collected() -> None:
    clusters = cluster_content_items([
        _item(
            "openai",
            "OpenAI frontier models and Codex are now available on AWS",
            "https://openai.com/index/openai-models-codex-aws/",
            "Official release: OpenAI frontier models and Codex are available on AWS Bedrock and GovCloud for enterprise security, governance, and developer workflows.",
            0,
        ),
        _item("youtube", "Weekend demo: talking to statues with AI", "https://www.youtube.com/watch?v=statues", "A demo commentary and reaction video about AI avatars.", 90000),
        _item("youtube", "Agent Lake podcast conversation", "https://www.youtube.com/watch?v=agent-lake", "Podcast conversation and analysis about agent infrastructure concepts.", 85000),
        _item("x_twitter", "Looped Diffusion Language Models are going viral", "https://x.com/researcher/status/1", "Community social discussion of a research idea with limited reproduction so far.", 80000),
        _item("github", "LangSmith Sandboxes for untrusted agent code", "https://github.com/langchain-ai/langsmith-sandbox", "Developer tool release for running untrusted agent code in secure sandboxes.", 1000),
    ])

    selected = select_top_event_clusters(clusters, 3)
    selected_titles = [cluster.title.lower() for cluster in selected]

    assert any("aws" in title and "openai" in title for title in selected_titles)
    assert selected_titles.index(next(title for title in selected_titles if "aws" in title and "openai" in title)) <= 2


def test_large_ai_capital_raise_enters_top5() -> None:
    clusters = cluster_content_items([
        _item(
            "finance",
            "Alphabet plans $80B equity capital raise for AI infrastructure and compute",
            "https://abc.xyz/investor/ai-infrastructure-capital-raise",
            "Official IR/finance item: Alphabet plans an $80 billion equity capital raise for AI infrastructure, data center compute capacity, and model training.",
            0,
        ),
        _item("youtube", "AI founder podcast about productivity habits", "https://www.youtube.com/watch?v=habits", "Interview and discussion about AI productivity routines.", 60000),
        _item("alphaxiv", "Incremental agent benchmark paper", "https://www.alphaxiv.org/abs/2606.1", "Paper benchmark for agent evaluation with preliminary results.", 50000),
        _item("x_twitter", "Community debate on local inference", "https://x.com/example/status/2", "Community discussion and analysis of local inference benchmarks.", 40000),
        _item("github", "small-ai-tool v0.1.1 patch release", "https://github.com/example/small-ai-tool/releases/tag/v0.1.1", "Patch release with bug fixes for a developer tool.", 30000),
        _item("youtube", "Generic model news recap", "https://www.youtube.com/watch?v=recap", "News recap commentary about the week in AI.", 25000),
    ])

    selected = select_top_event_clusters(clusters, 5)

    assert any("$80b" in cluster.title.lower() or "80b" in cluster.title.lower() for cluster in selected)
    capital = next(cluster for cluster in clusters if "80b" in cluster.title.lower())
    assert capital.score_breakdown["official_hard_event_signal"] > 0


def test_media_only_items_cannot_dominate_top10_without_direct_builder_exception() -> None:
    items = [
        _item("youtube", f"Generic AI news recap commentary #{idx}", f"https://www.youtube.com/watch?v=generic-{idx}", "Generic commentary, recap, reaction, and analysis without new primary facts.", 90000 - idx)
        for idx in range(6)
    ]
    items.extend([
        _item("openai", "OpenAI models available on AWS", "https://openai.com/index/aws-models/", "Official: OpenAI frontier models and Codex are available on AWS Bedrock for enterprise governance.", 0),
        _item("anthropic", "Anthropic submits draft S-1 to SEC", "https://www.anthropic.com/news/s-1", "Official: Anthropic confidentially submits draft S-1 to the SEC for IPO process.", 0),
        _item("nvidia", "NVIDIA Cosmos 3 open model release", "https://developer.nvidia.com/blog/cosmos-3", "Official release: NVIDIA open-source Cosmos 3 world model weights, training scripts, datasets, and deployment tools for physical AI.", 0),
        _item("github", "LangSmith Sandboxes for untrusted agent code", "https://github.com/langchain-ai/langsmith-sandbox", "Developer tool release for sandboxing untrusted AI agent code.", 1000),
    ])

    selected = select_top_event_clusters(cluster_content_items(items), 7)

    assert sum(1 for cluster in selected if cluster.category == "Videos & Podcasts") <= 3
    assert any(cluster.source == "openai" for cluster in selected)
    assert any(cluster.source == "anthropic" for cluster in selected)


def test_social_only_research_signal_cannot_rank_first_over_t1_hard_events() -> None:
    clusters = cluster_content_items([
        _item(
            "x_twitter",
            "Looped Diffusion Language Models LoopMDM",
            "https://x.com/researcher/status/loopmdm",
            "Community social-only research signal about Looped Diffusion Language Models, no official code or benchmark reproduction yet.",
            120000,
        ),
        _item(
            "openai",
            "OpenAI frontier models and Codex are now available on AWS",
            "https://openai.com/index/openai-models-codex-aws/",
            "Official release: OpenAI frontier models and Codex are available on AWS Bedrock and GovCloud for enterprise AI deployment.",
            0,
        ),
    ])

    selected = select_top_event_clusters(clusters, 2)

    assert selected[0].source == "openai"
    loopmdm = next(cluster for cluster in clusters if "looped diffusion" in cluster.title.lower())
    assert loopmdm.score_breakdown["official_hard_event_signal"] == 0


def test_old_hn_discussion_of_repo_artifact_does_not_enter_top10_over_major_model_release() -> None:
    clusters = cluster_content_items([
        _item(
            "hackernews",
            "AI Agent Guidelines for CS336 at Stanford",
            "https://github.com/stanford-cs336/assignment1-basics/blob/main/CLAUDE.md",
            "HN discussion of a course repo artifact and CLAUDE.md guidelines.",
            827.65,
        ),
        _item(
            "x_twitter",
            "MiniMax M3 major model upgrade",
            "https://x.com/example/status/minimax-m3",
            "MiniMax M3 major model upgrade with 1M context, MSA architecture, native multimodal training, API pricing update, and broader availability.",
            38,
        ),
    ])

    selected = select_top_event_clusters(clusters, 1)
    assert selected[0].title.lower().startswith("minimax m3")


def test_official_open_model_release_with_code_weights_enters_top10() -> None:
    clusters = cluster_content_items([
        _item(
            "nvidia",
            "NVIDIA Cosmos 3 open model release",
            "https://developer.nvidia.com/blog/cosmos-3-open-physical-ai-models/",
            "Official NVIDIA Developer release: Cosmos 3 open-source world model for physical AI ships model weights, training scripts, datasets, and deployment tools.",
            0,
        ),
        *[
            _item("youtube", f"Generic AI commentary recap {idx}", f"https://www.youtube.com/watch?v=recap-{idx}", "Generic podcast commentary and reaction about AI tools.", 70000 - idx)
            for idx in range(9)
        ],
    ])

    selected = select_top_event_clusters(clusters, 10)
    cosmos = next(cluster for cluster in clusters if "cosmos 3" in cluster.title.lower())

    assert cosmos.score_breakdown["official_hard_event_signal"] > 0
    assert any(cluster.event_id == cosmos.event_id for cluster in selected)


def test_official_source_zero_score_gets_minimum_evidence_floor() -> None:
    cluster = cluster_content_items([
        _item(
            "openai",
            "OpenAI frontier models and Codex are now available on AWS",
            "https://openai.com/index/openai-models-codex-aws/",
            "Official release: OpenAI frontier models and Codex are available on AWS Bedrock, GovCloud, and enterprise cloud channels.",
            0,
        )
    ])[0]

    assert cluster.score_breakdown["official_hard_event_signal"] > 0
    assert cluster.event_score >= 112.0


def test_official_codex_product_capability_update_enters_top10() -> None:
    clusters = cluster_content_items([
        _item(
            "openai",
            "OpenAI Codex adds role-specific plugins, Sites, and annotations",
            "https://openai.com/index/codex-role-specific-plugins-sites-annotations/",
            (
                "Official OpenAI release: Codex now supports role-specific plugins, "
                "Sites with source annotations and citations, custom developer workflows, "
                "agent tools, and pull request review comments."
            ),
            0,
        ),
        *[
            _item(
                "youtube",
                f"Generic AI commentary recap {idx}",
                f"https://www.youtube.com/watch?v=generic-codex-{idx}",
                "Generic commentary and analysis about AI tools without new primary facts.",
                90000 - idx,
            )
            for idx in range(8)
        ],
        *[
            _item(
                "alphaxiv",
                f"Incremental agent benchmark paper {idx}",
                f"https://www.alphaxiv.org/abs/2606.20{idx:02d}",
                "Ordinary paper benchmark for agent evaluation with preliminary results and no production deployment.",
                80000 - idx,
            )
            for idx in range(4)
        ],
    ])

    codex = next(cluster for cluster in clusters if "codex" in cluster.title.lower())
    selected = select_top_event_clusters(clusters, 10)

    assert codex.score_breakdown["official_hard_event_signal"] > 0
    assert codex.event_score >= 118.0
    assert any(cluster.event_id == codex.event_id for cluster in selected)
    assert [cluster.event_id for cluster in selected].index(codex.event_id) <= 3
