"""Event-level clustering and deterministic ranking for Daily Digest.

The normal URL/title dedupe keeps raw duplicates out, but AI news often arrives as
many different links about one event (official post, official X thread, media
coverage, community discussion). This module groups those links into event
clusters so Top Headlines rank events, not individual URLs.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qs, urlparse

from schema import ContentItem
from aggregator.merger import categorize_item, topic_decision
from report.source_validation import (
    SOURCE_TIER_WEIGHTS,
    evidence_sources,
    format_source_links,
    source_diversity_count,
    source_tier,
    source_weight,
)


@dataclass
class EventCluster:
    """A set of ContentItem objects that describe the same real-world event."""

    event_id: str
    items: list[ContentItem]
    main_item: ContentItem
    category: str
    title: str
    url: str
    source: str
    source_tier: str
    source_weight: float
    topic_weight: float
    topic_confidence: float
    topic_method: str
    topic_reason: str
    topic_model_used: bool
    dimension_scores: dict[str, float]
    score_breakdown: dict[str, Any]
    event_score: float
    key_entities: list[str]
    canonical_event_key: str = ""


_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "about", "after",
    "before", "over", "under", "will", "have", "has", "are", "was", "were", "been",
    "using", "uses", "use", "your", "their", "they", "you", "our", "its", "new", "now",
    "more", "less", "than", "then", "what", "when", "where", "which", "why", "how",
    "https", "http", "com", "www", "amp", "via", "can", "one", "all", "not", "but",
}

_IMPORTANT_ENTITIES = {
    "openai", "chatgpt", "gpt", "anthropic", "claude", "google", "gemini", "deepmind",
    "microsoft", "github", "copilot", "meta", "llama", "nvidia", "amd", "intel",
    "apple", "mistral", "qwen", "deepseek", "xai", "spacex", "colossus", "huggingface",
    "cloudflare", "cursor", "windsurf", "codex", "agent", "agents", "mcp", "cuda",
}

_TECH_TERMS = {
    "model", "models", "reasoning", "training", "inference", "benchmark", "paper", "papers",
    "research", "architecture", "multimodal", "diffusion", "robot", "robotics", "vla",
    "retrieval", "rag", "red", "team", "safety", "alignment", "scaling",
}
_PRODUCT_TERMS = {
    "launch", "release", "introducing", "update", "updates", "api", "app", "apps", "product",
    "feature", "features", "agent", "agents", "coding", "code", "developer", "workflow",
    "deploy", "deployment", "excel", "sheets", "limits", "rate", "usage",
}
_BUSINESS_TERMS = {
    "deal", "partnership", "revenue", "earnings", "forecast", "stock", "funding", "investment",
    "acquire", "acquisition", "lawsuit", "copyright", "regulation", "market", "compute",
    "capacity", "datacenter", "data", "center", "energy", "nuclear", "chip", "chips",
}
_NOVELTY_TERMS = {
    "first", "new", "introducing", "launch", "release", "breakthrough", "novel", "v2", "v3",
    "v4", "v5", "preview", "beta", "open", "source", "record", "largest", "fastest",
}
_LEE_RELEVANCE_TERMS = {
    "agent", "agents", "coding", "code", "developer", "mcp", "workflow", "automation",
    "product", "startup", "business", "compute", "infra", "infrastructure", "model", "models",
    "reasoning", "api", "tool", "tools", "deploy", "deployment", "quant", "finance",
}

DEFAULT_EVENT_RANKING_CONFIG: dict[str, Any] = {
    "tier_order": {"T1": 4, "T1.5": 3, "T2": 2, "T3": 1},
    # Tier may improve confidence, but should not masquerade as content quality.
    # Keep the deterministic dimension prior deliberately small; authority is
    # handled separately by the source confidence multiplier.
    "tier_bonus": {"T1": 0.8, "T1.5": 0.5, "T2": 0.25, "T3": 0.0},
    "dimension_weights": {
        "technical_importance": 0.20,
        "product_impact": 0.22,
        "business_impact": 0.18,
        "novelty": 0.14,
        "lee_relevance": 0.20,
        "confidence": 0.06,
    },
    "topic_weights": {},
    "score_scale": 10.0,
    "heat_weight": 8.0,
    "cross_source_bonus_per_identity": 0.10,
    "max_cross_source_bonus": 0.45,
    "cluster_size_bonus_per_extra_item": 0.04,
    "max_cluster_size_bonus": 0.25,
    "main_link_social_penalty": 1.0,
    "main_link_social_penalty_hosts": ["x.com", "twitter.com", "reddit.com", "youtube.com", "youtu.be"],
    "title_length_penalty_per_char": 0.001,
    "low_confidence_threshold": 0.0,
    "low_confidence_multiplier": 1.0,
    "release_update_multipliers": {
        "major": 0.85,
        "minor": 0.55,
        "patch": 0.35,
        "prerelease": 0.25,
        "unknown": 0.50,
    },
    # GitHub repository homepage scores are usually cumulative stars, not daily
    # momentum. Very large evergreen repos should remain visible in Developer
    # Tools, but they should not crowd out fresh AI events just because they were
    # pushed today.
    "github_evergreen_repo_star_threshold": 20000.0,
    "github_evergreen_repo_heat_cap": 5000.0,
    "github_evergreen_repo_multiplier": 0.55,
    # Show HN posts can be excellent discovery signals, but a single-source
    # self-reported launch/benchmark should not outrank independently
    # corroborated official events solely on HN heat.
    "single_source_show_hn_multiplier": 0.85,
    # Top Headlines selection is separate from raw event_score ranking. Keep
    # event_score truthful, then apply light lane quotas so one hot source, topic,
    # or umbrella launch cannot consume the whole headline section.
    "top_headlines_quota": {
        "enabled": True,
        "max_per_source": 3,
        "max_per_topic": {
            "Research Papers": 3,
            "Social & Community": 2,
            "Books & Reading": 1,
            "Finance & Markets": 2,
        },
        "min_required_if_available": {
            "Developer Tools": 1,
            "Infrastructure & Systems": 1,
        },
        "max_per_umbrella": 1,
    },
    # Research-paper ranking controls. Being a paper should only admit the item
    # into a specialist lane; actual rank must come from paper-specific quality.
    "research_paper_topic_cap": 1.0,
    "ordinary_paper_multiplier": 0.72,
    "incremental_paper_multiplier": 0.84,
    "solid_paper_multiplier": 0.96,
    "strong_paper_multiplier": 1.08,
    "breakthrough_paper_multiplier": 1.18,
    "survey_paper_cap": 0.80,
}

OBJECTIVE_DIMENSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "technical_importance",
        "product_impact",
        "business_impact",
        "novelty",
        "lee_relevance",
        "confidence",
    ],
    "properties": {
        "technical_importance": {"type": "number", "minimum": 0, "maximum": 10},
        "product_impact": {"type": "number", "minimum": 0, "maximum": 10},
        "business_impact": {"type": "number", "minimum": 0, "maximum": 10},
        "novelty": {"type": "number", "minimum": 0, "maximum": 10},
        "lee_relevance": {"type": "number", "minimum": 0, "maximum": 10},
        "confidence": {"type": "number", "minimum": 0, "maximum": 10},
    },
}


def objective_dimension_schema() -> dict[str, Any]:
    """Return the stable JSON schema for optional objective dimension scoring."""
    return deepcopy(OBJECTIVE_DIMENSION_SCHEMA)


def event_ranking_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return deterministic event-ranker knobs from config with safe defaults."""
    out = deepcopy(DEFAULT_EVENT_RANKING_CONFIG)
    raw = ((config or {}).get("ai_daily") or {}).get("event_ranking") or (config or {}).get("event_ranking") or {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if key in {"tier_order", "tier_bonus", "dimension_weights", "topic_weights", "release_update_multipliers"} and isinstance(value, dict):
            out[key].update(value)
        elif key == "top_headlines_quota" and isinstance(value, dict):
            out[key] = _deep_merge_dict(out.get(key, {}), value)
        elif key in out:
            out[key] = value
    for section in ("tier_order",):
        out[section] = {str(k): int(v) for k, v in out[section].items()}
    for section in ("tier_bonus", "dimension_weights", "topic_weights", "release_update_multipliers"):
        out[section] = {str(k): float(v) for k, v in out[section].items()}
    for key in (
        "score_scale",
        "heat_weight",
        "cross_source_bonus_per_identity",
        "max_cross_source_bonus",
        "cluster_size_bonus_per_extra_item",
        "max_cluster_size_bonus",
        "main_link_social_penalty",
        "title_length_penalty_per_char",
        "low_confidence_threshold",
        "low_confidence_multiplier",
        "research_paper_topic_cap",
        "ordinary_paper_multiplier",
        "incremental_paper_multiplier",
        "solid_paper_multiplier",
        "strong_paper_multiplier",
        "breakthrough_paper_multiplier",
        "survey_paper_cap",
        "github_evergreen_repo_star_threshold",
        "github_evergreen_repo_heat_cap",
        "github_evergreen_repo_multiplier",
        "single_source_show_hn_multiplier",
    ):
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            out[key] = DEFAULT_EVENT_RANKING_CONFIG[key]
    return out


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge one config dictionary level recursively without mutating inputs."""
    out = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _headline_quota_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return Top Headlines lane-quota settings from supported config locations."""
    ranking = event_ranking_config(config)
    default = deepcopy(DEFAULT_EVENT_RANKING_CONFIG["top_headlines_quota"])
    raw: dict[str, Any] = {}
    if isinstance(ranking.get("top_headlines_quota"), dict):
        raw = _deep_merge_dict(raw, ranking["top_headlines_quota"])
    cfg = config or {}
    for candidate in (
        ((cfg.get("ai_daily") or {}).get("top_headlines_quota") if isinstance(cfg.get("ai_daily"), dict) else None),
        ((cfg.get("summary") or {}).get("top_headlines_quota") if isinstance(cfg.get("summary"), dict) else None),
        cfg.get("top_headlines_quota"),
    ):
        if isinstance(candidate, dict):
            raw = _deep_merge_dict(raw, candidate)
    merged = _deep_merge_dict(default, raw)
    merged["enabled"] = bool(merged.get("enabled", True))
    for key in ("max_per_source", "max_per_umbrella"):
        try:
            value = int(merged.get(key, 0) or 0)
        except (TypeError, ValueError):
            value = int(default.get(key, 0) or 0)
        merged[key] = value if value > 0 else None
    for key in ("max_per_topic", "min_required_if_available"):
        raw_map = merged.get(key) or {}
        normalized: dict[str, int] = {}
        if isinstance(raw_map, dict):
            for raw_key, raw_value in raw_map.items():
                try:
                    value = int(raw_value or 0)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    normalized[str(raw_key)] = value
        merged[key] = normalized
    return merged


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


def _quota_lookup(mapping: dict[str, int], key: str) -> int | None:
    if not mapping:
        return None
    candidates = {key, key.lower(), _slug(key)}
    for raw_key, value in mapping.items():
        if raw_key in candidates or raw_key.lower() in candidates or _slug(raw_key) in candidates:
            return value
    return None


def _topic_matches_quota_key(topic: str, quota_key: str) -> bool:
    return topic == quota_key or topic.lower() == quota_key.lower() or _slug(topic) == _slug(quota_key)


# Terms that are too broad to prove two items describe the same event. They can
# help scoring, but clustering needs more specific shared terms such as SpaceX,
# Colossus, rate limits, MRC, a paper title token, or a product name.
_GENERIC_CLUSTER_TERMS = _STOPWORDS | {
    "ai", "llm", "llms", "model", "models", "agent", "agents", "code", "coding",
    "api", "app", "apps", "launch", "launching", "introducing", "new", "update",
    "updates", "release", "today", "official", "blog", "news", "research", "paper",
    "message", "messages", "context", "usage", "available", "read", "today",
    "breakthrough", "signal", "signals", "long", "sentence", "unique", "payload",
    "developer", "developers", "tool", "tools", "finance", "market", "markets",
    "openai", "anthropic", "claude", "chatgpt", "gpt", "google", "gemini",
    "deepmind", "microsoft", "github", "meta", "llama", "nvidia", "apple",
    "x_twitter", "twitter", "reddit", "hackernews", "youtube", "alphaxiv", "huggingface",
    "framework", "frameworks", "machine", "learning", "training", "inference",
}


def _clean_title(title: str) -> str:
    text = re.sub(r"https?://\S+", " ", title or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _text_for_item(item: ContentItem) -> str:
    return f"{item.title} {item.content} {' '.join(item.tags or [])} {item.author} {item.source}".lower()


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_+.-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    return {w.strip("._-") for w in words if len(w.strip("._-")) >= 3 and w not in _STOPWORDS}


def _entities(item: ContentItem) -> set[str]:
    text = _text_for_item(item)
    tokens = _tokens(text)
    entities = {t for t in tokens if t in _IMPORTANT_ENTITIES}
    entities.update(m.group(1).lower() for m in re.finditer(r"@([a-zA-Z0-9_]{2,})", text))
    host = urlparse(item.url or "").netloc.lower().removeprefix("www.")
    if host:
        host_root = host.split(".")[0]
        # Treat a URL host as an event entity only when it is a known actor
        # (openai, anthropic, spacex, ...). Generic publishers or test hosts
        # such as example.com should not cause unrelated stories to merge.
        if host_root in _IMPORTANT_ENTITIES:
            entities.add(host_root)
    # Do not add the collector/source name as an event entity: two unrelated
    # Hacker News or X items should not merge just because they came through the
    # same collector. URL host/account and @mentions already provide real actors.
    if item.arxiv_id:
        entities.add(item.arxiv_id.lower())
    return entities




def _canonical_event_key_for_item(item: ContentItem) -> str:
    """Return a coarse campaign/umbrella key when one launch spawns many events.

    This is deliberately narrower than `_same_event`: sibling announcements can
    remain separate event clusters for topic sections, but Top Headlines should
    not spend several slots on one Google I/O / Gemini campaign.
    """
    text = _text_for_item(item)
    parsed = urlparse(item.url or "")
    host = parsed.netloc.lower().removeprefix("www.")
    path = (parsed.path or "").lower()
    haystack = f"{text} {host} {path}"
    google_actor = (
        "google" in haystack
        or "gemini" in haystack
        or "deepmind" in haystack
        or host in {"blog.google", "deepmind.google", "ai.google", "developers.googleblog.com"}
        or host.endswith(".google")
    )
    if google_actor:
        google_io_terms = (
            "googleio",
            "google i/o",
            "#googleio",
            "i/o 20",
            "io-20",
            "/io-20",
            "search-io-20",
        )
        gemini_launch_terms = (
            "gemini 3.5",
            "gemini-3-5",
            "gemini omni",
            "gemini spark",
            "gemini for science",
            "agentic gemini",
            "google antigravity",
            " ai studio",
            "gemini api",
        )
        if any(term in haystack for term in google_io_terms) or any(term in haystack for term in gemini_launch_terms):
            return "umbrella:google-io-gemini"
    return ""


def _canonical_event_key_for_items(items: list[ContentItem]) -> str:
    keys = [_canonical_event_key_for_item(item) for item in items]
    keys = [key for key in keys if key]
    if not keys:
        return ""
    counts = defaultdict(int)
    for key in keys:
        counts[key] += 1
    return sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[0][0]


def _canonical_event_key_for_cluster(cluster: EventCluster) -> str:
    return getattr(cluster, "canonical_event_key", "") or _canonical_event_key_for_items(cluster.items)

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _title_similarity(a: ContentItem, b: ContentItem) -> float:
    return SequenceMatcher(None, _clean_title(a.title).lower(), _clean_title(b.title).lower()).ratio()


def _event_key_for_item(item: ContentItem) -> str:
    if item.arxiv_id:
        return f"arxiv:{item.arxiv_id.lower()}"
    parsed = urlparse(item.url or "")
    host = parsed.netloc.lower().removeprefix("www.")
    path_parts = [p for p in parsed.path.split("/") if p]
    if host in {"x.com", "twitter.com"} and len(path_parts) >= 3 and path_parts[1] == "status":
        # Do not force all statuses together, but keep account identity available.
        return ""
    if host in {"arxiv.org", "papers.cool", "alphaxiv.org", "openreview.net"} and path_parts:
        return f"paper:{host}:{path_parts[-1].lower()}"
    if host in {"youtube.com", "youtu.be"}:
        if host == "youtube.com" and path_parts and path_parts[0] == "watch":
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
            return f"url:{host}/watch:{video_id.lower()}" if video_id else ""
        if path_parts:
            return f"url:{host}/{path_parts[0].lower()}"
    if host and path_parts:
        normalized_path = "/".join(path_parts).strip("/").lower()
        return f"url:{host}/{normalized_path}" if normalized_path else ""
    return ""


def _has_unrelated_negation(item: ContentItem) -> bool:
    text = _text_for_item(item)
    return bool(re.search(r"\b(unrelated|not\s+related|separate\s+from)\b", text))


def _same_event(cluster: list[ContentItem], item: ContentItem) -> bool:
    item_key = _event_key_for_item(item)
    item_tokens = _tokens(_text_for_item(item))
    item_entities = _entities(item)
    item_action_tokens = item_tokens & (_TECH_TERMS | _PRODUCT_TERMS | _BUSINESS_TERMS | _NOVELTY_TERMS)
    item_negates_relation = _has_unrelated_negation(item)

    for existing in cluster:
        existing_key = _event_key_for_item(existing)
        if item_key and existing_key and item_key == existing_key:
            return True

        item_repo = _github_repo_identity(item)
        existing_repo = _github_repo_identity(existing)
        if item_repo and existing_repo and item_repo != existing_repo:
            continue

        title_sim = _title_similarity(item, existing)
        if title_sim >= 0.82:
            title_shared_specific = (
                _tokens(_clean_title(item.title)) & _tokens(_clean_title(existing.title))
            ) - _GENERIC_CLUSTER_TERMS
            if (item_entities & _entities(existing)) or len(title_shared_specific) >= 2:
                return True

        existing_tokens = _tokens(_text_for_item(existing))
        existing_entities = _entities(existing)
        entity_overlap = item_entities & existing_entities
        token_overlap = _jaccard(item_tokens, existing_tokens)
        action_overlap = item_action_tokens & (existing_tokens & (_TECH_TERMS | _PRODUCT_TERMS | _BUSINESS_TERMS | _NOVELTY_TERMS))
        shared_specific = (item_tokens & existing_tokens) - _GENERIC_CLUSTER_TERMS
        if (item_negates_relation or _has_unrelated_negation(existing)) and title_sim < 0.55:
            continue

        # Conservative event clustering: shared company/platform names alone are
        # not enough. This prevents unrelated Anthropic/OpenAI/agent stories from
        # collapsing into one giant cluster while still merging official release,
        # social posts, and media/community echoes for the same event.
        if len(shared_specific) >= 3 and len(entity_overlap) >= 1 and token_overlap >= 0.10:
            return True
        if len(shared_specific) >= 2 and len(entity_overlap) >= 2 and action_overlap and token_overlap >= 0.08:
            return True
        if title_sim >= 0.66 and len(shared_specific) >= 2 and len(entity_overlap) >= 1:
            return True
    return False


_OPENREVIEW_ORAL_OR_ABOVE_TERMS = (
    "oral",
    "best paper",
    "outstanding paper",
    "distinguished paper",
    "award",
    "plenary",
)


def _is_openreview_oral_or_above(item: ContentItem) -> bool:
    """Return True only for OpenReview papers with oral-or-better signals.

    Host-level `openreview.net` is T1.5. Lee's latest calibration promotes only
    oral-or-above papers to T1, using item-level venue/decision metadata.
    """
    host = urlparse(item.url or "").netloc.lower().removeprefix("www.")
    source = (item.source or "").lower()
    if source != "openreview" and host != "openreview.net":
        return False
    extra = item.extra or {}
    metadata_bits = [
        str(extra.get("venue") or ""),
        str(extra.get("venue_id") or ""),
        str(extra.get("presentation") or ""),
        str(extra.get("decision") or ""),
        str(extra.get("status") or ""),
        " ".join(map(str, item.tags or [])),
        item.content or "",
    ]
    text = " ".join(metadata_bits).lower()
    if "spotlight" in text and "oral" not in text and not any(term in text for term in _OPENREVIEW_ORAL_OR_ABOVE_TERMS[1:]):
        return False
    return any(term in text for term in _OPENREVIEW_ORAL_OR_ABOVE_TERMS)


def _source_tier_for_item(item: ContentItem) -> str:
    if _is_openreview_oral_or_above(item):
        return "T1"
    return source_tier(item.source, item.url)


def _source_tier_weight_for_item(item: ContentItem) -> float:
    tier = _source_tier_for_item(item)
    return float(SOURCE_TIER_WEIGHTS.get(tier, SOURCE_TIER_WEIGHTS.get("T3", 1.0)))


def _main_item_score(item: ContentItem, ranking: dict[str, Any] | None = None) -> tuple[float, float, float, float, str]:
    ranking = ranking or DEFAULT_EVENT_RANKING_CONFIG
    tier_order = ranking.get("tier_order") or DEFAULT_EVENT_RANKING_CONFIG["tier_order"]
    tier = _source_tier_for_item(item)
    host = urlparse(item.url or "").netloc.lower()
    social_hosts = set(ranking.get("main_link_social_penalty_hosts") or [])
    social_penalty = float(ranking.get("main_link_social_penalty", 1.0)) if host in social_hosts else 0.0
    title_len_penalty = -min(len(item.title or ""), 240) * float(ranking.get("title_length_penalty_per_char", 0.001))
    return (
        tier_order.get(tier, 1),
        source_weight(item.source, item.url),
        -social_penalty,
        float(item.score or 0) + title_len_penalty,
        item.title or "",
    )


def _select_main_item(items: list[ContentItem], ranking: dict[str, Any] | None = None) -> ContentItem:
    return max(items, key=lambda item: _main_item_score(item, ranking))


def _score_terms(tokens: set[str], terms: set[str], base: float = 1.5, scale: float = 1.6) -> float:
    return min(10.0, base + scale * len(tokens & terms))


def coerce_dimension_scores(raw: dict[str, Any] | None, fallback: dict[str, float]) -> dict[str, float]:
    """Validate optional objective JSON dimensions, falling back deterministically."""
    schema = OBJECTIVE_DIMENSION_SCHEMA
    if not isinstance(raw, dict):
        return dict(fallback)
    out: dict[str, float] = {}
    for key in schema["required"]:
        try:
            value = float(raw[key])
        except (KeyError, TypeError, ValueError):
            return dict(fallback)
        out[key] = round(min(10.0, max(0.0, value)), 2)
    return out


def _dimension_scores(
    items: list[ContentItem],
    main: ContentItem,
    ranking: dict[str, Any] | None = None,
) -> dict[str, float]:
    ranking = ranking or DEFAULT_EVENT_RANKING_CONFIG
    text_tokens: set[str] = set()
    for item in items:
        text_tokens |= _tokens(_text_for_item(item))

    tier = _source_tier_for_item(main)
    tier_bonus = (ranking.get("tier_bonus") or DEFAULT_EVENT_RANKING_CONFIG["tier_bonus"]).get(tier, 0.2)
    diversity = source_diversity_count(evidence_sources([_item_dict(i) for i in items], max_sources=12, dedupe_by_identity=True))
    diversity_bonus = min(2.0, max(0, diversity - 1) * 0.45)
    log_heat = min(2.0, math.log10(max(1.0, max(_heat_score_for_item(i, ranking) for i in items))) / 3.0)

    scores = {
        "technical_importance": _score_terms(text_tokens, _TECH_TERMS) + tier_bonus,
        "product_impact": _score_terms(text_tokens, _PRODUCT_TERMS) + tier_bonus * 0.6,
        "business_impact": _score_terms(text_tokens, _BUSINESS_TERMS) + diversity_bonus,
        "novelty": _score_terms(text_tokens, _NOVELTY_TERMS) + tier_bonus * 0.4,
        "lee_relevance": _score_terms(text_tokens, _LEE_RELEVANCE_TERMS, base=2.0, scale=1.35) + log_heat,
        "confidence": 3.0 + tier_bonus + diversity_bonus + min(1.5, len(items) * 0.25),
    }
    return {k: round(min(10.0, max(0.0, v)), 2) for k, v in scores.items()}


_PAPER_SOURCES = {
    "arxiv", "openreview", "papers", "coolpaper", "ccf_bestpaper", "alphaxiv"
}
_PAPER_HOSTS = {"arxiv.org", "openreview.net", "papers.cool", "alphaxiv.org"}
_SURVEY_TERMS = {
    "survey", "review", "overview", "taxonomy", "tutorial", "综述", "surveying"
}
_POSITION_TERMS = {"position", "opinion", "perspective", "essay", "whitepaper"}
_BENCHMARK_TERMS = {
    "benchmark", "benchmarks", "leaderboard", "eval", "evaluation", "dataset", "datasets", "suite"
}
_EMPIRICAL_TERMS = {
    "experiment", "experiments", "ablation", "ablate", "benchmark", "benchmarks", "evaluation",
    "eval", "results", "sota", "state-of-the-art", "dataset", "datasets", "leaderboard",
    "human study", "user study", "real-world", "production", "deployment"
}
_BREAKTHROUGH_TERMS = {
    "breakthrough", "state-of-the-art", "sota", "first", "novel", "new paradigm", "paradigm",
    "record", "largest", "fastest", "outperform", "outperforms", "scaling law", "emergent",
    "agentic", "reasoning", "long-context", "multi-agent"
}
_REPRODUCIBILITY_TERMS = {
    "code", "github", "open source", "open-source", "dataset", "datasets", "reproduce",
    "reproducible", "replication", "artifact", "demo", "weights", "model card", "paperswithcode"
}
_CREDIBILITY_TERMS = {
    "neurips", "iclr", "icml", "acl", "emnlp", "cvpr", "iccv", "eccv", "siggraph",
    "kdd", "osdi", "sosp", "mlsys", "nature", "science", "best paper", "oral", "spotlight",
    "accepted", "peer-reviewed", "openreview"
}


def _contains_any(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def _paper_like(main: ContentItem, category: str | None = None) -> bool:
    if (category or "") == "Research Papers":
        return True
    if main.arxiv_id:
        return True
    source = (main.source or "").lower()
    host = urlparse(main.url or "").netloc.lower().removeprefix("www.")
    return source in _PAPER_SOURCES or host in _PAPER_HOSTS


def _paper_subtype(text: str) -> str:
    if _contains_any(text, _SURVEY_TERMS):
        return "survey"
    if _contains_any(text, _POSITION_TERMS):
        return "position"
    if _contains_any(text, _BENCHMARK_TERMS):
        return "benchmark"
    if "replication" in text or "reproduce" in text:
        return "replication"
    return "original"


def _extra_number(item: ContentItem, keys: tuple[str, ...]) -> float:
    raw_extra = item.extra or {}
    for key in keys:
        if key in raw_extra:
            try:
                return float(raw_extra[key] or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _paper_signal_score(items: list[ContentItem]) -> float:
    """Return a 0-10 influence/attention signal using whatever metadata exists.

    Collectors expose different fields, so this intentionally accepts common
    aliases and falls back to `ContentItem.score`. The score is logarithmic: a
    modest number of likes/bookmarks/citations should not dominate quality.
    """
    best = 0.0
    for item in items:
        raw = max(
            float(item.score or 0.0),
            _extra_number(item, ("citations", "citation_count", "semantic_scholar_citations")) * 3.0,
            _extra_number(item, ("influential_citations", "influential_citation_count")) * 5.0,
            _extra_number(item, ("bookmarks", "bookmark_count", "favorites")) * 1.5,
            _extra_number(item, ("likes", "like_count", "upvotes")),
            _extra_number(item, ("stars", "github_stars")) * 1.2,
            _extra_number(item, ("downloads", "hf_downloads")) * 0.05,
        )
        best = max(best, raw)
    return round(min(10.0, math.log10(max(1.0, best)) * 2.2), 2)


def _paper_quality_breakdown(
    items: list[ContentItem],
    main: ContentItem,
    dimensions: dict[str, float],
    ranking: dict[str, Any],
    category: str | None = None,
) -> dict[str, Any] | None:
    """Score paper quality separately from generic topic/source authority.

    This is deliberately conservative: a random arXiv/survey item starts below
    neutral and only earns a boost when there are concrete novelty, rigor,
    influence, reproducibility, or credibility signals.
    """
    if not _paper_like(main, category):
        return None

    text = " ".join(_text_for_item(item) for item in items)
    subtype = _paper_subtype(text)
    text_tokens = _tokens(text)
    tier = _source_tier_for_item(main)
    source = (main.source or "").lower()
    host = urlparse(main.url or "").netloc.lower().removeprefix("www.")

    novelty = 2.0 + min(4.0, len(text_tokens & _BREAKTHROUGH_TERMS) * 1.25) + float(dimensions.get("novelty", 0.0)) * 0.35
    if subtype == "survey":
        novelty -= 2.0
    elif subtype == "benchmark":
        novelty -= 0.4

    technical_depth = 2.0 + min(3.5, len(text_tokens & _TECH_TERMS) * 0.55) + float(dimensions.get("technical_importance", 0.0)) * 0.35
    empirical_strength = 1.5 + min(5.0, len(text_tokens & _EMPIRICAL_TERMS) * 0.9)
    practical_impact = 1.5 + min(3.0, len(text_tokens & _LEE_RELEVANCE_TERMS) * 0.35) + (
        float(dimensions.get("product_impact", 0.0)) + float(dimensions.get("lee_relevance", 0.0))
    ) * 0.20
    reproducibility = 1.0 + min(6.0, len(text_tokens & _REPRODUCIBILITY_TERMS) * 1.1)
    credibility = 3.0 + min(3.0, len(text_tokens & _CREDIBILITY_TERMS) * 0.9)
    if tier == "T1":
        credibility += 1.0
    if source == "openreview" or host == "openreview.net":
        credibility += 0.8
    if source == "arxiv" or host == "arxiv.org":
        credibility += 0.3
    influence = _paper_signal_score(items)

    components = {
        "novelty": round(min(10.0, max(0.0, novelty)), 2),
        "technical_depth": round(min(10.0, max(0.0, technical_depth)), 2),
        "empirical_strength": round(min(10.0, max(0.0, empirical_strength)), 2),
        "practical_impact": round(min(10.0, max(0.0, practical_impact)), 2),
        "credibility": round(min(10.0, max(0.0, credibility)), 2),
        "reproducibility": round(min(10.0, max(0.0, reproducibility)), 2),
        "influence_signal": influence,
    }
    quality = (
        components["novelty"] * 0.24
        + components["technical_depth"] * 0.18
        + components["empirical_strength"] * 0.17
        + components["practical_impact"] * 0.14
        + components["credibility"] * 0.10
        + components["reproducibility"] * 0.10
        + components["influence_signal"] * 0.07
    )
    if subtype == "survey":
        quality *= 0.82
    elif subtype == "position":
        quality *= 0.78
    elif subtype == "benchmark":
        quality *= 0.96
    elif subtype == "replication":
        quality *= 0.90
    quality = round(min(10.0, max(0.0, quality)), 2)

    if quality >= 8.0:
        multiplier = float(ranking.get("breakthrough_paper_multiplier", 1.18))
    elif quality >= 6.2:
        multiplier = float(ranking.get("strong_paper_multiplier", 1.08))
    elif quality >= 4.8:
        multiplier = float(ranking.get("solid_paper_multiplier", 0.96))
    elif quality >= 3.5:
        multiplier = float(ranking.get("incremental_paper_multiplier", 0.84))
    else:
        multiplier = float(ranking.get("ordinary_paper_multiplier", 0.72))
    if subtype == "survey" and quality < 8.0:
        multiplier = min(multiplier, float(ranking.get("survey_paper_cap", 0.80)))

    return {
        "paper_subtype": subtype,
        "paper_quality_score": quality,
        "paper_quality_multiplier": round(multiplier, 4),
        "paper_quality_components": components,
    }


def _topic_weight(category: str, ranking: dict[str, Any] | None = None) -> float:
    """Return the configured event-ranking multiplier for a category/topic."""
    ranking = ranking or DEFAULT_EVENT_RANKING_CONFIG
    weights = ranking.get("topic_weights") or {}
    return float(weights.get(category, 1.0))


_VERSION_RE = re.compile(r"(?<![a-zA-Z0-9])v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+.]?([0-9A-Za-z][0-9A-Za-z.-]*))?")
_PRERELEASE_RE = re.compile(r"(?:^|[-.])(alpha|beta|rc|pre|preview|dev|nightly|canary)\d*", re.IGNORECASE)


def _release_update_kind(item: ContentItem) -> str | None:
    """Classify GitHub release update size for ranking penalties.

    Small GitHub release posts are useful but should not crowd out bigger AI
    events. Major releases are penalized lightly, minor/patch releases more, and
    prereleases/RCs the most (e.g. `v0.30.0-rc20`).
    """
    text = f"{item.title or ''} {item.url or ''}".lower()
    if item.source != "github" or ("[release]" not in text and "/releases/" not in text):
        return None
    match = _VERSION_RE.search(text)
    if not match:
        return "unknown"
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3) or 0)
    suffix = match.group(4) or ""
    if suffix and _PRERELEASE_RE.search(f"-{suffix}"):
        return "prerelease"
    if major >= 1 and minor == 0 and patch == 0:
        return "major"
    if patch > 0:
        return "patch"
    if minor > 0:
        return "minor"
    return "unknown"


def _release_update_multiplier(item: ContentItem, ranking: dict[str, Any]) -> tuple[str | None, float]:
    kind = _release_update_kind(item)
    if not kind:
        return None, 1.0
    multipliers = ranking.get("release_update_multipliers") or DEFAULT_EVENT_RANKING_CONFIG["release_update_multipliers"]
    return kind, float(multipliers.get(kind, multipliers.get("unknown", 0.50)))


def _github_repo_identity(item: ContentItem) -> str | None:
    if (item.source or "").lower() != "github":
        return None
    parsed = urlparse(item.url or "")
    host = parsed.netloc.lower().removeprefix("www.")
    if host != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    return f"{parts[0].lower()}/{parts[1].lower()}"


def _is_github_repo_homepage(item: ContentItem) -> bool:
    repo = _github_repo_identity(item)
    if not repo:
        return False
    parsed = urlparse(item.url or "")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 2:
        return False
    text = f"{item.title or ''} {item.url or ''}".lower()
    return "[release]" not in text and "/releases/" not in text


def _github_evergreen_repo_multiplier(item: ContentItem, ranking: dict[str, Any]) -> float:
    if not _is_github_repo_homepage(item):
        return 1.0
    stars = float((item.extra or {}).get("stars") or item.score or 0.0)
    threshold = float(ranking.get("github_evergreen_repo_star_threshold", 20000.0))
    if stars < threshold:
        return 1.0
    return float(ranking.get("github_evergreen_repo_multiplier", 0.55))


def _heat_score_for_item(item: ContentItem, ranking: dict[str, Any]) -> float:
    score = float(item.score or 0.0)
    if _is_github_repo_homepage(item):
        stars = float((item.extra or {}).get("stars") or score)
        threshold = float(ranking.get("github_evergreen_repo_star_threshold", 20000.0))
        if stars >= threshold:
            cap = float(ranking.get("github_evergreen_repo_heat_cap", 5000.0))
            return min(score, cap)
    return score


def _single_source_show_hn_multiplier(main: ContentItem, diversity: int, ranking: dict[str, Any]) -> float:
    title = (main.title or "").strip().lower()
    if (main.source or "").lower() == "hackernews" and title.startswith("show hn:") and diversity <= 1:
        return float(ranking.get("single_source_show_hn_multiplier", 0.85))
    return 1.0


def _event_score_breakdown(
    items: list[ContentItem],
    main: ContentItem,
    dimensions: dict[str, float],
    ranking: dict[str, Any] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    ranking = ranking or DEFAULT_EVENT_RANKING_CONFIG
    heat = math.log10(max(1.0, max(_heat_score_for_item(i, ranking) for i in items)))
    diversity = source_diversity_count(evidence_sources([_item_dict(i) for i in items], max_sources=12, dedupe_by_identity=True))
    tier_w = _source_tier_weight_for_item(main)
    weights = ranking.get("dimension_weights") or DEFAULT_EVENT_RANKING_CONFIG["dimension_weights"]
    weighted_dims = sum(
        float(dimensions.get(key, 0.0)) * float(weight)
        for key, weight in weights.items()
    )
    dimension_part = weighted_dims * float(ranking["score_scale"])
    heat_part = heat * float(ranking["heat_weight"])
    pre_multiplier_score = dimension_part + heat_part
    cross_source = 1.0 + min(float(ranking["max_cross_source_bonus"]), max(0, diversity - 1) * float(ranking["cross_source_bonus_per_identity"]))
    cluster_heat = 1.0 + min(float(ranking["max_cluster_size_bonus"]), max(0, len(items) - 1) * float(ranking["cluster_size_bonus_per_extra_item"]))
    topic_w = _topic_weight(category or "", ranking)
    paper_quality = _paper_quality_breakdown(items, main, dimensions, ranking, category=category)
    if paper_quality is not None:
        topic_w = min(topic_w, float(ranking.get("research_paper_topic_cap", 1.0)))
    paper_quality_multiplier = float((paper_quality or {}).get("paper_quality_multiplier", 1.0))
    low_confidence_multiplier = 1.0
    if float(dimensions.get("confidence", 0.0)) < float(ranking.get("low_confidence_threshold", 0.0)):
        low_confidence_multiplier = float(ranking.get("low_confidence_multiplier", 1.0))
    release_kind, release_update_multiplier = _release_update_multiplier(main, ranking)
    github_evergreen_multiplier = _github_evergreen_repo_multiplier(main, ranking)
    show_hn_multiplier = _single_source_show_hn_multiplier(main, diversity, ranking)
    final_score = (
        pre_multiplier_score
        * tier_w
        * topic_w
        * cross_source
        * cluster_heat
        * paper_quality_multiplier
        * low_confidence_multiplier
        * release_update_multiplier
        * github_evergreen_multiplier
        * show_hn_multiplier
    )
    return {
        "weighted_dimensions": round(weighted_dims, 4),
        "dimension_part": round(dimension_part, 4),
        "heat": round(heat, 4),
        "heat_part": round(heat_part, 4),
        "pre_multiplier_score": round(pre_multiplier_score, 4),
        "source_tier_multiplier": round(tier_w, 4),
        "topic_multiplier": round(topic_w, 4),
        "cross_source_multiplier": round(cross_source, 4),
        "cluster_size_multiplier": round(cluster_heat, 4),
        "paper_quality_score": (paper_quality or {}).get("paper_quality_score"),
        "paper_subtype": (paper_quality or {}).get("paper_subtype", "none"),
        "paper_quality_multiplier": round(paper_quality_multiplier, 4),
        "paper_quality_components": (paper_quality or {}).get("paper_quality_components", {}),
        "low_confidence_multiplier": round(low_confidence_multiplier, 4),
        "release_update_multiplier": round(release_update_multiplier, 4),
        "release_update_kind": release_kind or "none",
        "github_evergreen_repo_multiplier": round(github_evergreen_multiplier, 4),
        "single_source_show_hn_multiplier": round(show_hn_multiplier, 4),
        "final_score": round(final_score, 3),
    }


def _event_score(
    items: list[ContentItem],
    main: ContentItem,
    dimensions: dict[str, float],
    ranking: dict[str, Any] | None = None,
    category: str | None = None,
) -> float:
    return float(_event_score_breakdown(items, main, dimensions, ranking, category).get("final_score", 0.0))


def _item_dict(item: ContentItem) -> dict:
    return {"source": item.source, "url": item.url, "score": item.score, "title": item.title}


def _cluster_id(items: list[ContentItem]) -> str:
    basis = "|".join(sorted((i.url or i.title or "")[:180] for i in items))
    return hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:12]


def cluster_content_items(
    items: list[ContentItem],
    config: dict[str, Any] | None = None,
) -> list[EventCluster]:
    """Cluster raw items into ranked events.

    The algorithm is intentionally deterministic and conservative. It is not a
    semantic embedding replacement, but it handles common AI-news duplication:
    official announcement + X thread + media/community echoes.
    """
    ranking = event_ranking_config(config)
    sorted_items = sorted(items, key=lambda i: (float(i.score or 0), source_weight(i.source, i.url)), reverse=True)
    raw_clusters: list[list[ContentItem]] = []
    for item in sorted_items:
        for cluster in raw_clusters:
            if _same_event(cluster, item):
                cluster.append(item)
                break
        else:
            raw_clusters.append([item])

    clusters: list[EventCluster] = []
    for raw in raw_clusters:
        main = _select_main_item(raw, ranking)
        dimensions = _dimension_scores(raw, main, ranking)
        topic_info = topic_decision(main, config=config)
        category = str(topic_info["topic"])
        score_breakdown = _event_score_breakdown(raw, main, dimensions, ranking, category=category)
        canonical_event_key = _canonical_event_key_for_items(raw)
        if canonical_event_key:
            score_breakdown["canonical_event_key"] = canonical_event_key
        entities = sorted(set().union(*(_entities(i) for i in raw)))[:12]
        clusters.append(EventCluster(
            event_id=_cluster_id(raw),
            items=sorted(raw, key=lambda item: _main_item_score(item, ranking), reverse=True),
            main_item=main,
            category=category,
            title=_clean_title(main.title) or "Untitled",
            url=main.url,
            source=main.source,
            source_tier=_source_tier_for_item(main),
            source_weight=source_weight(main.source, main.url),
            topic_weight=_topic_weight(category, ranking),
            topic_confidence=float(topic_info.get("confidence", 0.0) or 0.0),
            topic_method=str(topic_info.get("method") or "deterministic"),
            topic_reason=str(topic_info.get("reason") or ""),
            topic_model_used=bool(topic_info.get("model_used", False)),
            dimension_scores=dimensions,
            score_breakdown=score_breakdown,
            event_score=float(score_breakdown["final_score"]),
            key_entities=entities,
            canonical_event_key=canonical_event_key,
        ))
    clusters.sort(key=lambda c: (c.event_score, len(c.items), c.source_weight), reverse=True)
    return clusters



def select_top_event_clusters(
    clusters: list[EventCluster],
    top_n: int,
    config: dict[str, Any] | None = None,
) -> list[EventCluster]:
    """Select Top Headlines from ranked clusters with source/topic/umbrella lanes.

    `cluster_content_items()` remains the canonical event-score ranking. This
    selector only chooses which already-ranked event clusters are allowed into
    the scarce Top Headlines lane, preserving score order among selected items.
    """
    limit = max(0, int(top_n or 0))
    if limit <= 0 or not clusters:
        return []
    quota = _headline_quota_config(config)
    if not quota.get("enabled", True):
        return list(clusters[:limit])

    selected: list[EventCluster] = []
    selected_ids: set[str] = set()
    source_counts: defaultdict[str, int] = defaultdict(int)
    topic_counts: defaultdict[str, int] = defaultdict(int)
    umbrella_counts: defaultdict[str, int] = defaultdict(int)
    max_per_source = quota.get("max_per_source")
    max_per_umbrella = quota.get("max_per_umbrella")
    max_per_topic = quota.get("max_per_topic") or {}

    def can_select(
        cluster: EventCluster,
        *,
        relax_topic: bool = False,
        relax_source: bool = False,
        relax_umbrella: bool = False,
    ) -> bool:
        if cluster.event_id in selected_ids:
            return False
        if not relax_source and max_per_source is not None and source_counts[cluster.source or "unknown"] >= int(max_per_source):
            return False
        topic_cap = _quota_lookup(max_per_topic, cluster.category)
        if not relax_topic and topic_cap is not None and topic_counts[cluster.category] >= topic_cap:
            return False
        umbrella = _canonical_event_key_for_cluster(cluster)
        if umbrella and not relax_umbrella and max_per_umbrella is not None and umbrella_counts[umbrella] >= int(max_per_umbrella):
            return False
        return True

    def add(cluster: EventCluster) -> None:
        selected.append(cluster)
        selected_ids.add(cluster.event_id)
        source_counts[cluster.source or "unknown"] += 1
        topic_counts[cluster.category] += 1
        umbrella = _canonical_event_key_for_cluster(cluster)
        if umbrella:
            umbrella_counts[umbrella] += 1

    # Reserve a small number of slots for strategic lanes if they exist. This is
    # what keeps one hot research/social campaign from crowding out infra/tools.
    required = quota.get("min_required_if_available") or {}
    required_keys = sorted(
        required,
        key=lambda key: max((c.event_score for c in clusters if _topic_matches_quota_key(c.category, key)), default=-1.0),
        reverse=True,
    )
    for topic_key in required_keys:
        needed = int(required.get(topic_key) or 0)
        while needed > 0 and len(selected) < limit:
            current = sum(count for topic, count in topic_counts.items() if _topic_matches_quota_key(topic, topic_key))
            if current >= int(required.get(topic_key) or 0):
                break
            candidate = next(
                (cluster for cluster in clusters if _topic_matches_quota_key(cluster.category, topic_key) and can_select(cluster)),
                None,
            )
            if candidate is None:
                break
            add(candidate)
            needed -= 1

    def fill(*, relax_topic: bool = False, relax_source: bool = False, relax_umbrella: bool = False) -> None:
        for cluster in clusters:
            if len(selected) >= limit:
                return
            if can_select(cluster, relax_topic=relax_topic, relax_source=relax_source, relax_umbrella=relax_umbrella):
                add(cluster)

    fill()
    # Prefer a full headline section over an underfilled one. Relax in stages:
    # topic caps first, then source caps, and only finally umbrella caps.
    if len(selected) < limit:
        fill(relax_topic=True)
    if len(selected) < limit:
        fill(relax_topic=True, relax_source=True)
    if len(selected) < limit:
        fill(relax_topic=True, relax_source=True, relax_umbrella=True)

    original_order = {cluster.event_id: idx for idx, cluster in enumerate(clusters)}
    return sorted(selected[:limit], key=lambda cluster: original_order.get(cluster.event_id, 10**9))

def event_cluster_payload(cluster: EventCluster, *, max_supporting_sources: int = 6) -> dict:
    """Render an EventCluster as compact JSON for LLM prompts and ledgers."""
    supporting = evidence_sources(
        [_item_dict(item) for item in cluster.items],
        max_sources=max_supporting_sources,
        dedupe_by_identity=True,
        per_source_cap=2,
    )
    main = cluster.main_item
    content_bits = []
    if main.content:
        content_bits.append(main.content[:320])
    for item in cluster.items:
        if item is main or not item.content:
            continue
        content_bits.append(item.content[:180])
        if len(content_bits) >= 3:
            break
    return {
        "event_id": cluster.event_id,
        "title": cluster.title,
        "source": cluster.source,
        "source_tier": cluster.source_tier,
        "url": cluster.url,
        "content": "\n".join(content_bits)[:700],
        "score": cluster.event_score,
        "event_score": cluster.event_score,
        "source_weight": cluster.source_weight,
        "topic_weight": cluster.topic_weight,
        "topic_confidence": cluster.topic_confidence,
        "topic_method": cluster.topic_method,
        "topic_reason": cluster.topic_reason,
        "topic_model_used": cluster.topic_model_used,
        "dimension_scores": cluster.dimension_scores,
        "score_breakdown": cluster.score_breakdown,
        "cluster_size": len(cluster.items),
        "key_entities": cluster.key_entities,
        "canonical_event_key": _canonical_event_key_for_cluster(cluster),
        "selected_top_headline": False,
        "selection_rank": None,
        "supporting_sources": supporting,
        "supporting_sources_markdown": format_source_links(supporting),
    }
