"""LLM-based digest report generation using Opus.

Summary prompt style references:
- any2summary/prompts/article_summary_prompt.txt
- any2summary/prompts/summary_prompt.txt
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from aggregator.dedup import deduplicate
from aggregator.merger import group_by_category
from schema import ContentItem, DigestReport

logger = logging.getLogger(__name__)

# Summary prompt inspired by any2summary style
DIGEST_PROMPT = """你是一个多源信息聚合摘要助手。你的任务是将来自多个平台（Twitter/X、GitHub、Reddit、YouTube、知乎、即刻、小宇宙、HuggingFace、papers.cool、Apple Podcast、微信读书）的内容条目，生成一份高质量的每日精选报告。

## 输入
以下是按分类整理的内容条目（JSON 格式）：

{categorized_json}

## 输出要求

### 1. Today's Top {top_n} Headlines
- 从全部条目中选出最重要/最有影响力的 {top_n} 条，每条用一句话概括
- 重要的、insightful、非共识的内容用 **加粗** 标识
- 每条附上原文链接

### 2. 跨平台主题分析
- 找出跨多个平台重复出现的主题或趋势
- 用 2-3 段话分析这些趋势的意义
- 专业词汇和人名不要翻译（如 agent、LLM、Sam）

### 3. 分类详情
按以下分类生成详细摘要，每个分类：
- 总结：不超过 5 句话，包含非共识的 insight
- 要点：层次化、结构化展现，每个要点是一个观点/结论/事实
- 每条内容保留原文链接和作者
- 如果是非中文内容，专业表达保留原文，口语化部分翻译成中文

分类列表：{categories}

### 4. 平台统计
- 各平台采集数量
- 总条目数

## 翻译规范
1. 专业词汇和人名不翻译，例如 `agent`、`LLM`、`Sam`，或后面加原始词如：费曼图（Feynman diagram）
2. 不要压缩、省略或遗漏任何关键信息
3. 将重要的、insightful 的内容用 markdown **加粗** 标识，特别重要的用 `高亮`

请输出完整的 Markdown 格式报告。
"""


async def generate_digest_report(
    items: list[ContentItem],
    config: dict,
    target_date: str,
) -> DigestReport:
    """Generate a full digest report using Opus LLM."""
    summary_config = config.get("summary", {})
    categories = summary_config.get("categories", [
        "AI Models & Research", "Developer Tools", "Research Papers",
        "Videos & Podcasts", "Books & Reading", "Social & Community",
    ])
    top_n = summary_config.get("top_headlines", 5)

    # Deduplicate across all sources
    deduped = deduplicate(items)
    logger.info("After dedup: %d items (from %d)", len(deduped), len(items))

    # Group by category
    grouped = group_by_category(deduped, categories)

    # Prepare categorized data for LLM
    categorized_for_llm = {}
    for cat, cat_items in grouped.items():
        if not cat_items:
            continue
        categorized_for_llm[cat] = [
            {
                "title": item.title,
                "author": item.author,
                "source": item.source,
                "url": item.url,
                "content": item.content[:500],  # Truncate for LLM context
                "score": item.score,
                "published_at": item.published_at.isoformat(),
                "tags": item.tags,
            }
            for item in cat_items
        ]

    prompt = DIGEST_PROMPT.format(
        categorized_json=json.dumps(categorized_for_llm, ensure_ascii=False, indent=2),
        top_n=top_n,
        categories=", ".join(categories),
    )

    # Call LLM (placeholder - will be implemented in Phase 6)
    full_markdown = await _call_llm(prompt, config)

    # Build report header
    header = f"""# Daily Digest - {target_date}

> Generated at {datetime.now().strftime('%Y-%m-%d %H:%M')} | {len(deduped)} items from {len(set(i.source for i in deduped))} sources

---

"""

    # Stats
    stats = {
        "total_items": len(deduped),
        "sources": {
            source: len([i for i in deduped if i.source == source])
            for source in set(i.source for i in deduped)
        },
        "categories": {
            cat: len(items) for cat, items in grouped.items() if items
        },
    }

    stats_section = "\n\n---\n\n## Platform Statistics\n\n"
    stats_section += "| Platform | Items |\n|----------|-------|\n"
    for source, count in sorted(stats["sources"].items()):
        stats_section += f"| {source} | {count} |\n"
    stats_section += f"| **Total** | **{stats['total_items']}** |\n"

    full_report = header + full_markdown + stats_section

    return DigestReport(
        date=target_date,
        headline_summary="",  # Extracted by LLM
        category_sections={cat: "" for cat in categories},
        full_markdown=full_report,
        stats=stats,
        items_count=len(deduped),
        sources_count=len(stats["sources"]),
    )


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


async def _call_llm(prompt: str, config: dict) -> str:
    """Call LLM for summary generation via Azure OpenAI Responses API (streaming).

    Uses streaming mode to keep the connection alive during long inference,
    preventing proxy/load-balancer idle-timeout disconnects.

    Falls back to OpenClaw models.json config if env vars are missing.
    Falls back to placeholder template if API is not configured.
    """
    import os
    import httpx

    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    base_url = (os.environ.get("AZURE_OPENAI_BASE_URL")
                or os.environ.get("AZURE_OPENAI_ENDPOINT", ""))
    api_version = (os.environ.get("AZURE_OPENAI_API_VERSION") or "2025-03-01-preview")

    # Always load OpenClaw config for model selection; base_url env var takes priority
    openclaw_cfg = _load_openclaw_azure_config()
    if not base_url:
        base_url = openclaw_cfg.get("base_url", "")
        if base_url:
            logger.info("Using base_url from OpenClaw models.json")

    if not api_key or not base_url:
        logger.warning("Azure OpenAI not configured. Using fallback template.")
        return _generate_fallback_report(prompt)

    # Ensure base_url ends with /openai for Responses API
    if not base_url.endswith("/openai"):
        base_url = base_url.rstrip("/") + "/openai"

    # Use Responses API endpoint
    url = f"{base_url}/responses?api-version={api_version}"

    # Hard-coded model — env vars proved unreliable (llab-gpt-5-pro mismatch)
    model = "llab-gpt-5.2-codex"

    payload = {
        "model": model,
        "input": prompt,
        "max_output_tokens": 16000,
        "stream": True,
    }
    headers = {
        "api-key": api_key,
        "Content-Type": "application/json",
    }
    # 120s read timeout: TTFT limit for large prompts on Codex models
    timeout = httpx.Timeout(connect=30.0, read=120.0, write=60.0, pool=30.0)

    logger.info(
        "Calling LLM: model=%s, url=%s, prompt_chars=%d", model, url, len(prompt)
    )

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=headers,
                ) as resp:
                    resp.raise_for_status()
                    result = await _parse_sse_stream(resp)

            if result:
                logger.info("LLM summary generated: %d chars", len(result))
                return result
            else:
                logger.warning("LLM returned empty response, using fallback")
                return _generate_fallback_report(prompt)

        except httpx.HTTPStatusError as e:
            logger.error(
                "LLM API returned HTTP %d: %s", e.response.status_code, e,
            )
            return _generate_fallback_report(prompt)
        except httpx.TransportError as e:
            if attempt < 2:
                wait = 3 * (attempt + 1)
                logger.warning(
                    "LLM request failed (attempt %d/3, %s), retrying in %ds: %s",
                    attempt + 1, type(e).__name__, wait, e,
                )
                await asyncio.sleep(wait)
                continue
            logger.error(
                "LLM API call failed after 3 attempts (%s): %s",
                type(e).__name__, e,
            )
            return _generate_fallback_report(prompt)

    # Should not reach here, but satisfy type checker
    return _generate_fallback_report(prompt)


async def _parse_sse_stream(resp: object) -> str:
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
                # Log first non-delta event type for debugging
                elif line_count <= 5:
                    logger.debug("SSE event: %s", event_type)
            except json.JSONDecodeError:
                if line_count <= 3:
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


def _generate_fallback_report(prompt: str) -> str:
    """Generate a basic report without LLM when API is unavailable."""
    return (
        "## Summary\n\n"
        "_LLM summary unavailable. Raw collected data saved in intermediate JSON._\n\n"
        f"_Prompt prepared: {len(prompt)} chars for LLM processing._\n"
    )
