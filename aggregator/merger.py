"""Cross-source topic clustering and categorization."""

from __future__ import annotations

import re
from copy import deepcopy
from functools import lru_cache
from typing import Any

from schema import ContentItem

# Default category mapping based on source + keywords
CATEGORY_RULES: dict[str, list[str]] = {
    "AI Models & Agent": ["huggingface", "coolpaper"],
    "Developer Tools": ["github"],
    "Infrastructure & Systems": [],
    "Research Papers": ["openreview"],  # Matched by arxiv_id presence too
    "Finance & Markets": ["finance"],
    "Videos & Podcasts": ["youtube", "apple_podcast", "ai_exec_podcast", "xiaoyuzhou"],
    "Books & Reading": ["weread", "kindle_books", "douban"],
    "Social & Community": ["x_twitter", "reddit", "zhihu", "jike", "hackernews"],
}

TOPIC_CATEGORIES = tuple(CATEGORY_RULES.keys())

# Invert: source -> category
_SOURCE_TO_CATEGORY: dict[str, str] = {}
for cat, sources in CATEGORY_RULES.items():
    for src in sources:
        _SOURCE_TO_CATEGORY[src] = cat

UNCERTAIN_TOPIC_SOURCES = {
    "x_twitter", "reddit", "zhihu", "jike", "hackernews", "alphaxiv",
    "youtube", "apple_podcast", "ai_exec_podcast", "xiaoyuzhou", "producthunt", "cn_tech_blog", "baoyu_blog", "blog_feeds",
}

DEFAULT_TOPIC_CLASSIFIER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "mode": "deterministic_with_optional_cheap_model",
    "min_confidence": 0.68,
    "uncertain_sources": sorted(UNCERTAIN_TOPIC_SOURCES),
    "cheap_model": {
        "enabled": False,
        "model_id": "microsoft-foundry/llab-gpt-5-mini",
        "alias": "llab-gpt-5 mini",
        "route_uncertain_only": True,
        "timeout_seconds": 8,
        "max_items_per_batch": 40,
    },
}

_TOPIC_KEYWORDS: dict[str, set[str]] = {
    "AI Models & Agent": {
        "ai", "llm", "llms", "model", "models", "agent", "agents", "agentic",
        "openai", "anthropic", "claude", "chatgpt", "gpt", "gemini", "deepseek",
        "kimi", "moonshot", "glm", "zhipu", "minimax", "xai", "grok", "llama", "meta",
        "thinking machines", "thinking machine", "cursor",
        "multimodal", "reasoning", "alignment", "eval", "benchmark",
        "大模型", "模型升级", "版本升级", "超长上下文", "多模态",
    },
    "Developer Tools": {
        "github", "release", "sdk", "api", "cli", "code", "coding", "developer",
        "devtools", "runtime", "library", "framework", "plugin", "workflow", "mcp",
        "copilot", "cursor", "codex", "opencode", "ollama",
        "skill", "skills", "tool use", "function calling", "ai assistant", "ai assistants",
        "开放平台", "开放能力", "服务调用", "一句话下单", "跑腿", "real-world service",
    },
    "Infrastructure & Systems": {
        "infra", "infrastructure", "server", "servers", "gpu", "cuda", "h100", "h200",
        "blackwell", "datacenter", "data", "center", "cloud", "deployment", "deploy",
        "inference", "serving", "latency", "throughput", "kernel", "storage",
    },
    "Research Papers": {
        "paper", "papers", "arxiv", "openreview", "research", "benchmark", "dataset",
        "method", "technical", "report", "study", "towards", "evaluating",
    },
    "Finance & Markets": {
        "stock", "market", "earnings", "revenue", "funding", "valuation", "shares",
        "nasdaq", "sec", "guidance", "forecast", "tariff",
    },
    "Videos & Podcasts": {"youtube", "podcast", "video", "episode", "interview", "course"},
    "Books & Reading": {"book", "books", "reading", "kindle", "weread", "douban"},
    "Social & Community": {"community", "reddit", "twitter", "x", "hackernews", "hn", "discussion"},
}

# Generic market/macro/earnings headlines are useful for Lee's investment report,
# but should not crowd out the AI daily report. Finance items stay in AI Daily
# only when they have a clear AI-industry-chain connection.
AI_RELEVANT_FINANCE_TERMS = {
    "ai", "artificial intelligence", "machine learning", "llm", "generative ai",
    "openai", "anthropic", "deepmind", "gemini", "claude", "chatgpt",
    "nvidia", "gpu", "h100", "h200", "b200", "blackwell", "cuda",
    "semiconductor", "chip", "chips", "accelerator", "tsmc", "asml", "broadcom",
    "data center", "datacenter", "cloud", "hyperscaler", "inference", "training",
    "ai server", "co-packaged optics", "hbm", "memory chip",
    "microsoft", "google", "alphabet", "meta", "amazon", "aws", "apple", "tesla",
}

BOOK_SOURCES = {"weread", "kindle_books", "douban"}
PRIMARY_AI_SOURCES = {
    "openai", "anthropic", "google_blog", "deepmind", "huggingface",
    "openreview", "coolpaper", "papers_cool", "alphaxiv",
}
BROAD_SIGNAL_SOURCES = {
    "x_twitter", "reddit", "hackernews", "zhihu", "jike", "youtube",
    "apple_podcast", "ai_exec_podcast", "xiaoyuzhou", "producthunt", "cn_tech_blog", "blog_feeds", "github",
}
AI_RELEVANCE_TAGS = {
    "ai", "llm", "ml", "model", "models", "agent", "agents", "infra",
    "research", "paper", "benchmark", "eval", "coding", "developer-tools",
    "developer_tools", "multimodal", "robotics", "rag", "inference",
}
AI_RELEVANCE_TERMS = {
    # Core AI / model terms
    "ai", "artificial intelligence", "agi", "llm", "large language model", "machine learning",
    "generative ai", "foundation model", "reasoning model", "multimodal", "vision-language",
    "openai", "chatgpt", "gpt", "anthropic", "claude", "deepmind", "gemini", "gemma",
    "llama", "mistral", "mixtral", "qwen", "kimi", "moonshot", "deepseek", "hugging face",
    "huggingface", "transformer", "diffusion", "embedding", "reranker", "benchmark", "eval",
    "fine-tuning", "finetune", "rlhf", "distillation", "alignment", "safety", "red team",
    # Agents / coding / application infra
    "agent", "agents", "agentic", "mcp", "rag", "retrieval", "tool use", "function calling",
    "cursor", "copilot", "codex", "claude code", "ai coding", "vibe coding", "devin",
    "langchain", "llamaindex", "vllm", "sglang", "ollama", "inference", "serving",
    # Hardware / infra / industry-chain
    "gpu", "nvidia", "cuda", "h100", "h200", "b200", "blackwell", "hbm", "datacenter",
    "data center", "ai server", "semiconductor", "chip", "accelerator", "tpu", "npu",
    "云", "算力", "芯片", "显卡", "推理", "训练", "大模型", "模型", "智能体", "多模态",
    "人工智能", "开源模型", "评测", "基准", "具身", "机器人", "自动驾驶", "代码生成",
}

PARADIGM_SHIFT_ROUTE_KEY = "route:paradigm-shift"
PARADIGM_SHIFT_ROUTE_LABEL = "AI paradigm shift: alternatives to the mainstream scaling route"

_PARADIGM_SHIFT_STRONG_PHRASES = {
    # Named examples / anchors for non-mainstream scale routes.
    "elf: embedded language flows",
    "embedded language flows",
    "cola-dlm",
    "cola dlm",
    "continuous latent diffusion language model",
    "continuous latent-space diffusion language model",
    "latent diffusion language model",
    "diffusion language model",
    "diffusion language models",
    "recursive self-improvement",
    "self-improving llm",
    "self-improving language model",
    "continual learning for language models",
    "continual learning for llms",
}
_PARADIGM_SHIFT_ROUTE_PHRASES = {
    # Alternative generation/objective routes.
    "continuous embedding",
    "continuous embeddings",
    "continuous latent",
    "continuous latent-space",
    "continuous latent space",
    "latent representation",
    "latent representations",
    "representation space",
    "representation-space",
    "discrete token",
    "discrete tokens",
    "token autoregression",
    "flow matching",
    "non-autoregressive",
    "non autoregressive",
    "not predict next token",
    "not predicting next token",
    "not next-token prediction",
    "not next token prediction",
    # Alternative learning/improvement routes.
    "continual learning",
    "continuous learning",
    "continue learning",
    "lifelong learning",
    "online learning",
    "test-time learning",
    "test time learning",
    "test-time adaptation",
    "test time adaptation",
    "self-improvement",
    "self improvement",
    "self-improving",
    "self improving",
    # Explicit anti-pure-scaling framing.
    "beyond scaling",
    "beyond scale",
    "scaling alternative",
    "alternatives to scaling",
    "non-scaling",
    "non scaling",
    "非主流 scale",
    "非 scale",
}
_PARADIGM_SHIFT_AI_CONTEXT = {
    "ai",
    "artificial intelligence",
    "model",
    "models",
    "foundation model",
    "foundation models",
    "language model",
    "language models",
    "language generation",
    "large language model",
    "large language models",
    "llm",
    "llms",
    "text generation",
    "agent",
    "agents",
    "tokens",
    "token",
    "大模型",
    "模型",
    "智能体",
}
_PARADIGM_SHIFT_NEGATED_NEXT_TOKEN_RE = re.compile(
    r"\b(?:not|without|beyond|abandon(?:ing)?|abandons?|drop(?:ping)?|drops?|"
    r"do(?:es)?\s+not|doesn't|don't)\b\s+(?:\w+\s+){0,4}"
    r"(?:predict(?:ing)?\s+(?:the\s+)?next[-\s]?token|next[-\s]?token\s+prediction)\b"
)
_PARADIGM_SHIFT_CHINESE_NEXT_TOKEN_RE = re.compile(
    r"(?:放弃|不再|不是|并非|无需).{0,12}预测.{0,4}下一个\s*token",
    re.IGNORECASE,
)

DEFAULT_CHEAP_RELEVANCE_MODEL = "microsoft-foundry/llab-gpt-5-mini"
DEFAULT_RELEVANCE_PREFILTER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "mode": "deterministic",
    "min_score": 0.35,
    "cheap_model": {
        "enabled": False,
        "model_id": DEFAULT_CHEAP_RELEVANCE_MODEL,
        "alias": "llab-gpt-5 mini",
        "route_uncertain_only": True,
        "uncertain_min_score": 0.20,
        "uncertain_max_score": 0.50,
        "timeout_seconds": 8,
    },
}


def relevance_prefilter_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return normalized AI Daily relevance prefilter settings.

    The optional cheap-model route is configuration-only here. This module does
    not call external models; deterministic rules remain the default and the
    fallback behavior for local validation/tests.
    """
    out = deepcopy(DEFAULT_RELEVANCE_PREFILTER_CONFIG)
    raw = ((config or {}).get("ai_daily") or {}).get("prefilter") or (config or {}).get("prefilter") or {}
    if not isinstance(raw, dict):
        return out
    for key in ("enabled", "mode", "min_score"):
        if key in raw:
            out[key] = raw[key]
    cheap_raw = raw.get("cheap_model")
    if isinstance(cheap_raw, dict):
        out["cheap_model"].update({k: v for k, v in cheap_raw.items() if v is not None})
    try:
        out["min_score"] = float(out["min_score"])
    except (TypeError, ValueError):
        out["min_score"] = DEFAULT_RELEVANCE_PREFILTER_CONFIG["min_score"]
    for key in ("uncertain_min_score", "uncertain_max_score", "timeout_seconds"):
        try:
            out["cheap_model"][key] = float(out["cheap_model"][key])
        except (TypeError, ValueError):
            out["cheap_model"][key] = DEFAULT_RELEVANCE_PREFILTER_CONFIG["cheap_model"][key]
    out["enabled"] = bool(out.get("enabled", True))
    out["cheap_model"]["enabled"] = bool(out["cheap_model"].get("enabled", False))
    return out


def prefilter_policy_metadata(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Expose routing policy metadata for ledgers/eval without making model calls."""
    settings = relevance_prefilter_config(config)
    cheap = settings["cheap_model"]
    return {
        "mode": settings["mode"],
        "min_score": settings["min_score"],
        "deterministic_fallback": True,
        "cheap_model_enabled": bool(cheap.get("enabled")),
        "cheap_model_id": cheap.get("model_id") or DEFAULT_CHEAP_RELEVANCE_MODEL,
        "cheap_model_alias": cheap.get("alias") or "llab-gpt-5 mini",
        "cheap_model_status": "configured_not_called" if cheap.get("enabled") else "disabled",
    }


def _item_search_text(item: ContentItem) -> str:
    parts = [item.title, item.content, item.author, item.source]
    parts.extend(item.tags or [])
    parts.extend(str(v) for v in (item.extra or {}).values() if isinstance(v, str))
    return " ".join(p for p in parts if p).lower()


@lru_cache(maxsize=8192)
def _compact_text(text: str) -> str:
    return re.sub(r"[\s_\-:：/·.'\"“”‘’「」]+", "", text.lower())


def _contains_phrase_or_compact(text: str, phrase: str) -> bool:
    phrase = phrase.lower()
    return phrase in text or _compact_text(phrase) in _compact_text(text)


@lru_cache(maxsize=4096)
def _has_paradigm_shift_route_text(text: str) -> bool:
    """Return true for a broader AI paradigm-shift route.

    This theme sits above a single modeling family: it captures credible
    alternatives to the current mainstream “scale larger transformers +
    next-token prediction” path, including representation-space generation
    (ELF/CoLa-DLM), continual/online learning, and self-improvement loops. The
    detector stays phrase/context-gated so generic image diffusion or generic
    lifelong-learning content does not route without AI/model context or a named
    anchor.
    """
    normalized = re.sub(r"\s+", " ", (text or "").lower())
    if any(_contains_phrase_or_compact(normalized, phrase) for phrase in _PARADIGM_SHIFT_STRONG_PHRASES):
        return True

    has_language_context = any(phrase in normalized for phrase in _PARADIGM_SHIFT_AI_CONTEXT)
    if not has_language_context:
        return False

    route_hits = sum(
        1
        for phrase in _PARADIGM_SHIFT_ROUTE_PHRASES
        if _contains_phrase_or_compact(normalized, phrase)
    )
    route_anchor = any(
        _contains_phrase_or_compact(normalized, phrase)
        for phrase in (
            "flow matching",
            "non-autoregressive",
            "non autoregressive",
            "continual learning",
            "continuous learning",
            "continue learning",
            "lifelong learning",
            "online learning",
            "test-time learning",
            "test time learning",
            "test-time adaptation",
            "test time adaptation",
            "self-improvement",
            "self improvement",
            "self-improving",
            "self improving",
            "beyond scaling",
            "beyond scale",
            "scaling alternative",
            "alternatives to scaling",
        )
    )
    if route_hits >= 2 and route_anchor:
        return True
    return bool(
        _PARADIGM_SHIFT_NEGATED_NEXT_TOKEN_RE.search(normalized)
        or _PARADIGM_SHIFT_CHINESE_NEXT_TOKEN_RE.search(normalized)
    )


def paradigm_shift_route_signal(item: ContentItem) -> bool:
    """Return whether an item belongs to the AI paradigm-shift route."""
    return _has_paradigm_shift_route_text(_item_search_text(item))


def is_ai_relevant_finance(item: ContentItem) -> bool:
    """Return true when a finance item is still relevant to AI Daily.

    This intentionally errs conservative: broad markets/macro/earnings stories
    are routed to the investment report unless they mention AI infrastructure,
    compute/GPU/cloud, AI companies, or AI industry-chain signals.
    """
    if item.source != "finance" and "finance" not in {t.lower() for t in item.tags}:
        return False
    text = _item_search_text(item)
    return any(term in text for term in AI_RELEVANT_FINANCE_TERMS)


def ai_relevance_score(item: ContentItem) -> float:
    """Deterministic all-source AI relevance score for AI Daily prefiltering.

    The goal is cost/control, not perfect classification: keep primary AI,
    infra, coding-agent, research and Lee-relevant signals; keep Books as their
    own topic; route generic finance elsewhere; drop broad non-AI chatter before
    the LLM prompt.
    """
    source = item.source
    tags = {t.lower() for t in (item.tags or [])}
    if source in BOOK_SOURCES or tags & {"book", "books", "reading"}:
        return 1.0
    if is_ai_relevant_finance(item):
        return 0.9

    text = _item_search_text(item)
    score = 0.0
    if _has_paradigm_shift_route_text(text):
        score += 0.35
    if source in PRIMARY_AI_SOURCES:
        score += 0.35
    if tags & AI_RELEVANCE_TAGS:
        score += 0.35
    hits = sum(1 for term in AI_RELEVANCE_TERMS if term in text)
    score += min(0.60, hits * 0.12)
    if re.search(r"\bai\b", text):
        score += 0.25
    if item.arxiv_id and hits:
        score += 0.15
    if source in {"github", "producthunt"} and hits:
        score += 0.10
    if source in BROAD_SIGNAL_SOURCES and not (tags & AI_RELEVANCE_TAGS) and hits == 0:
        score -= 0.30
    return round(max(0.0, min(1.0, score)), 3)


def is_ai_digest_relevant(item: ContentItem, min_score: float = 0.35) -> bool:
    """Return whether an item should remain in the AI Daily LLM input."""
    if should_route_to_investment_input(item):
        return False
    return ai_relevance_score(item) >= min_score


def should_route_to_investment_input(item: ContentItem) -> bool:
    """Generic finance should feed investment research, not AI Daily mainline."""
    return (item.source == "finance" or "finance" in {t.lower() for t in item.tags}) and not is_ai_relevant_finance(item)


def split_ai_digest_items_with_relevance(
    items: list[ContentItem],
    config: dict[str, Any] | None = None,
) -> tuple[list[ContentItem], list[ContentItem], list[ContentItem]]:
    """Split collected items into AI Daily, investment input, and low-relevance audit buckets."""
    settings = relevance_prefilter_config(config)
    min_score = float(settings.get("min_score", 0.35))
    ai_items: list[ContentItem] = []
    investment_inputs: list[ContentItem] = []
    low_relevance: list[ContentItem] = []
    for item in items:
        if should_route_to_investment_input(item):
            investment_inputs.append(item)
        elif not settings.get("enabled", True) or is_ai_digest_relevant(item, min_score=min_score):
            ai_items.append(item)
        else:
            low_relevance.append(item)
    return ai_items, investment_inputs, low_relevance


def split_ai_digest_items(
    items: list[ContentItem],
    config: dict[str, Any] | None = None,
) -> tuple[list[ContentItem], list[ContentItem]]:
    """Split collected items into AI Daily items and investment-report inputs.

    Backward-compatible wrapper; callers that need audit data should use
    split_ai_digest_items_with_relevance().
    """
    ai_items, investment_inputs, _low_relevance = split_ai_digest_items_with_relevance(items, config=config)
    return ai_items, investment_inputs


def topic_classifier_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return topic-classifier settings with deterministic safe defaults.

    The cheap-model branch is explicit metadata/hook configuration. This module
    never calls an external model by itself; callers may inject model decisions
    through item.extra["ai_topic"] or item.extra["topic_classifier"].
    """
    out = deepcopy(DEFAULT_TOPIC_CLASSIFIER_CONFIG)
    raw = ((config or {}).get("ai_daily") or {}).get("topic_classifier") or (config or {}).get("topic_classifier") or {}
    if isinstance(raw, dict):
        for key in ("enabled", "mode", "min_confidence", "uncertain_sources"):
            if key in raw:
                out[key] = raw[key]
        cheap_raw = raw.get("cheap_model")
        if isinstance(cheap_raw, dict):
            out["cheap_model"].update({k: v for k, v in cheap_raw.items() if v is not None})
    try:
        out["min_confidence"] = float(out.get("min_confidence", 0.68))
    except (TypeError, ValueError):
        out["min_confidence"] = DEFAULT_TOPIC_CLASSIFIER_CONFIG["min_confidence"]
    out["enabled"] = bool(out.get("enabled", True))
    out["uncertain_sources"] = {str(s).strip() for s in (out.get("uncertain_sources") or []) if str(s).strip()}
    out["cheap_model"]["enabled"] = bool(out["cheap_model"].get("enabled", False))
    try:
        out["cheap_model"]["timeout_seconds"] = float(out["cheap_model"].get("timeout_seconds", 8))
    except (TypeError, ValueError):
        out["cheap_model"]["timeout_seconds"] = 8.0
    try:
        out["cheap_model"]["max_items_per_batch"] = int(out["cheap_model"].get("max_items_per_batch", 40))
    except (TypeError, ValueError):
        out["cheap_model"]["max_items_per_batch"] = 40
    return out


def topic_classifier_policy_metadata(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Expose topic classifier routing policy for ledgers/reports."""
    settings = topic_classifier_config(config)
    cheap = settings["cheap_model"]
    return {
        "enabled": settings["enabled"],
        "mode": settings["mode"],
        "min_confidence": settings["min_confidence"],
        "uncertain_sources": sorted(settings["uncertain_sources"]),
        "deterministic_fallback": True,
        "cheap_model_enabled": bool(cheap.get("enabled")),
        "cheap_model_id": cheap.get("model_id") or DEFAULT_CHEAP_RELEVANCE_MODEL,
        "cheap_model_alias": cheap.get("alias") or "llab-gpt-5 mini",
        "cheap_model_status": "configured_not_called_in_merger" if cheap.get("enabled") else "disabled",
    }


def _valid_topic(value: Any) -> str | None:
    topic = str(value or "").strip()
    return topic if topic in TOPIC_CATEGORIES else None


def _explicit_topic_decision(item: ContentItem) -> dict[str, Any] | None:
    extra = item.extra or {}
    classifier = extra.get("topic_classifier") if isinstance(extra.get("topic_classifier"), dict) else {}
    for raw in (classifier.get("topic"), extra.get("ai_topic"), extra.get("topic"), extra.get("category")):
        topic = _valid_topic(raw)
        if topic:
            return {
                "topic": topic,
                "confidence": float(classifier.get("confidence") or 1.0),
                "method": str(classifier.get("method") or "explicit_override"),
                "reason": str(classifier.get("reason") or "explicit topic override from item.extra"),
                "model_used": bool(classifier.get("model_used", False)),
            }

    search_text = _item_search_text(item)
    normalized = search_text.lower()
    service_skill_markers = (
        " skill", "skills ", " ai助手", "ai 助手", "ai assistant", "mcp",
        "开放能力", "开放平台", "服务调用", "一句话下单", "tool use", "function calling",
    )
    real_world_service_markers = (
        "跑腿", "下单", "外卖", "打车", "配送", "预约", "上门", "酒店", "机票", "real-world service",
    )
    if any(marker in normalized for marker in service_skill_markers) and any(marker in normalized for marker in real_world_service_markers):
        return {
            "topic": "Developer Tools",
            "confidence": 0.95,
            "method": "deterministic_service_skill_promotion",
            "reason": "traditional service opened Skill/API so AI assistants can directly call a real-world service",
            "model_used": False,
        }
    if _has_paradigm_shift_route_text(normalized):
        return {
            "topic": "AI Models & Agent",
            "confidence": 0.94,
            "method": "deterministic_paradigm_shift_route",
            "reason": "AI paradigm shift: credible alternative to the mainstream scale route, e.g. representation-space generation, continual learning, or self-improvement",
            "model_used": False,
        }
    return None


def _default_topic_for_item(item: ContentItem) -> tuple[str, str, float]:
    """Legacy deterministic topic route: source mapping + tag/arxiv overrides."""
    if item.source in {"hackernews", "alphaxiv"}:
        tags = {tag.lower() for tag in item.tags}
        if "infra" in tags:
            return "Infrastructure & Systems", "tag:infra override for broad/community source", 0.88
        if "ai" in tags:
            return "AI Models & Agent", "tag:ai override for broad/community source", 0.86

    if item.arxiv_id:
        return "Research Papers", "arxiv_id present", 0.95

    topic = _SOURCE_TO_CATEGORY.get(item.source, "Social & Community")
    text = f"{(item.title or '').lower()} {(item.content or '').lower()}"
    model_release_markers = (
        "deepseek", "kimi", "moonshot", "glm", "zhipu", "minimax", "xai", "grok", "llama", "meta",
        "thinking machines", "thinking machine", "cursor", "大模型", "模型升级", "版本升级", "超长上下文", "多模态",
    )
    if topic == "Social & Community" and any(marker in text for marker in model_release_markers):
        topic = "AI Models & Agent"
    confidence = 0.88 if item.source in _SOURCE_TO_CATEGORY else 0.55
    return topic, f"source mapping: {item.source or 'unknown'}", confidence


def _keyword_topic_scores(item: ContentItem) -> dict[str, float]:
    tags = {tag.lower() for tag in item.tags}
    text_tokens = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_+.-]{1,}|[一-鿿]{2,}", _item_search_text(item)))
    scores = {topic: 0.0 for topic in TOPIC_CATEGORIES}
    source_topic = _SOURCE_TO_CATEGORY.get(item.source)
    if source_topic:
        scores[source_topic] += 0.45
    if item.arxiv_id:
        scores["Research Papers"] += 1.0
    for topic, keywords in _TOPIC_KEYWORDS.items():
        tag_hits = tags & keywords
        text_hits = text_tokens & keywords
        scores[topic] += min(0.45, len(tag_hits) * 0.18)
        scores[topic] += min(0.55, len(text_hits) * 0.08)
    if item.source in {"youtube", "apple_podcast", "ai_exec_podcast", "xiaoyuzhou"}:
        scores["Videos & Podcasts"] += 0.35
    if item.source in {"weread", "kindle_books", "douban"}:
        scores["Books & Reading"] += 0.50
    return scores


def topic_decision(item: ContentItem, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return an auditable topic decision for a content item.

    Classification is deterministic-first. A future/outer cheap-model classifier
    can write `item.extra["topic_classifier"] = {topic, confidence, reason}`;
    this function will honor that explicit decision and preserve fallback behavior.
    """
    explicit = _explicit_topic_decision(item)
    if explicit:
        return explicit

    default_topic, default_reason, default_confidence = _default_topic_for_item(item)
    settings = topic_classifier_config(config)
    if default_reason.startswith("tag:") or not settings.get("enabled", True):
        return {
            "topic": default_topic,
            "confidence": default_confidence,
            "method": "legacy_deterministic" if not settings.get("enabled", True) else "deterministic_tag_override",
            "reason": default_reason,
            "model_used": False,
        }

    scores = _keyword_topic_scores(item)
    best_topic, best_score = max(scores.items(), key=lambda kv: kv[1])
    sorted_scores = sorted(scores.values(), reverse=True)
    margin = best_score - (sorted_scores[1] if len(sorted_scores) > 1 else 0.0)
    uncertain_source = item.source in settings.get("uncertain_sources", set())
    confidence = round(min(0.98, max(default_confidence, 0.45 + best_score * 0.35 + margin * 0.20)), 2)

    if uncertain_source and best_score >= 0.75 and margin >= 0.12:
        return {
            "topic": best_topic,
            "confidence": confidence,
            "method": "deterministic_topic_classifier",
            "reason": f"uncertain source keyword/topic score: {best_topic}={best_score:.2f}, margin={margin:.2f}",
            "model_used": False,
            "cheap_model_eligible": bool(settings["cheap_model"].get("enabled")) and confidence < float(settings.get("min_confidence", 0.68)),
        }

    return {
        "topic": default_topic,
        "confidence": default_confidence,
        "method": "deterministic_source_route",
        "reason": default_reason,
        "model_used": False,
        "cheap_model_eligible": bool(settings["cheap_model"].get("enabled")) and uncertain_source and default_confidence < float(settings.get("min_confidence", 0.68)),
    }


def categorize_item(item: ContentItem, config: dict[str, Any] | None = None) -> str:
    """Assign a category/topic to an item with auditable deterministic rules."""
    return str(topic_decision(item, config=config)["topic"])


def group_by_category(
    items: list[ContentItem],
    categories: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, list[ContentItem]]:
    """Group items by category.

    Args:
        items: All content items.
        categories: Ordered list of category names. If None, uses CATEGORY_RULES keys.

    Returns:
        Dict of category -> items, ordered by the categories list.
    """
    if categories is None:
        categories = list(CATEGORY_RULES.keys())

    grouped: dict[str, list[ContentItem]] = {cat: [] for cat in categories}

    for item in items:
        cat = categorize_item(item, config=config)
        if cat in grouped:
            grouped[cat].append(item)
        else:
            grouped.setdefault("Social & Community", []).append(item)

    # Sort each category by score
    for cat in grouped:
        grouped[cat].sort(key=lambda x: x.score, reverse=True)

    return grouped
