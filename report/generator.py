"""LLM-based digest report generation using Opus.

Summary prompt style references:
- any2summary/prompts/article_summary_prompt.txt
- any2summary/prompts/summary_prompt.txt
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from aggregator.dedup import deduplicate
from aggregator.event_cluster import cluster_content_items, event_cluster_payload, select_top_event_clusters
from aggregator.merger import group_by_category
from report.event_ledger import build_event_ledger, write_event_ledger
from schema import ContentItem, DigestReport
from report.usage import (
    append_usage_section,
    usage_summary_with_openclaw,
    extract_response_usage,
    record_usage,
)
from report.source_validation import source_weight
from report.style import language_style_section

logger = logging.getLogger(__name__)

_URL_OPEN_CACHE: dict[str, bool] = {}
_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Summary prompt inspired by any2summary style
DIGEST_PROMPT = """你是一个多源信息聚合摘要助手。你的任务是将来自多个平台（Twitter/X、GitHub、Reddit、Hacker News、OpenReview、alphaXiv、金融市场 RSS/SEC、YouTube、知乎、即刻、小宇宙、HuggingFace、papers.cool、Apple Podcast、微信读书、Product Hunt）的内容条目，生成一份高质量的每日精选报告。

## 输入
以下是按分类整理、按 `event_score` 排序的事件簇（JSON 格式），不是原始链接列表。系统已经在生成前完成 URL/标题去重、事件级聚类、信源分级、主来源选择和硬失效链接过滤；如果事件没有 url，就不要为它编造链接：

{categorized_json}

## 输出要求

{language_style_rules}

### 1. Today's Top {top_n} Headlines
- 使用输入中 `selected_top_headline=true` 的事件簇，并按 `selection_rank` 从小到大输出；不要改用未选中的高分同源/同主题/同 umbrella 事件
- 不要重新把同一 `event_id` 的 supporting_sources 拆成多条
- 每条用一句话概括，优先解释这个事件为什么重要，而不是复述标题
- 优先使用事件簇的 `url` / 主来源作为链接；`supporting_sources` 只作为旁证，不要挤占 Top Headlines 名额
- 重要的、insightful、非共识的内容用 **加粗** 标识
- 每条必须使用输入里的原始 url 作为链接；链接必须是 Markdown 标准格式 `[标题](<url>)`；禁止输出裸 URL；如果输入没有 url，直接省略链接，不要编造

### 2. 主题与分类精选
把原来的“跨平台主题分析”和“分类详情”合并为一个部分，减少重复：
- 先用 2-4 个小节概括今天跨来源反复出现的核心主题 / 趋势；每个主题下面直接引用相关分类和代表事件
- 再按分类列表补充未覆盖但值得保留的重点；每个分类最多保留最重要的 3-6 个事件
- 已进入 `Today's Top Headlines` 的事件不要在本节重复展示；本节只放 Top Headlines 之外的补充信号或更高层主题判断
- 每个分类/主题都要有简短判断：不超过 5 句话，包含非共识的 insight；不要堆砌条目
- 每个事件保留主来源原文链接；可在句末用“旁证：...”概括 supporting_sources，但不要堆链接
- 作者存在时保留作者，不存在时直接省略，不要写“作者未知”或“Unknown author”
- 链接必须来自输入 url，格式统一为 `[标题](<url>)`；禁止裸 URL；不要输出无法确认的链接
- 如果是非中文内容，专业表达保留原文，口语化部分翻译成中文
- 对于 Books & Reading 中带 `ranking` 字段的条目，优先把 `ranking.summary` 改写成自然语言解释；必要时再参考 `base_signal`、`base_score`、`multiplier`、`factors`。最终成稿中不要直接出现 `base_signal`、`base_score`、`multiplier`、`factors`、`rank_bonus`、`rating_bonus` 这类字段名，而要翻译成自然中文表达；也不要把乘数修正误读为原始热度本身

分类列表：{categories}

## 不要输出以下内容
- 不要输出“重要性与交叉验证”
- 不要输出“高权重主题/分类”
- 不要输出“重要候选信息”
- 不要输出英文“Platform Statistics”；中文“平台统计”由系统自动生成并放在报告前面，不要在正文中重复生成
- 不要重复拆成单独的“跨平台主题分析”和“分类详情”两个部分

## 信息保真规范
1. 遵守“表达与术语原则”，避免硬翻专业词汇；常见口语可自然中文化
2. 不要压缩、省略或遗漏任何关键信息
3. 将重要的、insightful 的内容用 markdown **加粗** 标识，特别重要的用 `高亮`
4. 所有链接必须是 Markdown 标准格式 `[label](<url>)`；禁止裸 URL；禁止输出 404/410 等硬失效链接

请输出完整的 Markdown 格式报告。
"""


# Avoid asking one LLM call to digest hundreds of long items at once. In recent
# runs a ~234k-character prompt produced a long but structurally cut-off report,
# which then degraded to raw fallback. The robust path is now:
#   1) deterministic importance ledger from the full input,
#   2) chunk summaries with deterministic per-chunk fallback,
#   3) incremental compaction when chunk summaries are themselves too long,
#   4) final synthesis, then deterministic ledger-coverage repair.
DEFAULT_DIRECT_PROMPT_CHAR_LIMIT = 120_000
DEFAULT_CHUNK_CHAR_LIMIT = 55_000
DEFAULT_FINAL_SYNTHESIS_CHAR_LIMIT = 95_000
DEFAULT_COMPACT_BATCH_CHAR_LIMIT = 65_000
DEFAULT_COVERAGE_LEDGER_MAX_ITEMS = 80
DEFAULT_COVERAGE_REQUIRED_ITEMS = 35
DEFAULT_COVERAGE_PER_CATEGORY = 8

CHUNK_SUMMARY_PROMPT = """你是 Daily Digest 的分块摘要器。下面是当天采集数据的第 {chunk_index}/{chunk_count} 个分块（JSON）。

目标：只提炼这个分块里的高价值事件簇，供最终日报二次综合；不要写完整日报，不要写平台统计。

输出要求：
- 中文为主，保留必要英文术语。
- 分成三段：`### 重要候选`、`### 主题信号`、`### 分类补充`。
- 重要候选最多 12 个事件；每个事件必须保留输入中的主来源 Markdown 链接 `[标题](<url>)`，没有 url 就不编造；不要把同一 `event_id` 的 supporting_sources 拆成多条。
- 主题信号最多 5 条，每条 1-2 句，指出为什么值得关注。
- 分类补充按分类列出，每类最多 3 条。
- 不要输出“平台统计”“信息源可用性”。

分块 JSON：
{chunk_json}
"""

COMPACT_SUMMARY_PROMPT = """你是 Daily Digest 的中间压缩器。下面是若干个分块摘要；它们太长，不能一次进入最终综合。

目标：做“保真压缩”，不是重新创作。宁可少写判断，也不要丢掉高影响候选、关键链接、来源、趋势变化。

输出要求：
- 保留 `### 重要候选`、`### 主题信号`、`### 分类补充` 三段。
- 合并近似重复项，但必须保留原始 Markdown 链接 `[标题](<url>)`。
- 重要候选最多 18 条；主题信号最多 8 条；分类补充每类最多 4 条。
- 不要输出平台统计或信息源状态。

分块摘要：
{chunk_summaries}
"""

CHUNK_FINAL_PROMPT = """你是 Daily Digest 的最终编辑。下面是同一天多个分块摘要的结果，以及系统从完整输入中确定的“重要信息覆盖台账”。请基于它们生成最终日报正文。

注意：系统会在正文前自动添加“平台统计”和“信息源可用性与自修复”，所以你不要重复输出这些部分。

{language_style_rules}

硬性保真要求：
- `must_cover` 是系统从完整输入按事件分、来源权重、分类均衡挑出的重要事件台账；最终正文必须覆盖这些事件，至少保留标题或链接。
- 如果某个重要事件不适合放进 Top Headlines，也要放入主题或分类补充；不要因为分块摘要压缩而丢失。
- 不要把同一 `event_id` 的 supporting_sources 拆成多个 Top Headlines；supporting_sources 只作为旁证。
- 不要编造新链接；只能使用分块摘要或覆盖台账里的 Markdown 链接/URL。

输出结构：
## 1. Today's Top {top_n} Headlines
- 使用覆盖台账 `must_cover` 前 {top_n} 个事件；每条一句话概括。
- 必须使用已有 Markdown 链接 `[标题](<url>)`；没有链接就不要编造。
- 重要、insightful、非共识内容用 **加粗**。

## 2. 主题与分类精选
- 先用 2-4 个主题小节概括今天反复出现的核心趋势。
- 再按分类补充未覆盖但值得保留的重点；已进入 `Today's Top Headlines` 的事件不要在本节重复展示。
- 每个主题/分类不超过 5 句话，避免堆砌条目。
- 不要输出“重要性与交叉验证”“高权重主题/分类”“重要候选信息”。

重要信息覆盖台账：
{coverage_ledger}

分块摘要：
{chunk_summaries}

请输出完整 Markdown 正文。
"""


def _summary_int(config: dict, key: str, default: int) -> int:
    try:
        return int((config.get("summary") or {}).get(key, default))
    except Exception:
        return default


def _compact_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return default


def _ledger_key(row: dict) -> str:
    event_id = str(row.get("event_id") or "").strip().lower()
    if event_id:
        return f"event:{event_id}"
    url = str(row.get("url") or "").strip().lower()
    if url:
        return f"url:{url}"
    title = " ".join(str(row.get("title") or "").lower().split())
    source = str(row.get("source") or "").lower()
    return f"title:{source}:{title}"


def _item_priority(category: str, item: dict) -> tuple[float, float, float, float, str]:
    selection_rank = item.get("selection_rank")
    selected = 1.0 if item.get("selected_top_headline") and selection_rank is not None else 0.0
    try:
        rank_component = -float(selection_rank) if selected else 0.0
    except (TypeError, ValueError):
        rank_component = 0.0
    return (
        selected,
        rank_component,
        _safe_float(item.get("event_score") or item.get("score")),
        _safe_float(item.get("source_weight")),
        str(item.get("title") or ""),
    )


def _ledger_row(category: str, item: dict) -> dict:
    content = str(item.get("content") or "").strip()
    ranking = item.get("ranking") or {}
    row = {
        "category": category,
        "event_id": item.get("event_id") or "",
        "title": _display_title(str(item.get("title") or "Untitled")),
        "url": item.get("url") or "",
        "source": item.get("source") or "",
        "source_tier": item.get("source_tier") or "",
        "score": item.get("event_score") or item.get("score"),
        "event_score": item.get("event_score") or item.get("score"),
        "source_weight": item.get("source_weight"),
        "dimension_scores": item.get("dimension_scores") or {},
        "cluster_size": item.get("cluster_size") or 1,
        "supporting_sources": item.get("supporting_sources") or [],
        "supporting_sources_markdown": item.get("supporting_sources_markdown") or "",
        "selected_top_headline": bool(item.get("selected_top_headline", False)),
        "selection_rank": item.get("selection_rank"),
        "canonical_event_key": item.get("canonical_event_key") or "",
        "reason": content[:220],
    }
    if ranking.get("summary"):
        row["ranking_summary"] = ranking.get("summary")
    return row


def _build_importance_ledger(
    categorized_for_llm: dict[str, list[dict]],
    config: dict,
    *,
    top_n: int,
    categories: list[str],
) -> dict[str, list[dict]]:
    """Build a deterministic coverage ledger from the complete input.

    Chunk summaries are lossy by design. This ledger is the non-LLM safety net:
    high-score/high-weight items and top items from each category are carried all
    the way to final synthesis, then checked again after the LLM response.
    """
    max_items = _summary_int(config, "coverage_ledger_max_items", DEFAULT_COVERAGE_LEDGER_MAX_ITEMS)
    required_items = _summary_int(config, "coverage_required_items", DEFAULT_COVERAGE_REQUIRED_ITEMS)
    per_category = _summary_int(config, "coverage_per_category", DEFAULT_COVERAGE_PER_CATEGORY)
    required_items = max(top_n, min(required_items, max_items))

    all_rows: list[dict] = []
    by_category: dict[str, list[dict]] = {}
    for category in categories:
        cat_items = list(categorized_for_llm.get(category) or [])
        ranked = sorted(cat_items, key=lambda item: _item_priority(category, item), reverse=True)
        rows = [_ledger_row(category, item) for item in ranked[:per_category]]
        if rows:
            by_category[category] = rows
        all_rows.extend(_ledger_row(category, item) for item in ranked)

    # Include categories not listed in config, preserving data rather than losing it.
    for category, cat_items in categorized_for_llm.items():
        if category in by_category or category in categories:
            continue
        ranked = sorted(cat_items, key=lambda item: _item_priority(category, item), reverse=True)
        rows = [_ledger_row(category, item) for item in ranked[:per_category]]
        if rows:
            by_category[category] = rows
        all_rows.extend(_ledger_row(category, item) for item in ranked)

    deduped: list[dict] = []
    seen: set[str] = set()
    for row in sorted(
        all_rows,
        key=lambda r: (_safe_float(r.get("score")), _safe_float(r.get("source_weight")), str(r.get("title"))),
        reverse=True,
    ):
        key = _ledger_key(row)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= max_items:
            break

    return {
        "must_cover": deduped[:required_items],
        "category_priorities": by_category,
        "all_priorities": deduped,
    }


def _format_coverage_ledger_for_prompt(ledger: dict) -> str:
    prompt_obj = {
        "must_cover": ledger.get("must_cover", []),
        "category_priorities": ledger.get("category_priorities", {}),
    }
    return json.dumps(prompt_obj, ensure_ascii=False, indent=2)


def _chunk_categorized_for_llm(
    categorized_for_llm: dict[str, list[dict]],
    *,
    max_chars: int = DEFAULT_CHUNK_CHAR_LIMIT,
) -> list[dict[str, list[dict]]]:
    """Split categorized payload into JSON-size bounded chunks."""
    chunks: list[dict[str, list[dict]]] = []
    current: dict[str, list[dict]] = {}

    def add_to(payload: dict[str, list[dict]], category: str, item: dict) -> dict[str, list[dict]]:
        clone = {k: list(v) for k, v in payload.items()}
        clone.setdefault(category, []).append(item)
        return clone

    for category, items in categorized_for_llm.items():
        for item in items:
            candidate = add_to(current, category, item)
            if current and len(_compact_json(candidate)) > max_chars:
                chunks.append(current)
                current = {category: [item]}
            else:
                current = candidate

    if current:
        chunks.append(current)
    return chunks


def _item_markdown_link(row: dict) -> str:
    title = _display_title(str(row.get("title") or "Untitled"))
    url = str(row.get("url") or "").strip()
    return f"[{title}](<{url}>)" if url else title


def _deterministic_chunk_summary(chunk: dict[str, list[dict]], chunk_index: int, chunk_count: int) -> str:
    """Non-LLM per-chunk fallback so a failed chunk never disappears."""
    rows: list[dict] = []
    for category, items in chunk.items():
        for item in items:
            rows.append(_ledger_row(category, item))
    rows.sort(key=lambda r: (_safe_float(r.get("score")), _safe_float(r.get("source_weight"))), reverse=True)

    lines = [f"<!-- chunk {chunk_index}/{chunk_count} deterministic -->", "### 重要候选"]
    if rows:
        for row in rows[:12]:
            reason = str(row.get("reason") or "").strip()
            meta = " / ".join(filter(None, [str(row.get("category") or ""), str(row.get("source") or "")]))
            suffix = f"：{reason}" if reason else ""
            lines.append(f"- {_item_markdown_link(row)}｜{meta}{suffix}")
    else:
        lines.append("- 本分块暂无可摘要条目。")

    lines += ["", "### 主题信号"]
    category_counts = {category: len(items) for category, items in chunk.items() if items}
    for category, count in sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]:
        lines.append(f"- {category}：本分块 {count} 条，优先查看该分类高分条目。")
    if not category_counts:
        lines.append("- 暂无明显主题信号。")

    lines += ["", "### 分类补充"]
    for category, items in chunk.items():
        ranked = sorted(items, key=lambda item: _item_priority(category, item), reverse=True)[:3]
        if not ranked:
            continue
        lines.append(f"#### {category}")
        for item in ranked:
            row = _ledger_row(category, item)
            reason = str(row.get("reason") or "").strip()
            lines.append(f"- {_item_markdown_link(row)}" + (f"：{reason}" if reason else ""))
    return "\n".join(lines).strip()


async def _compact_chunk_summaries_incrementally(
    chunk_summaries: list[str],
    config: dict,
    *,
    usage_collector: list[dict],
) -> list[str]:
    """Compact oversized chunk-summary sets in bounded batches.

    This is the "compact" layer: when many chunks would make the final prompt too
    large, summarize summaries in batches, preserving links and candidates. If a
    compaction call fails, keep the original batch text rather than dropping it.
    """
    final_limit = _summary_int(config, "final_synthesis_char_limit", DEFAULT_FINAL_SYNTHESIS_CHAR_LIMIT)
    batch_limit = _summary_int(config, "compact_batch_char_limit", DEFAULT_COMPACT_BATCH_CHAR_LIMIT)
    current = [s for s in chunk_summaries if s.strip()]
    round_index = 0

    while len("\n\n---\n\n".join(current)) > final_limit and len(current) > 1 and round_index < 4:
        round_index += 1
        batches: list[list[str]] = []
        batch: list[str] = []
        for summary in current:
            candidate = batch + [summary]
            if batch and len("\n\n---\n\n".join(candidate)) > batch_limit:
                batches.append(batch)
                batch = [summary]
            else:
                batch = candidate
        if batch:
            batches.append(batch)

        logger.info(
            "Compacting chunk summaries: round=%d, summaries=%d, batches=%d",
            round_index, len(current), len(batches),
        )
        compacted: list[str] = []
        for idx, batch in enumerate(batches, start=1):
            batch_text = "\n\n---\n\n".join(batch)
            prompt = COMPACT_SUMMARY_PROMPT.format(chunk_summaries=batch_text)
            text, used_fallback = await _call_llm(
                prompt,
                config,
                None,
                usage_collector=usage_collector,
                fallback_to_raw=False,
                validator=_validate_chunk_digest,
            )
            if used_fallback or not text.strip() or is_truncated_report(text):
                logger.warning(
                    "Chunk-summary compact round %d batch %d failed; keeping original batch",
                    round_index, idx,
                )
                compacted.append(batch_text)
            else:
                compacted.append(f"<!-- compact round {round_index} batch {idx}/{len(batches)} -->\n{text.strip()}")
        old_len = len("\n\n---\n\n".join(current))
        new_len = len("\n\n---\n\n".join(compacted))
        if new_len >= old_len:
            # No progress; avoid an infinite loop.
            break
        current = compacted
    return current


def _markdown_contains_ledger_row(markdown: str, row: dict) -> bool:
    url = str(row.get("url") or "").strip()
    if url and url in markdown:
        return True
    title = _display_title(str(row.get("title") or "")).strip()
    if title and title in markdown:
        return True
    # Last-resort fuzzy title check for very long titles that the model may trim.
    compact_title = "".join(title.split())
    compact_markdown = "".join(markdown.split())
    return bool(compact_title and len(compact_title) >= 12 and compact_title[:24] in compact_markdown)


def _ensure_ledger_coverage(markdown: str, ledger: dict, config: dict) -> str:
    """Append deterministic coverage repairs for important ledger/category items the LLM missed."""
    required_default = len(ledger.get("must_cover") or [])
    required_count = _summary_int(config, "coverage_required_items", required_default)
    required_rows = list(ledger.get("must_cover") or [])[:required_count]

    missing: list[dict] = []
    seen_keys: set[str] = set()

    def add_missing(row: dict) -> None:
        key = _ledger_key(row)
        if key in seen_keys:
            return
        seen_keys.add(key)
        if not _markdown_contains_ledger_row(markdown, row):
            missing.append(row)

    for row in required_rows:
        add_missing(row)

    # Category-balanced safety net: even when global top scores are dominated by
    # one noisy source/category, keep at least N representative items from every
    # non-empty category visible in the final report.
    per_category_required = _summary_int(config, "coverage_per_category_required", 1)
    if per_category_required > 0:
        for category, rows in (ledger.get("category_priorities") or {}).items():
            cat_rows = list(rows or [])[:per_category_required]
            if not cat_rows:
                continue
            if any(_markdown_contains_ledger_row(markdown, row) for row in rows or []):
                continue
            for row in cat_rows:
                add_missing(row)

    if not missing:
        return markdown

    logger.warning("Final digest missed %d required ledger/category item(s); appending reader-facing supplement", len(missing))
    lines = [
        "",
        "## 3. 仍值得保留的补充信号",
        "",
        "> 下面是未进入前两部分、但按来源质量和事件分数仍值得保留的少量信号，供后续延伸阅读。",
        "",
    ]
    for row in missing:
        meta = " / ".join(filter(None, [str(row.get("category") or ""), str(row.get("source") or "")]))
        reason = str(row.get("reason") or row.get("ranking_summary") or "").strip()
        if len(reason) > 180:
            reason = reason[:177].rstrip() + "…"
        suffix = f"：{reason}" if reason else ""
        lines.append(f"- {_item_markdown_link(row)}｜{meta}{suffix}")
    return markdown.rstrip() + "\n" + "\n".join(lines).rstrip() + "\n"


async def _call_llm_for_digest(
    prompt: str,
    config: dict,
    categorized_for_llm: dict[str, list[dict]],
    *,
    top_n: int,
    categories: list[str],
    usage_collector: list[dict],
    event_ledger: dict | None = None,
) -> tuple[str, bool]:
    """Direct summarize for normal inputs; chunk+compact+coverage for large inputs."""
    limit = _summary_int(config, "direct_prompt_char_limit", DEFAULT_DIRECT_PROMPT_CHAR_LIMIT)
    if len(prompt) <= limit:
        direct, used_fallback = await _call_llm(
            prompt,
            config,
            categorized_for_llm,
            usage_collector=usage_collector,
            validator=lambda text: _validate_digest_body(text, top_n=top_n),
        )
        if not used_fallback and not is_truncated_report(direct):
            ledger = _build_importance_ledger(categorized_for_llm, config, top_n=top_n, categories=categories)
            return _ensure_ledger_coverage(direct, ledger, config), False
        logger.warning(
            "Direct digest result was fallback/truncated; retrying with chunked incremental summary before raw fallback"
        )

    else:
        logger.warning(
            "Digest prompt is large (%d chars > %d); using chunked incremental summary",
            len(prompt), limit,
        )

    chunked = await _call_llm_chunked_digest(
        categorized_for_llm,
        config,
        top_n=top_n,
        categories=categories,
        usage_collector=usage_collector,
    )
    if chunked:
        return chunked, False

    logger.warning("Chunked digest unavailable; using deterministic digest fallback instead of raw item dump")
    if event_ledger:
        return render_digest_from_event_ledger(event_ledger, config=config, top_n=top_n), False
    ledger = _build_importance_ledger(categorized_for_llm, config, top_n=top_n, categories=categories)
    return _generate_deterministic_digest_fallback(categorized_for_llm, ledger, config, top_n=top_n), False


async def _call_llm_chunked_digest(
    categorized_for_llm: dict[str, list[dict]],
    config: dict,
    *,
    top_n: int,
    categories: list[str],
    usage_collector: list[dict],
) -> str | None:
    chunk_limit = _summary_int(config, "chunk_char_limit", DEFAULT_CHUNK_CHAR_LIMIT)
    chunks = _chunk_categorized_for_llm(categorized_for_llm, max_chars=chunk_limit)
    if len(chunks) <= 1:
        return None

    ledger = _build_importance_ledger(categorized_for_llm, config, top_n=top_n, categories=categories)
    logger.info("Chunked digest: %d chunks, chunk_char_limit=%d", len(chunks), chunk_limit)
    chunk_summaries: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        prompt = CHUNK_SUMMARY_PROMPT.format(
            chunk_index=idx,
            chunk_count=len(chunks),
            chunk_json=json.dumps(chunk, ensure_ascii=False, indent=2),
        )
        summary, used_fallback = await _call_llm(
            prompt,
            config,
            chunk,
            usage_collector=usage_collector,
            fallback_to_raw=False,
            validator=_validate_chunk_digest,
        )
        if used_fallback or not summary.strip() or is_truncated_report(summary):
            logger.warning(
                "Chunk %d/%d summary failed or truncated; using deterministic chunk fallback",
                idx, len(chunks),
            )
            summary = _deterministic_chunk_summary(chunk, idx, len(chunks))
        else:
            summary = f"<!-- chunk {idx}/{len(chunks)} -->\n" + summary.strip()
        chunk_summaries.append(summary)

    compacted_summaries = await _compact_chunk_summaries_incrementally(
        chunk_summaries,
        config,
        usage_collector=usage_collector,
    )
    final_prompt = CHUNK_FINAL_PROMPT.format(
        language_style_rules=language_style_section(),
        top_n=top_n,
        categories=", ".join(categories),
        coverage_ledger=_format_coverage_ledger_for_prompt(ledger),
        chunk_summaries="\n\n---\n\n".join(compacted_summaries),
    )
    final, used_fallback = await _call_llm(
        final_prompt,
        config,
        None,
        usage_collector=usage_collector,
        fallback_to_raw=False,
        validator=lambda text: _validate_digest_body(text, top_n=top_n),
    )
    if used_fallback or not final.strip():
        logger.warning("Final chunk synthesis failed; using deterministic chunk-summary fallback")
        return _generate_chunk_summary_fallback(chunk_summaries, ledger, config)
    if is_truncated_report(final):
        logger.warning("Final chunk synthesis looks truncated; using deterministic chunk-summary fallback")
        return _generate_chunk_summary_fallback(chunk_summaries, ledger, config)
    return _ensure_ledger_coverage(final, ledger, config)


def _generate_deterministic_digest_fallback(
    categorized_for_llm: dict[str, list[dict]],
    ledger: dict | None,
    config: dict | None = None,
    *,
    top_n: int = 10,
) -> str:
    """Readable non-LLM fallback for Daily Digest.

    This replaces the old user-facing raw dump for the report pipeline: if every
    model/fallback model produces unusable output, we still emit a concise,
    category-balanced digest scaffold rather than hundreds of raw items.
    """
    cfg = config or {}
    ledger = ledger or _build_importance_ledger(
        categorized_for_llm, cfg, top_n=top_n, categories=list(categorized_for_llm.keys())
    )
    priorities = list(ledger.get("all_priorities") or ledger.get("must_cover") or [])
    top_rows = priorities[:max(1, int(top_n or 10))]
    top_keys = {_ledger_key(row) for row in top_rows}

    lines = [
        "## 1. Today's Top Headlines",
        "",
        "> 自动摘要未通过质量校验，以下按完整输入中的高优先级事件生成一版保守摘要；不会发送原始长列表。"
        "",
    ]
    if top_rows:
        for idx, row in enumerate(top_rows, start=1):
            reason = str(row.get("reason") or row.get("ranking_summary") or "").strip()
            suffix = f"：{reason}" if reason else ""
            lines.append(f"{idx}. **{_item_markdown_link(row)}**｜{row.get('category', '')} / {row.get('source', '')}{suffix}")
    else:
        lines.append("- 暂无可用条目。")

    lines += ["", "## 2. 主题与分类精选", ""]
    for category, rows in (ledger.get("category_priorities") or {}).items():
        if not rows:
            continue
        rows_to_show = [row for row in rows if _ledger_key(row) not in top_keys][:3]
        if not rows_to_show:
            continue
        lines.append(f"### {category}")
        for row in rows_to_show:
            reason = str(row.get("reason") or row.get("ranking_summary") or "").strip()
            suffix = f"：{reason}" if reason else ""
            lines.append(f"- {_item_markdown_link(row)}｜{row.get('source', '')}{suffix}")
        lines.append("")

    report = "\n".join(lines).rstrip() + "\n"
    return _ensure_ledger_coverage(report, ledger, cfg)


def render_digest_from_event_ledger(
    ledger: dict,
    config: dict | None = None,
    *,
    top_n: int | None = None,
) -> str:
    """Render a complete deterministic AI Daily body from an event ledger."""
    cfg = config or {}
    events = [event for event in (ledger.get("events") or []) if isinstance(event, dict)]
    selected = [event for event in events if event.get("selected")]
    if selected:
        selected.sort(key=lambda event: int(event.get("selection_rank") or event.get("rank") or 10**9))
    if not selected:
        limit = int(top_n or (cfg.get("summary") or {}).get("top_headlines", 10) or 10)
        selected = events[:limit]

    def event_key(event: dict) -> str:
        main = event.get("main_item") if isinstance(event.get("main_item"), dict) else {}
        return _ledger_key({
            "event_id": event.get("event_id") or "",
            "url": main.get("url") or "",
            "title": main.get("title") or event.get("title") or "",
            "source": main.get("source") or event.get("source") or "",
        })

    def link_for(event: dict) -> str:
        main = event.get("main_item") if isinstance(event.get("main_item"), dict) else {}
        title = _display_title(str(main.get("title") or event.get("title") or "Untitled"))
        url = str(main.get("url") or "").strip()
        if url:
            return f"[{title}](<{url}>)"
        return title

    selected_keys = {event_key(event) for event in selected}

    lines = [
        "## 1. Today's Top Headlines",
        "",
        "> LLM 摘要不可用，以下根据 event ledger 的确定性排序生成；保留主来源与旁证，不展开原始链接列表。",
        "",
    ]
    if selected:
        for event in selected:
            main = event.get("main_item") if isinstance(event.get("main_item"), dict) else {}
            rank = int(event.get("selection_rank") or event.get("rank") or len(lines))
            source = str(main.get("source") or "")
            score = float(event.get("final_score") or event.get("event_score") or 0.0)
            tier = str(main.get("source_tier") or "")
            meta = " / ".join(part for part in (source, tier, f"score {score:.1f}") if part)
            lines.append(f"{rank}. **{link_for(event)}**｜{meta}")
            supporting = event.get("supporting_sources") or []
            if supporting:
                labels = []
                for src in supporting[:3]:
                    if not isinstance(src, dict):
                        continue
                    src_title = _display_title(str(src.get("title") or src.get("source") or "source"))
                    src_url = str(src.get("url") or "").strip()
                    labels.append(f"[{src_title}](<{src_url}>)" if src_url else src_title)
                if labels:
                    lines.append(f"   - 旁证：{' / '.join(labels)}")
    else:
        lines.append("- 暂无可用事件。")

    by_topic: dict[str, list[dict]] = {}
    for event in events:
        if event_key(event) in selected_keys:
            continue
        topic = str(event.get("topic") or event.get("category") or "Other")
        by_topic.setdefault(topic, []).append(event)

    lines += ["", "## 2. 主题与分类精选", ""]
    for topic, rows in by_topic.items():
        rows_to_show = rows[:3]
        if not rows_to_show:
            continue
        lines.append(f"### {topic}")
        for event in rows_to_show:
            main = event.get("main_item") if isinstance(event.get("main_item"), dict) else {}
            source = str(main.get("source") or "")
            lines.append(f"- {link_for(event)}｜{source}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _generate_chunk_summary_fallback(chunk_summaries: list[str], ledger: dict | None = None, config: dict | None = None) -> str:
    """Compact degraded report that is still much better than raw item dumps."""
    cfg = config or {}
    ledger = ledger or {}
    body = "\n\n---\n\n".join(s.strip() for s in chunk_summaries if s.strip())
    max_chars = _summary_int(cfg, "chunk_fallback_max_chars", 30_000)
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n\n[chunk summary truncated]"
    report = (
        "## 1. Today's Top Headlines\n\n"
        "> 自动综合未通过质量校验，以下合并分块摘要中的高价值信号；已避免发送原始长列表。\n\n"
        "## 2. 主题与分类精选（分块降级版）\n\n"
        + body
        + "\n"
    )
    if ledger:
        report = _ensure_ledger_coverage(report, ledger, cfg)
    return report

async def generate_digest_report(
    items: list[ContentItem],
    config: dict,
    target_date: str,
) -> DigestReport:
    """Generate a full digest report using Opus LLM."""
    generated_at = datetime.now(timezone.utc)
    summary_config = config.get("summary", {})
    categories = summary_config.get("categories", [
        "AI Models & Agent", "Developer Tools", "Infrastructure & Systems",
        "Research Papers", "Finance & Markets", "Videos & Podcasts", "Books & Reading", "Social & Community",
    ])
    top_n = summary_config.get("top_headlines", 5)

    # Deduplicate across all sources, then remove items whose source links are
    # hard-dead before the LLM can cite them. This keeps final report links both
    # Markdown-formatted and openable.
    deduped = deduplicate(items)
    logger.info("After dedup: %d items (from %d)", len(deduped), len(items))
    deduped, link_health = await _filter_items_with_open_links(deduped, config)
    if link_health.get("checked"):
        logger.info(
            "Link validation: checked=%d kept=%d dropped=%d",
            link_health.get("checked", 0), link_health.get("kept", len(deduped)), link_health.get("dropped", 0),
        )

    # Group raw items for statistics, then build event clusters for the report
    # body. The LLM sees already-ranked event clusters rather than individual
    # links, so one announcement cannot occupy multiple Top Headlines slots.
    grouped = group_by_category(deduped, categories, config=config)
    event_clusters = cluster_content_items(deduped, config=config)
    selected_headline_clusters = select_top_event_clusters(event_clusters, top_n, config=config)
    selected_event_ids = {cluster.event_id for cluster in selected_headline_clusters}
    selection_ranks = {cluster.event_id: rank for rank, cluster in enumerate(selected_headline_clusters, start=1)}
    logger.info(
        "After event clustering: %d events (from %d deduped items); selected %d top headline events",
        len(event_clusters), len(deduped), len(selected_headline_clusters),
    )

    ledger_path = None
    event_ledger_config = config.get("event_ledger", {})
    ledger_enabled = event_ledger_config.get("enabled", bool(config.get("general")))
    event_ledger_payload = None
    if ledger_enabled:
        data_dir = Path(
            event_ledger_config.get("data_dir")
            or (config.get("general") or {}).get("data_dir")
            or (_PROJECT_ROOT / "data")
        ).expanduser()
        event_ledger_payload = build_event_ledger(
            date=target_date,
            generated_at=generated_at,
            event_clusters=event_clusters,
            raw_items=items,
            kept_items=deduped,
            categories=categories,
            top_n=top_n,
            selected_event_ids=selected_event_ids,
            selection_ranks=selection_ranks,
            routing_stats=config.get("_ai_daily_routing_stats"),
            link_health=link_health,
        )
        ledger_path = write_event_ledger(event_ledger_payload, data_dir=data_dir, generated_at=generated_at)
        logger.info("Event ledger saved to %s", ledger_path)

    # Prepare categorized event data for LLM.
    categorized_for_llm: dict[str, list[dict]] = {cat: [] for cat in categories}
    for cluster in event_clusters:
        cat = cluster.category if cluster.category in categorized_for_llm else "Social & Community"
        payload = event_cluster_payload(cluster)
        payload["selected_top_headline"] = cluster.event_id in selected_event_ids
        payload["selection_rank"] = selection_ranks.get(cluster.event_id)
        categorized_for_llm.setdefault(cat, []).append(payload)
    categorized_for_llm = {cat: rows for cat, rows in categorized_for_llm.items() if rows}

    prompt = DIGEST_PROMPT.format(
        categorized_json=json.dumps(categorized_for_llm, ensure_ascii=False, indent=2),
        language_style_rules=language_style_section(),
        top_n=top_n,
        categories=", ".join(categories),
    )

    # Call LLM with structured fallback data for when all models fail.
    # Keep a mutable usage collector so reports and Weixin notifications can
    # include token/cost information for every LLM call made during generation.
    usage_entries: list[dict] = []
    full_markdown, used_fallback = await _call_llm_for_digest(
        prompt,
        config,
        categorized_for_llm,
        top_n=top_n,
        categories=categories,
        usage_collector=usage_entries,
        event_ledger=event_ledger_payload,
    )
    usage_summary = usage_summary_with_openclaw(usage_entries, reason='未调用任务 LLM')

    # Build report header
    header = f"""# Daily Digest - {target_date}

> Generated at {datetime.now().strftime('%Y-%m-%d %H:%M')} | {len(deduped)} items from {len(set(i.source for i in deduped))} sources

---

"""

    # Render one front-matter statistics section in Chinese only. Keep English
    # "Platform Statistics" out of the final report to avoid duplicate headings.
    stats = {
        "total_items": len(deduped),
        "sources": {
            source: len([i for i in deduped if i.source == source])
            for source in set(i.source for i in deduped)
        },
        "categories": {
            cat: len(items) for cat, items in grouped.items() if items
        },
        "link_validation": link_health,
        "events_count": len(event_clusters),
        "llm_usage": usage_summary,
        "event_ledger_path": str(ledger_path) if ledger_path else "",
    }

    industry_events_section = _build_industry_events_section(config, target_date)
    full_markdown = _insert_industry_events_section(full_markdown, industry_events_section)

    platform_stats_section = _build_platform_statistics_section(stats)
    source_health_section = _build_source_health_section(config)

    full_report = header + platform_stats_section + source_health_section + full_markdown
    full_report = await _sanitize_report_links(full_report, config)

    # If the LLM returned structurally broken markdown, degrade gracefully to
    # the structured raw fallback instead of aborting the whole summarize step.
    if not used_fallback and is_truncated_report(full_report):
        logger.warning("LLM report looks truncated after assembly; switching to deterministic digest fallback")
        ledger = _build_importance_ledger(categorized_for_llm, config, top_n=top_n, categories=categories)
        full_markdown = _generate_deterministic_digest_fallback(categorized_for_llm, ledger, config, top_n=top_n)
        full_markdown = _insert_industry_events_section(full_markdown, industry_events_section)
        used_fallback = False
        platform_stats_section = _build_platform_statistics_section(stats)
        source_health_section = _build_source_health_section(config)
        full_report = header + platform_stats_section + source_health_section + full_markdown
        full_report = await _sanitize_report_links(full_report, config)

    full_report = append_usage_section(full_report, usage_summary)

    # Tag fallback/raw reports so orchestrator can distinguish them from broken truncation.
    if used_fallback:
        full_report = "<!-- RAW_FALLBACK_REPORT -->\n" + full_report

    return DigestReport(
        date=target_date,
        generated_at=generated_at,
        headline_summary="",  # Extracted by LLM
        category_sections={cat: "" for cat in categories},
        full_markdown=full_report,
        stats=stats,
        items_count=len(deduped),
        sources_count=len(stats["sources"]),
    )


def _build_platform_statistics_section(stats: dict) -> str:
    """Render the single report statistics section in Chinese."""
    sources = stats.get("sources") or {}
    categories = stats.get("categories") or {}

    def top_rows(mapping: dict, limit: int = 12) -> str:
        rows = sorted(mapping.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))[:limit]
        if not rows:
            return "无"
        return "、".join(f"`{name}` {count}" for name, count in rows)

    lines = [
        "## 平台统计",
        "",
        f"- 总计：{stats.get('total_items', 0)} 条，来自 {len(sources)} 个信息源。",
        f"- 信息源分布：{top_rows(sources)}。",
        f"- 分类分布：{top_rows(categories)}。",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)



def _build_source_health_section(config: dict) -> str:
    """Render source availability and automatic repair status."""
    health = config.get("_source_health") or {}
    results = health.get("results") or []
    if not results:
        return ""

    failed = [r for r in results if not r.get("success")]
    repaired = [r for r in results if r.get("repair_status") == "recovered"]
    lines = [
        "## 信息源可用性与自修复",
        "",
    ]
    if not failed and not repaired:
        lines.append("- 本次未发现不可用信息源；所有 enabled sources 至少完成采集流程。")
    else:
        if repaired:
            lines.append("- 已自动修复/重试成功的信息源：")
            for row in repaired:
                actions = "; ".join(row.get("repair_actions") or []) or "retry"
                lines.append(f"  - `{row.get('source')}`：{actions}")
        if failed:
            lines.append("- 仍不可用、需要后续处理的信息源：")
            for row in failed:
                actions = "; ".join(row.get("repair_actions") or []) or "已重试一次"
                notes = "; ".join(row.get("repair_notes") or [])
                error = str(row.get("error") or "unknown error").replace("\n", " ")[:220]
                suffix = f"；备注：{notes}" if notes else ""
                lines.append(f"  - `{row.get('source')}`：{error}；已尝试：{actions}{suffix}")
    lines.extend(["", "---", ""])
    return "\n".join(lines)


def _parse_iso_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _event_date_label(event: dict) -> str:
    label = str(event.get("date_label") or "").strip()
    if label:
        return label
    start = str(event.get("start_date") or event.get("date") or "").strip()
    end = str(event.get("end_date") or "").strip()
    if start and end and end != start:
        return f"{start}–{end}"
    if start:
        return start
    return "时间待公布"


def _event_markdown_link(event: dict) -> str:
    title = _clean_link_label(str(event.get("title") or "Untitled event").strip())
    url = str(event.get("url") or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return f"[{title}](<{url}>)"
    return title


def _event_focus_text(event: dict) -> str:
    focus = event.get("focus") or event.get("watch") or []
    if isinstance(focus, str):
        focus_items = [focus]
    else:
        focus_items = [str(x).strip() for x in focus if str(x).strip()]
    note = str(event.get("note") or "").strip()
    parts = []
    if focus_items:
        parts.append("关注点：" + "、".join(focus_items[:5]))
    if note:
        parts.append(note)
    return "；".join(parts)


def _format_industry_event_line(event: dict) -> str:
    date_label = _event_date_label(event)
    organizer = str(event.get("organizer") or "").strip()
    location = str(event.get("location") or "").strip()
    meta = " / ".join(x for x in [date_label, organizer, location] if x)
    focus = _event_focus_text(event)
    suffix = f" — {focus}" if focus else ""
    return f"- {meta}：{_event_markdown_link(event)}{suffix}" if meta else f"- {_event_markdown_link(event)}{suffix}"


def _build_industry_events_section(config: dict, target_date: str) -> str:
    """Build Lee's requested upcoming AI/industry-events watch section.

    This is deterministic rather than LLM-generated so scheduled reports keep a
    stable watchlist even when daily source collection does not surface a
    conference announcement. Recent missed events are also shown briefly so they
    can trigger a retrospective catch-up, e.g. Google Cloud Next 26.
    """
    cfg = config.get("industry_events") or (config.get("summary") or {}).get("industry_events") or {}
    if not cfg.get("enabled", False):
        return ""
    events = [e for e in (cfg.get("events") or []) if isinstance(e, dict) and e.get("enabled", True)]
    if not events:
        return ""

    today = _parse_iso_date(target_date) or datetime.now().date()
    horizon_days = int(cfg.get("horizon_days", 30) or 30)
    recent_days = int(cfg.get("recent_days", 30) or 30)
    imminent_days = int(cfg.get("imminent_days", 7) or 7)
    weekly_reminder_weekday = int(cfg.get("weekly_reminder_weekday", 0) or 0)
    is_weekly_reminder_day = today.weekday() == weekly_reminder_weekday
    horizon_end = today + timedelta(days=horizon_days)
    recent_start = today - timedelta(days=recent_days)

    upcoming: list[dict] = []
    recent: list[dict] = []
    tba: list[dict] = []
    for event in events:
        start = _parse_iso_date(event.get("start_date") or event.get("date"))
        end = _parse_iso_date(event.get("end_date")) or start
        always_show = bool(event.get("always_show", False))
        if start is None:
            # TBA / watchlist items are useful, but they should not repeat every
            # day in the short Weixin digest. Show them on the weekly reminder
            # cadence unless an event explicitly opts into always_show.
            if is_weekly_reminder_day or always_show:
                tba.append(event)
            continue
        if end is not None and end < today:
            if end >= recent_start and (is_weekly_reminder_day or always_show):
                recent.append(event)
            continue
        if start <= horizon_end:
            days_until = (start - today).days
            if days_until <= imminent_days or is_weekly_reminder_day or always_show:
                upcoming.append(event)

    def sort_key(event: dict) -> tuple[date, int, str]:
        d = _parse_iso_date(event.get("start_date") or event.get("date")) or date.max
        priority = int(event.get("priority", 50) or 50)
        return d, priority, str(event.get("title") or "")

    upcoming.sort(key=sort_key)
    recent.sort(key=sort_key, reverse=True)
    tba.sort(key=lambda e: (int(e.get("priority", 50) or 50), str(e.get("title") or "")))

    max_upcoming = int(cfg.get("max_upcoming", 8) or 8)
    max_recent = int(cfg.get("max_recent", 4) or 4)
    max_tba = int(cfg.get("max_tba", 5) or 5)

    lines = [
        "## 2. 最近要发生的重要产业事件",
        "",
    ]
    if upcoming:
        lines += ["### 未来窗口", ""]
        lines.extend(_format_industry_event_line(e) for e in upcoming[:max_upcoming])
        lines.append("")
    if recent:
        lines += ["### 近期已发生 / 需要补课", ""]
        lines.extend(_format_industry_event_line(e) for e in recent[:max_recent])
        lines.append("")
    if tba:
        lines += ["### 时间待公布 / 持续关注", ""]
        lines.extend(_format_industry_event_line(e) for e in tba[:max_tba])
        lines.append("")
    if not (upcoming or recent or tba):
        lines.append("- 暂无进入当前窗口的重点产业事件。")
        lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _insert_industry_events_section(markdown: str, section: str) -> str:
    if not section.strip():
        return markdown
    text = (markdown or "").strip() + "\n"
    if "最近要发生的重要产业事件" in text:
        return text

    theme_re = re.compile(r"^##\s*(?:(?:2|3)[\.、]\s*)?(主题与分类精选.*)$", re.MULTILINE)
    match = theme_re.search(text)
    if not match:
        return text.rstrip() + "\n\n" + section

    theme_title = match.group(1).strip()
    replacement = f"## 3. {theme_title}"
    return text[:match.start()] + section + replacement + text[match.end():]


def _link_validation_config(config: dict) -> dict:
    """Return link-validation settings.

    Validation is opt-in via config so unit tests and ad-hoc library use do not
    unexpectedly perform network I/O. The production config enables it.
    """
    summary = config.get("summary") or {}
    cfg = summary.get("link_validation") or config.get("link_validation") or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "timeout_seconds": float(cfg.get("timeout_seconds", 6.0)),
        "concurrency": int(cfg.get("concurrency", 20)),
    }


def _is_openable_status(status_code: int) -> bool:
    """Classify URL health from HTTP status.

    2xx/3xx are open. 401/403/429 often mean auth, anti-bot, or rate limiting
    rather than a dead URL, so we keep them. 404/410/5xx are not reportable.
    """
    return 200 <= status_code < 400 or status_code in {401, 403, 429}


async def _validate_urls(urls: list[str], config: dict) -> dict[str, bool]:
    """Validate HTTP(S) URLs and cache results for this process."""
    import httpx
    from urllib.parse import urlparse

    cfg = _link_validation_config(config)
    if not cfg["enabled"]:
        return {u: True for u in urls}

    unique = []
    for url in urls:
        url = (url or "").strip()
        if not url or url in unique:
            continue
        unique.append(url)

    result: dict[str, bool] = {}
    pending: list[str] = []
    for url in unique:
        if url in _URL_OPEN_CACHE:
            result[url] = _URL_OPEN_CACHE[url]
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            _URL_OPEN_CACHE[url] = False
            result[url] = False
            continue
        pending.append(url)

    if not pending:
        return result

    timeout = httpx.Timeout(cfg["timeout_seconds"], connect=min(cfg["timeout_seconds"], 5.0))
    limits = httpx.Limits(max_connections=max(1, cfg["concurrency"]), max_keepalive_connections=max(1, cfg["concurrency"] // 2))
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36 OpenClaw-DailyDigest/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    sem = asyncio.Semaphore(max(1, cfg["concurrency"]))

    async def check_one(client: httpx.AsyncClient, url: str) -> tuple[str, bool]:
        async with sem:
            try:
                resp = await client.head(url, follow_redirects=True)
                if resp.status_code not in {405, 501}:
                    return url, _is_openable_status(resp.status_code)
            except Exception:
                pass
            try:
                resp = await client.get(url, follow_redirects=True, headers={"Range": "bytes=0-0"})
                return url, _is_openable_status(resp.status_code)
            except Exception as exc:
                logger.debug("URL validation failed for %s: %s", url, exc)
                return url, False

    async with httpx.AsyncClient(timeout=timeout, limits=limits, headers=headers) as client:
        for url, ok in await asyncio.gather(*(check_one(client, u) for u in pending)):
            _URL_OPEN_CACHE[url] = ok
            result[url] = ok
    return result


async def _filter_items_with_open_links(items: list[ContentItem], config: dict) -> tuple[list[ContentItem], dict[str, int]]:
    """Drop items with hard-dead source URLs when link validation is enabled."""
    cfg = _link_validation_config(config)
    urls = [i.url.strip() for i in items if (i.url or "").strip()]
    if not cfg["enabled"]:
        return items, {"enabled": 0, "checked": 0, "kept": len(items), "dropped": 0}

    status = await _validate_urls(urls, config)
    kept: list[ContentItem] = []
    dropped = 0
    for item in items:
        url = (item.url or "").strip()
        if url and not status.get(url, False):
            dropped += 1
            logger.info("Dropping item with non-openable URL: source=%s title=%r url=%s", item.source, item.title[:120], url)
            continue
        kept.append(item)
    return kept, {"enabled": 1, "checked": len(status), "kept": len(kept), "dropped": dropped}


def _clean_link_label(label: str) -> str:
    """Keep Markdown link labels readable without nested square brackets."""
    return (label or "link").replace("[", "").replace("]", "").strip() or "link"


def _markdown_link_pattern():
    import re
    # Allows one level of balanced square brackets inside the label, e.g.
    # `[[Podcast] Episode](<url>)`, so we can normalize it safely.
    return re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])+)\]\((?:<([^>]+)>|([^\)]+))\)")


def _normalize_markdown_links(markdown: str) -> str:
    """Normalize raw/loose links to `[label](<url>)` markdown form."""
    import re

    link_re = _markdown_link_pattern()

    def repl(match):
        label = _clean_link_label(match.group(1).strip())
        url = (match.group(2) or match.group(3) or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return f"[{label}](<{url}>)"
        return match.group(0)

    markdown = link_re.sub(repl, markdown)

    # Convert remaining bare HTTP(S) URLs that are not already markdown targets.
    bare_re = re.compile(r"(?<![<\(])(?<!\]\()https?://[^\s<>()]+")

    def bare_repl(match):
        url = match.group(0).rstrip(".,;:!?")
        suffix = match.group(0)[len(url):]
        return f"[link](<{url}>){suffix}"

    return bare_re.sub(bare_repl, markdown)


def _enforce_report_structure(markdown: str) -> str:
    """Remove deprecated sections and fold old split headings into one section."""
    import re

    forbidden = ("重要性与交叉验证", "高权重主题/分类", "重要候选信息", "Platform Statistics")
    heading_re = re.compile(r"^(#{2,6})\s*(.*)$")
    kept: list[str] = []
    skip_level: int | None = None
    for line in markdown.splitlines():
        match = heading_re.match(line.strip())
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and any(token in title for token in forbidden):
                skip_level = level
                continue
        if skip_level is not None:
            continue
        kept.append(line)
    markdown = "\n".join(kept).strip() + "\n"

    # If an older prompt/model still emits the former split headings, keep the
    # content but fold it under the new combined section shape.
    markdown = re.sub(
        r"^##\s*(?:\d+[\.、]\s*)?跨平台主题分析\s*$",
        "## 2. 主题与分类精选",
        markdown,
        flags=re.MULTILINE,
    )
    markdown = re.sub(
        r"^##\s*(?:\d+[\.、]\s*)?分类详情\s*$",
        "### 分类补充",
        markdown,
        flags=re.MULTILINE,
    )
    markdown = re.sub(
        r"^###\s*(?:\d+[\.、]\s*)?分类详情\s*$",
        "### 分类补充",
        markdown,
        flags=re.MULTILINE,
    )
    return markdown


def _dedupe_platform_statistics_sections(markdown: str) -> str:
    """Keep only the first Chinese platform statistics section."""
    import re

    heading_re = re.compile(r"^(#{2,6})\s*(.*)$")
    kept: list[str] = []
    seen = False
    skip_level: int | None = None
    for line in markdown.splitlines():
        match = heading_re.match(line.strip())
        if match:
            level = len(match.group(1))
            title = re.sub(r"^\d+[\.、]\s*", "", match.group(2).strip())
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and title == "平台统计":
                if seen:
                    skip_level = level
                    continue
                seen = True
        if skip_level is not None:
            continue
        kept.append(line)
    return "\n".join(kept).strip() + "\n"


async def _sanitize_report_links(markdown: str, config: dict) -> str:
    """Ensure emitted report hyperlinks are Markdown-formatted and not hard-dead."""
    markdown = _dedupe_platform_statistics_sections(_normalize_markdown_links(_enforce_report_structure(markdown)))
    link_re = _markdown_link_pattern()
    matches = list(link_re.finditer(markdown))
    urls = []
    for match in matches:
        url = (match.group(2) or match.group(3) or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            urls.append(url)
    if not urls:
        return markdown

    status = await _validate_urls(urls, config)

    def repl(match):
        label = _clean_link_label(match.group(1).strip())
        url = (match.group(2) or match.group(3) or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return match.group(0)
        if status.get(url, False):
            return f"[{label}](<{url}>)"
        logger.info("Removing non-openable report hyperlink: %s", url)
        return label

    return link_re.sub(repl, markdown)


def _display_title(title: str) -> str:
    """Keep titles readable and prevent raw URLs from leaking into link labels."""
    import re

    cleaned = re.sub(r"https?://\S+", "", title or "").strip()
    cleaned = _clean_link_label(cleaned)
    return cleaned or _clean_link_label(title) or "Untitled"


def _build_llm_item_payload(item: ContentItem) -> dict:
    payload = {
        "title": _display_title(item.title),
        "source": item.source,
        "url": item.url,
        "content": item.content[:300],
        "score": item.score,
        "source_weight": source_weight(item.source, item.url),
        "published_at": item.published_at.isoformat(),
        "tags": item.tags,
    }
    if item.author:
        payload["author"] = item.author

    ranking = _extract_ranking_payload(item)
    if ranking:
        payload["ranking"] = ranking

    return payload


def _format_metric_value(value) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value >= 100:
            return f"{value:,.0f}"
        if value >= 10:
            return f"{value:,.1f}"
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return str(value)


_SIGNAL_LABELS = {
    "reading_count": "在读人数",
    "rating_count": "评价人数",
    "ratings_count": "评分人数",
    "rating": "评分",
    "star_rating": "星级",
    "rank_bonus": "榜单名次加分",
    "rating_bonus": "评分加分",
    "finished": "完读率修正",
    "rating_percent": "评分率修正",
    "recommend_percent": "推荐率修正",
    "free_ratio": "免费占比修正",
    "ai_keypoint_coverage": "AI 要点覆盖修正",
}


def _build_ranking_summary(
    *,
    base_signal: str | None,
    base_score,
    multiplier,
    factors: dict,
) -> str | None:
    bits: list[str] = []

    if base_signal and base_score not in (None, ""):
        label = _SIGNAL_LABELS.get(base_signal, base_signal)
        bits.append(f"基础热度主要来自{label} {_format_metric_value(base_score)}")

    additive_factors = []
    multiplicative_factors = []
    for name, value in factors.items():
        if not isinstance(value, (int, float)):
            continue
        label = _SIGNAL_LABELS.get(name, name)
        if "bonus" in name:
            additive_factors.append(f"{label} +{_format_metric_value(value)}")
        else:
            multiplicative_factors.append(f"{label} {value:+.3f}")

    if additive_factors:
        bits.append("另有" + "、".join(additive_factors))

    if isinstance(multiplier, (int, float)):
        if abs(multiplier - 1.0) >= 0.001:
            factor_text = "、".join(multiplicative_factors) if multiplicative_factors else "若干口碑/完成度因子"
            bits.append(f"并由 {factor_text} 带来约 {multiplier:.3f}x 的轻微修正")
        elif multiplicative_factors:
            bits.append("并参考 " + "、".join(multiplicative_factors))

    if not bits:
        return None
    return "；".join(bits) + "。"


def _extract_ranking_payload(item: ContentItem) -> dict:
    extra = item.extra or {}
    ranking = {}

    base_signal = extra.get("ranking_base_signal")
    base_score = extra.get("ranking_base_score")
    multiplier = extra.get("ranking_multiplier")
    factors = extra.get("ranking_factors") or {}

    if base_signal:
        ranking["base_signal"] = base_signal
    if base_score not in (None, ""):
        ranking["base_score"] = base_score
    if multiplier not in (None, ""):
        ranking["multiplier"] = multiplier
    if factors:
        ranking["factors"] = factors

    signals = {}
    for key in (
        "reading_count",
        "rating_count",
        "ratings_count",
        "rating",
        "star_rating",
        "rank",
        "rating_percent",
        "recommend_percent",
        "total_words",
        "finished",
        "category",
        "categories",
    ):
        value = extra.get(key)
        if value not in (None, "", [], {}):
            signals[key] = value
    if signals:
        ranking["signals"] = signals

    summary = _build_ranking_summary(
        base_signal=base_signal,
        base_score=base_score,
        multiplier=multiplier,
        factors=factors,
    )
    if summary:
        ranking["summary"] = summary

    return ranking


def _load_openclaw_azure_config() -> dict[str, str]:
    """Load Azure OpenAI config from OpenClaw models.json as fallback.

    Returns dict with keys: base_url, model (empty string if not found).
    """
    import json
    from pathlib import Path

    models_path = (
        Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"
    )
    try:
        data = json.loads(models_path.read_text(encoding="utf-8"))
        provider = data.get("providers", {}).get("azure-openai-responses", {})
        base_url = provider.get("baseUrl", "")
        models = provider.get("models", [])
        model_id = models[0]["id"] if models else ""
        return {"base_url": base_url, "model": model_id}
    except (FileNotFoundError, json.JSONDecodeError, KeyError, IndexError):
        return {"base_url": "", "model": ""}



# Model fallback chain: (model_name, max_attempts)
# If primary model fails all attempts, try next model before falling back to template.
MODELS: list[tuple[str, int]] = [
    ("llab-gpt-5.4", 3),
    ("llab-gpt-5-mini", 2),
]


def _summary_model_chain(config: dict) -> list[tuple[str, int]]:
    """Return model fallback chain, defaulting to GPT-5.4 -> GPT-5 mini.

    Config can override with summary.model_fallback_chain:
      - ["model-a", "model-b"]
      - [{"model": "model-a", "attempts": 3}, ...]
    Env DAILY_DIGEST_MODEL_FALLBACKS can also provide comma-separated models.
    """
    import os

    raw = (config.get("summary") or {}).get("model_fallback_chain")
    chain: list[tuple[str, int]] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str):
                model = entry.strip()
                attempts = 2
            elif isinstance(entry, dict):
                model = str(entry.get("model") or entry.get("name") or "").strip()
                try:
                    attempts = int(entry.get("attempts", 2))
                except Exception:
                    attempts = 2
            else:
                continue
            if model:
                chain.append((model, max(1, attempts)))
    elif isinstance(raw, str):
        chain = [(m.strip(), 2) for m in raw.split(",") if m.strip()]

    env_models = os.environ.get("DAILY_DIGEST_MODEL_FALLBACKS", "").strip()
    if env_models:
        chain = [(m.strip(), 2) for m in env_models.split(",") if m.strip()]

    return chain or list(MODELS)


# Progressive attempt configs: streaming → streaming (longer timeout) → non-streaming
# Non-streaming bypasses Azure proxy idle timeout during model TTFT thinking.
_ATTEMPT_CONFIGS = [
    {"stream": True,  "read_timeout": 600.0},   # Attempt 1: default streaming
    {"stream": True,  "read_timeout": 900.0},   # Attempt 2: longer timeout
    {"stream": False, "read_timeout": 900.0},   # Attempt 3+: non-streaming fallback
]

# LLM refusal detection patterns
_REFUSAL_PATTERNS = [
    "i'm sorry",
    "i cannot assist",
    "i can't assist",
    "i'm not able to",
    "i apologize, but",
    "as an ai",
    "i cannot help with",
    "i'm unable to",
]

FORBIDDEN_DIGEST_HEADINGS = ("重要性与交叉验证", "高权重主题/分类", "重要候选信息", "Platform Statistics")
Validator = Callable[[str], bool | tuple[bool, str]]


def _validator_result(result: bool | tuple[bool, str]) -> tuple[bool, str]:
    if isinstance(result, tuple):
        ok, reason = result
        return bool(ok), str(reason or "")
    return bool(result), ""


def _validate_digest_body(markdown: str, *, top_n: int = 10) -> tuple[bool, str]:
    """Validate user-facing LLM digest structure before accepting a model result.

    Model/API success is not enough: today's failure mode was a syntactically
    successful response that was structurally unsuitable for the report. Invalid
    output should fall through to the next model before deterministic repair.
    """
    text = (markdown or "").strip()
    if is_truncated_report(text):
        return False, "truncated_or_broken_markdown"
    if len(text) < 800:
        return False, "too_short_for_digest"
    for forbidden in FORBIDDEN_DIGEST_HEADINGS:
        if forbidden in text:
            return False, f"forbidden_section:{forbidden}"
    if "Today's Top" not in text:
        return False, "missing_top_headlines"
    if "主题与分类精选" not in text:
        return False, "missing_theme_section"

    top_section = text.split("Today's Top", 1)[1].split("## 2.", 1)[0]
    headline_lines = [
        line for line in top_section.splitlines()
        if re.match(r"^\s*(?:[-*]|\d+[.)])\s+", line)
    ]
    min_headlines = max(3, min(int(top_n or 10), 6))
    if len(headline_lines) < min_headlines:
        return False, f"too_few_top_headlines:{len(headline_lines)}"
    if "http" not in top_section:
        return False, "top_headlines_missing_links"
    return True, ""


def _validate_chunk_digest(markdown: str) -> tuple[bool, str]:
    text = (markdown or "").strip()
    if is_truncated_report(text):
        return False, "truncated_or_broken_markdown"
    if len(text) < 80:
        return False, "too_short"
    if "重要候选" not in text and "分类补充" not in text:
        return False, "missing_chunk_sections"
    return True, ""


def is_truncated_report(text: str) -> bool:
    """Heuristic guard for obviously cut-off reports.

    We only want to catch hard failures like:
    - unfinished emphasis/code markers
    - text ending mid-title/mid-sentence without closing sections
    - visibly broken markdown structure near EOF

    Keep this conservative: false negatives are better than blocking good reports.
    """
    if not text:
        return True

    stripped = text.rstrip()

    # Explicitly allow structured raw fallback reports.
    if "<!-- RAW_FALLBACK_REPORT -->" in stripped:
        return False

    # Unbalanced markdown fences / emphasis markers near EOF usually means truncation.
    if stripped.count("```") % 2 != 0:
        return True
    if stripped.count("**") % 2 != 0:
        return True

    tail = stripped[-300:]

    # Clearly broken endings we've observed in practice.
    # Treat a single trailing "*" as suspicious, but allow a properly closed
    # bold span to end the report (tail ending with "**"). Same idea for code
    # fences: a lone backtick is suspicious, balanced fences are handled above.
    broken_suffixes = (
        "[",
        "(",
        "{",
        "\"",
        "“",
        "—",
        ":",
    )
    if tail.endswith(broken_suffixes):
        return True
    if tail.endswith("*") and not tail.endswith("**"):
        return True
    if tail.endswith("`") and not tail.endswith("```"):
        return True

    return False


def _is_refusal(text: str) -> bool:
    """Detect LLM refusal/safety responses.

    Real digest reports are thousands of chars; refusals are short boilerplate.
    """
    if len(text) > 500:
        return False
    text_lower = text.lower()
    return any(p in text_lower for p in _REFUSAL_PATTERNS)


async def _call_llm(
    prompt: str,
    config: dict,
    categorized_for_llm: dict[str, list[dict]] | None = None,
    usage_collector: list[dict] | None = None,
    fallback_to_raw: bool = True,
    validator: Validator | None = None,
) -> tuple[str, bool]:
    """Call LLM for summary generation with model fallback chain.

    Tries each model in MODELS list. If all attempts for a model fail,
    tries the next model. Falls back to structured raw-data report if all models fail.

    Falls back to OpenClaw models.json config if env vars are missing.
    """
    import os

    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    base_url = (os.environ.get("AZURE_OPENAI_BASE_URL")
                or os.environ.get("AZURE_OPENAI_ENDPOINT", ""))
    api_version = (os.environ.get("AZURE_OPENAI_API_VERSION") or "2025-03-01-preview")

    # Always load OpenClaw config; base_url env var takes priority
    openclaw_cfg = _load_openclaw_azure_config()
    if not base_url:
        base_url = openclaw_cfg.get("base_url", "")
        if base_url:
            logger.info("Using base_url from OpenClaw models.json")

    if not api_key or not base_url:
        logger.warning("Azure OpenAI not configured. Using fallback report.")
        return (_generate_fallback_report(categorized_for_llm) if fallback_to_raw else ""), True

    # Ensure base_url ends with /openai for Responses API
    if not base_url.endswith("/openai"):
        base_url = base_url.rstrip("/") + "/openai"

    url = f"{base_url}/responses?api-version={api_version}"

    for model, max_attempts in _summary_model_chain(config):
        result = await _call_llm_with_model(
            prompt, url, api_key, model, max_attempts, usage_collector=usage_collector,
        )
        if result is not None:
            if validator is not None:
                ok, reason = _validator_result(validator(result))
                if not ok:
                    logger.warning(
                        "LLM output rejected by validator (model=%s, reason=%s); trying next model",
                        model, reason or "invalid",
                    )
                    continue
            return result, False
        logger.warning("Model %s exhausted all %d attempts, trying next...", model, max_attempts)

    logger.error("All models failed or produced invalid output, using fallback report with raw data.")
    return (_generate_fallback_report(categorized_for_llm) if fallback_to_raw else ""), True


async def _call_llm_with_model(
    prompt: str,
    url: str,
    api_key: str,
    model: str,
    max_attempts: int,
    usage_collector: list[dict] | None = None,
) -> str | None:
    """Try a single model up to max_attempts times with progressive degradation.

    Attempt strategy (from _ATTEMPT_CONFIGS):
    1. Streaming with 600s read timeout
    2. Streaming with 900s read timeout
    3+. Non-streaming with 900s read timeout (bypasses proxy idle timeout)

    Returns the generated text on success, or None if all attempts fail.
    """
    import httpx

    headers = {
        "api-key": api_key,
        "Content-Type": "application/json",
    }
    # Bypass proxy — Azure OpenAI is directly reachable and proxies
    # introduce idle-timeout disconnects during long model thinking.
    transport = httpx.AsyncHTTPTransport()

    logger.info(
        "Calling LLM: model=%s, url=%s, prompt_chars=%d", model, url, len(prompt)
    )

    for attempt in range(max_attempts):
        cfg = _ATTEMPT_CONFIGS[min(attempt, len(_ATTEMPT_CONFIGS) - 1)]
        use_stream = cfg["stream"]
        timeout = httpx.Timeout(
            connect=30.0, read=cfg["read_timeout"], write=60.0, pool=30.0,
        )
        payload = {
            "model": model,
            "input": prompt,
            "max_output_tokens": 16000,
            "stream": use_stream,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
                if use_stream:
                    async with client.stream("POST", url, json=payload, headers=headers) as resp:
                        resp.raise_for_status()
                        result = await _parse_sse_stream(resp, usage_collector=usage_collector, model=model)
                else:
                    logger.info(
                        "Using non-streaming mode (model=%s, attempt %d/%d)",
                        model, attempt + 1, max_attempts,
                    )
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    # Handle both sync and async .json() (e.g. in tests)
                    if hasattr(data, "__await__"):
                        data = await data
                    record_usage(usage_collector, model=model, usage=extract_response_usage(data))
                    result = _extract_response_text(data)

            if result:
                if _is_refusal(result):
                    logger.warning(
                        "LLM refusal detected (model=%s, %d chars): %.100s",
                        model, len(result), result,
                    )
                    return None  # Trigger fallback to next model
                logger.info("LLM summary generated: model=%s, %d chars", model, len(result))
                return result
            else:
                logger.warning("LLM returned empty response (model=%s)", model)
                return None

        except httpx.HTTPStatusError as e:
            logger.error(
                "LLM API returned HTTP %d (model=%s): %s",
                e.response.status_code, model, e,
            )
            return None
        except httpx.TransportError as e:
            if attempt < max_attempts - 1:
                wait = 3 * (attempt + 1)
                logger.warning(
                    "LLM request failed (model=%s, attempt %d/%d, %s), retrying in %ds: %s",
                    model, attempt + 1, max_attempts, type(e).__name__, wait, e,
                )
                await asyncio.sleep(wait)
                continue
            logger.error(
                "LLM API call failed after %d attempts (model=%s, %s): %s",
                max_attempts, model, type(e).__name__, e,
            )
            return None

    return None


def _extract_response_text(data: dict) -> str:
    """Extract text content from Azure OpenAI Responses API non-streaming response."""
    output = data.get("output", [])
    text_parts = []
    for item in output:
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text_parts.append(content.get("text", ""))
    return "".join(text_parts)


async def _parse_sse_stream(
    resp: object,
    usage_collector: list[dict] | None = None,
    model: str | None = None,
) -> str:
    """Parse Server-Sent Events stream from Azure OpenAI Responses API.

    Collects ``response.output_text.delta`` events and joins them.
    If the connection drops mid-stream but we already have substantial
    content (>= 500 chars), return the partial result instead of raising.
    """
    import httpx

    text_parts: list[str] = []
    line_count = 0
    delta_count = 0
    try:
        async for line in resp.aiter_lines():  # type: ignore[union-attr]
            line_count += 1
            # Debug: log every raw SSE line (truncated to 500 chars)
            logger.debug("SSE line %d: %.500s", line_count, line)
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                event = json.loads(data_str)
                event_type = event.get("type", "")
                if event_type == "response.output_text.delta":
                    text_parts.append(event.get("delta", ""))
                    delta_count += 1
                # Capture token usage from the final Responses API event when present.
                elif event_type == "response.completed":
                    usage = extract_response_usage(event.get("response") or event)
                    record_usage(usage_collector, model=model or "unknown", usage=usage)
                # Log non-delta event types for debugging
                elif line_count <= 10:
                    logger.debug("SSE event: %s", event_type)
            except json.JSONDecodeError:
                if line_count <= 5:
                    logger.debug("SSE non-JSON line: %.200s", data_str)
                continue
    except httpx.TransportError as e:
        partial = "".join(text_parts)
        logger.info(
            "Stream interrupted: lines=%d, deltas=%d, chars=%d (%s)",
            line_count, delta_count, len(partial), type(e).__name__,
        )
        if len(partial) >= 500:
            logger.warning(
                "Using partial result (%d chars) despite stream disconnect",
                len(partial),
            )
            return partial
        # Too little content — re-raise so the caller retries
        raise
    return "".join(text_parts)


def _generate_fallback_report(
    categorized_for_llm: dict[str, list[dict]] | None = None,
) -> str:
    """Generate a structured report from raw data when LLM is unavailable."""
    if not categorized_for_llm:
        return "## Summary\n\n_LLM summary unavailable. No item data available._\n"

    parts = ["## Summary (Raw Data \u2014 LLM Unavailable)\n"]
    parts.append("> LLM \u6458\u8981\u751f\u6210\u5931\u8d25\uff0c\u4ee5\u4e0b\u4e3a\u6309\u5206\u7c7b\u6574\u7406\u7684\u539f\u59cb\u91c7\u96c6\u6570\u636e\u3002\n")

    for category, items in categorized_for_llm.items():
        if not items:
            continue
        parts.append(f"\n### {category} ({len(items)} items)\n")
        for item in items[:30]:
            title = _display_title(item.get("title", "Untitled"))
            url = item.get("url", "")
            source = item.get("source", "")
            author = item.get("author", "")
            content = item.get("content", "")[:150]
            ranking = item.get("ranking") or {}
            link = f"[{title}](<{url}>)" if url else title
            meta = " | ".join(filter(None, [author, source]))
            parts.append(f"- **{link}**{f' \u2014 {meta}' if meta else ''}")
            if content:
                parts.append(f"  > {content}...")
            if ranking:
                ranking_bits = []
                base_signal = ranking.get("base_signal")
                base_score = ranking.get("base_score")
                multiplier = ranking.get("multiplier")
                factors = ranking.get("factors") or {}
                summary = ranking.get("summary")
                if summary:
                    parts.append(f"  > Ranking Summary: {summary}")
                if base_signal and base_score not in (None, ""):
                    ranking_bits.append(f"base={base_signal}:{base_score}")
                if isinstance(multiplier, (int, float)):
                    ranking_bits.append(f"multiplier={multiplier:.3f}")
                if factors:
                    factor_text = ", ".join(
                        f"{name}+{value:.3f}" if isinstance(value, (int, float)) else f"{name}+{value}"
                        for name, value in factors.items()
                    )
                    ranking_bits.append(f"factors={factor_text}")
                if ranking_bits and not summary:
                    parts.append(f"  > Ranking: {' | '.join(ranking_bits)}")
            parts.append("")

    return "\n".join(parts)
