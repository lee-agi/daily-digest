"""Cross-source validation helpers for report generation.

Importance is estimated from two explicit signals:
1. number of distinct cited information sources;
2. the configured weight of each cited source.

The helpers intentionally stay deterministic and lightweight so periodic reports can
append evidence metadata even when LLM summarization is skipped or cached.
"""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import yaml

Source = dict[str, object]

# Higher = closer to primary / authoritative signal. These are intentionally
# conservative defaults; unknown sources still count, but with lower weight.
DEFAULT_SOURCE_WEIGHTS: dict[str, float] = {
    # Official / primary channels
    "openai": 1.6,
    "openai.com": 1.6,
    "anthropic": 1.6,
    "anthropic.com": 1.6,
    "google": 1.5,
    "blog.google": 1.5,
    "deepmind.google": 1.5,
    "microsoft": 1.5,
    "microsoft.com": 1.5,
    "github": 1.3,
    "github.com": 1.3,
    "arxiv": 1.3,
    "arxiv.org": 1.3,
    "openreview": 1.4,
    "openreview.net": 1.4,
    "alphaxiv": 1.1,
    "alphaxiv.org": 1.1,
    "huggingface": 1.2,
    "huggingface.co": 1.2,
    "producthunt": 0.9,
    "producthunt.com": 0.9,
    # Social/community signals: useful corroboration, not primary proof.
    "x": 0.7,
    "twitter": 0.7,
    "x.com": 0.7,
    "twitter.com": 0.7,
    "reddit": 0.7,
    "reddit.com": 0.7,
    "hackernews": 0.7,
    "news.ycombinator.com": 0.7,
    "finance": 1.0,
    "finance.yahoo.com": 1.0,
    "cnbc.com": 1.0,
    "marketwatch.com": 1.0,
    "wsj.com": 1.1,
    "sec.gov": 1.5,
    "youtube": 0.6,
    "youtube.com": 0.6,
    "youtu.be": 0.6,
    "zhihu": 0.6,
    "zhihu.com": 0.6,
    "xiaoyuzhou": 0.6,
    "xiaoyuzhoufm.com": 0.6,
    "ai_exec_podcast": 1.5,
    "blog_feeds": 1.0,
    "nextsignalprediction.substack.com": 1.0,
    "baoyu.io": 1.4,
    "s.baoyu.io": 1.4,
    "weread": 0.5,
    "kindle_books": 0.5,
    "douban": 0.5,
}
DEFAULT_SOURCE_WEIGHT = 0.6

# Source tiers drive event main-source selection and deterministic ranking.
# Higher tier means closer to primary evidence. Social/media sources are still
# valuable as heat/corroboration, but should not displace an official release as
# the visible main link for an event.
DEFAULT_SOURCE_TIER_WEIGHTS: dict[str, float] = {
    "T1": 1.12,   # official blogs, release notes, papers, primary docs
    "T1.5": 1.06, # official social / developer platforms / model hubs
    "T2": 1.00,   # high-quality community or specialist sources
    "T3": 0.90,   # broad media / generic heat signals
}

DEFAULT_TIER_SOURCES: dict[str, set[str]] = {
    # T1 is restricted to highest-confidence primary evidence. Paper sources are
    # T1.5 by default; OpenReview oral-or-above promotion is handled with item
    # metadata in the event ranker, not by host-level tier.
    "T1": {"openai", "anthropic", "google_blog", "google", "deepmind"},
    "T1.5": {
        "microsoft", "meta", "nvidia", "apple", "baidu", "huggingface_blog",
        "openreview", "arxiv", "papers", "coolpaper", "ccf_bestpaper",
        "x_twitter", "github", "huggingface", "alphaxiv", "producthunt",
        "apple_podcast", "ai_exec_podcast", "youtube",
    },
    "T2": {"reddit", "hackernews", "zhihu", "xiaoyuzhou", "cn_tech_blog", "blog_feeds"},
    "T3": set(),
}
DEFAULT_TIER_HOSTS: dict[str, set[str]] = {
    "T1": {"openai.com", "anthropic.com", "google.com", "blog.google", "deepmind.google"},
    "T1.5": {
        "microsoft.com", "research.microsoft.com", "ai.meta.com", "nvidia.com",
        "developer.nvidia.com", "apple.com", "machinelearning.apple.com",
        "huggingface.co", "openreview.net", "arxiv.org", "papers.cool",
        "x.com", "twitter.com", "github.com", "gist.github.com", "alphaxiv.org",
        "producthunt.com", "youtube.com", "youtu.be", "baoyu.io", "s.baoyu.io",
    },
    "T2": {"reddit.com", "news.ycombinator.com", "zhihu.com", "nextsignalprediction.substack.com"},
    "T3": set(),
}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_REGISTRY_PATH = _PROJECT_ROOT / "source_registry.yaml"


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _float_mapping(raw: Any, defaults: dict[str, float], *, lowercase_keys: bool = True) -> dict[str, float]:
    out = dict(defaults)
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        normalized = _normalize_key(key) if lowercase_keys else str(key or "").strip()
        if not normalized:
            continue
        try:
            out[normalized] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _tier_sets(raw: Any, field: str, defaults: dict[str, set[str]]) -> dict[str, set[str]]:
    out = {tier: set(values) for tier, values in defaults.items()}
    if not isinstance(raw, dict):
        return out
    for tier, spec in raw.items():
        tier_key = str(tier or "").strip()
        if not tier_key or not isinstance(spec, dict):
            continue
        values = {
            _normalize_key(value)
            for value in (spec.get(field) or [])
            if _normalize_key(value)
        }
        if values:
            out.setdefault(tier_key, set()).update(values)
    return out


def load_source_registry(path: Path | str | None = None) -> dict[str, Any]:
    """Load source weights/tiers from YAML with conservative built-in fallback."""
    registry_path = Path(path) if path is not None else DEFAULT_SOURCE_REGISTRY_PATH
    defaults = {
        "default_source_weight": DEFAULT_SOURCE_WEIGHT,
        "source_weights": deepcopy(DEFAULT_SOURCE_WEIGHTS),
        "tier_weights": deepcopy(DEFAULT_SOURCE_TIER_WEIGHTS),
        "tier_sources": {tier: set(values) for tier, values in DEFAULT_TIER_SOURCES.items()},
        "tier_hosts": {tier: set(values) for tier, values in DEFAULT_TIER_HOSTS.items()},
    }
    try:
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return defaults
    if not isinstance(data, dict):
        return defaults

    try:
        default_weight = float(data.get("default_source_weight", DEFAULT_SOURCE_WEIGHT))
    except (TypeError, ValueError):
        default_weight = DEFAULT_SOURCE_WEIGHT

    return {
        "default_source_weight": default_weight,
        "source_weights": _float_mapping(data.get("source_weights"), DEFAULT_SOURCE_WEIGHTS),
        "tier_weights": _float_mapping(data.get("tier_weights"), DEFAULT_SOURCE_TIER_WEIGHTS, lowercase_keys=False),
        "tier_sources": _tier_sets(data.get("tiers"), "sources", DEFAULT_TIER_SOURCES),
        "tier_hosts": _tier_sets(data.get("tiers"), "hosts", DEFAULT_TIER_HOSTS),
    }


_REGISTRY = load_source_registry()
SOURCE_WEIGHTS: dict[str, float] = _REGISTRY["source_weights"]
SOURCE_TIER_WEIGHTS: dict[str, float] = _REGISTRY["tier_weights"]
SOURCE_TIER_SOURCES: dict[str, set[str]] = _REGISTRY["tier_sources"]
SOURCE_TIER_HOSTS: dict[str, set[str]] = _REGISTRY["tier_hosts"]
CONFIGURED_DEFAULT_SOURCE_WEIGHT: float = float(_REGISTRY["default_source_weight"])


def normalize_source_name(source: str | None = None, url: str | None = None) -> str:
    """Return a stable human-readable source name."""
    source = (source or "").strip()
    if source:
        return source
    host = url_host(url or "")
    return host or "unknown"


def url_host(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return host.removeprefix("www.")


def _source_candidates(source: str | None = None, url: str | None = None) -> list[str]:
    candidates: list[str] = []
    if source:
        s = source.strip().lower()
        candidates.extend([s, s.replace("_", " "), s.replace(" ", "_")])
    host = url_host(url or "")
    if host:
        candidates.append(host)
        parts = host.split(".")
        if len(parts) >= 2:
            candidates.append(".".join(parts[-2:]))
    return candidates


def source_weight(source: str | None = None, url: str | None = None) -> float:
    """Weight a source by configured source name or URL host."""
    for c in _source_candidates(source, url):
        if c in SOURCE_WEIGHTS:
            return SOURCE_WEIGHTS[c]
    return CONFIGURED_DEFAULT_SOURCE_WEIGHT


def source_tier(source: str | None = None, url: str | None = None) -> str:
    """Return a coarse tier label used for event-level ranking and main-source choice."""
    candidates = set(_source_candidates(source, url))
    for tier in ("T1", "T1.5", "T2"):
        if candidates & SOURCE_TIER_SOURCES.get(tier, set()) or candidates & SOURCE_TIER_HOSTS.get(tier, set()):
            return tier
    return "T3"


def source_tier_weight(source: str | None = None, url: str | None = None) -> float:
    return SOURCE_TIER_WEIGHTS.get(source_tier(source, url), SOURCE_TIER_WEIGHTS["T3"])


def source_identity(source: str | None = None, url: str | None = None) -> str:
    """Return the independent source body used for cross-validation diversity.

    Counting only URLs can overstate validation when many citations come from the
    same publisher. This identity keeps official sites as one body, but preserves
    project/account-level independence for community/developer platforms.
    """
    raw_url = (url or "").strip()
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower().removeprefix("www.")
    path_parts = [p for p in parsed.path.split("/") if p]
    if parsed.scheme == "file":
        return parsed.path or normalize_source_name(source, raw_url)
    if host in {"github.com", "gist.github.com"}:
        return "/".join([host, *path_parts[:2]]) if path_parts else host
    if host == "huggingface.co":
        return "/".join([host, *path_parts[:2]]) if path_parts else host
    if host in {"x.com", "twitter.com"}:
        return f"{host}/{path_parts[0]}" if path_parts else host
    if host == "reddit.com":
        return "/".join([host, *path_parts[:2]]) if len(path_parts) >= 2 and path_parts[0] == "r" else host
    if host in {"youtube.com", "youtu.be"}:
        # A YouTube video URL identifies a clip, not an independent source body.
        # Without channel/account metadata, treating every video_id as a separate
        # identity overstates cross-source corroboration for conference playlists.
        return "youtube.com"
    if host in {"arxiv.org", "papers.cool", "alphaxiv.org", "openreview.net"}:
        return "/".join([host, *path_parts[-1:]]) if path_parts else host
    return host or normalize_source_name(source, raw_url).lower()


def source_diversity_count(sources: Iterable[Source]) -> int:
    identities = {str(s.get("identity") or source_identity(str(s.get("name") or ""), str(s.get("url") or ""))) for s in sources}
    identities.discard("")
    return len(identities)


def evidence_sources(
    items: Iterable[dict],
    max_sources: int = 6,
    *,
    dedupe_by_identity: bool = False,
    per_source_cap: int | None = None,
) -> list[Source]:
    """Build a deduplicated, weighted source list from report/item dicts.

    Default behavior deduplicates exact URLs, preserving legacy report output.
    For cross-validation/importance analysis, set dedupe_by_identity=True and
    per_source_cap to avoid over-counting many links from the same platform
    (for example GitHub releases or WeRead books) as independent evidence.
    """
    merged: dict[str, Source] = {}
    source_counts: dict[str, int] = {}
    for item in items:
        url = str(item.get("url") or item.get("report_url") or item.get("path_url") or "").strip()
        name = normalize_source_name(str(item.get("source") or item.get("report") or "").strip(), url)
        identity = source_identity(name, url)
        key = (identity if dedupe_by_identity else (url or name)).lower()
        if not key:
            continue
        source_key = name.lower()
        if per_source_cap is not None and key not in merged and source_counts.get(source_key, 0) >= per_source_cap:
            continue
        weight = float(item.get("source_weight") or source_weight(name, url))
        existing = merged.get(key)
        if existing:
            existing["weight"] = max(float(existing.get("weight", 0)), weight)
            existing["count"] = int(existing.get("count", 1)) + 1
            continue
        merged[key] = {"name": name, "url": url, "weight": weight, "count": 1, "identity": identity}
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
    return sorted(merged.values(), key=lambda x: (-float(x.get("weight", 0)), str(x.get("name", "")).lower()))[:max_sources]


def evidence_score(sources: Iterable[Source]) -> float:
    return round(sum(float(s.get("weight", 0)) for s in sources), 2)


def format_source_links(sources: Iterable[Source]) -> str:
    """Render sources as standard Markdown links, with weight for auditability.

    URL destinations are percent-encoded where needed so the report can use
    broadly compatible `[label](url)` links without leaking literal angle
    brackets into PDF/chat clients.
    """
    parts: list[str] = []
    for src in sources:
        name = str(src.get("name") or "unknown")
        url = str(src.get("url") or "").strip()
        weight = float(src.get("weight", 0))
        label = f"{name}({weight:.1f})"
        if url:
            encoded_url = quote(url, safe=":/?#@!$&'*,;=%+")
            parts.append(f"[{label}]({encoded_url})")
        else:
            parts.append(label)
    return " / ".join(parts) if parts else "暂无"


def validation_label(sources: Iterable[Source]) -> str:
    sources = list(sources)
    return f"{len(sources)} 源 / {source_diversity_count(sources)} 主体 / 权重 {evidence_score(sources):.1f}"
