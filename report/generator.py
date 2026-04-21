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
                "content": item.content[:300],  # Truncate for LLM context
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

    # Call LLM with structured fallback data for when all models fail
    full_markdown, used_fallback = await _call_llm(prompt, config, categorized_for_llm)

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

    stats_section = "## Platform Statistics\n\n"
    stats_section += "| Platform | Items |\n|----------|-------|\n"
    for source, count in sorted(stats["sources"].items()):
        stats_section += f"| {source} | {count} |\n"
    stats_section += f"| **Total** | **{stats['total_items']}** |\n"
    stats_section += "\n---\n\n"

    full_report = header + stats_section + full_markdown

    # Tag fallback/raw reports so orchestrator can distinguish them from broken truncation.
    if used_fallback:
        full_report = "<!-- RAW_FALLBACK_REPORT -->\n" + full_report

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



# Model fallback chain: (model_name, max_attempts)
# If primary model fails all attempts, try next model before falling back to template.
MODELS: list[tuple[str, int]] = [
    ("llab-gpt-5.2-codex", 3),
    ("llab-gpt-5-mini", 2),
]

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


def is_truncated_report(text: str) -> bool:
    """Heuristic guard for obviously cut-off reports.

    We only want to catch hard failures like:
    - unfinished emphasis/code markers
    - text ending mid-title/mid-sentence without closing sections
    - missing stats tail that every normal report appends

    Keep this conservative: false negatives are better than blocking good reports.
    """
    if not text:
        return True

    stripped = text.rstrip()

    # Explicitly allow structured raw fallback reports.
    if "<!-- RAW_FALLBACK_REPORT -->" in stripped:
        return False

    # All normal reports end with the platform statistics tail.
    if "## Platform Statistics" not in stripped:
        return True

    # Unbalanced markdown fences / emphasis markers near EOF usually means truncation.
    if stripped.count("```") % 2 != 0:
        return True
    if stripped.count("**") % 2 != 0:
        return True

    tail = stripped[-300:]

    # Clearly broken endings we've observed in practice.
    broken_suffixes = (
        "**",
        "*",
        "`",
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

    # If the file ends with an alnum fragment and never reaches stats, it's suspect.
    last_line = stripped.splitlines()[-1].strip()
    if last_line and "| **Total** |" not in stripped and last_line[-1].isalnum():
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
        return _generate_fallback_report(categorized_for_llm), True

    # Ensure base_url ends with /openai for Responses API
    if not base_url.endswith("/openai"):
        base_url = base_url.rstrip("/") + "/openai"

    url = f"{base_url}/responses?api-version={api_version}"

    for model, max_attempts in MODELS:
        result = await _call_llm_with_model(
            prompt, url, api_key, model, max_attempts,
        )
        if result is not None:
            return result, False
        logger.warning("Model %s exhausted all %d attempts, trying next...", model, max_attempts)

    logger.error("All models failed, using fallback report with raw data.")
    return _generate_fallback_report(categorized_for_llm), True


async def _call_llm_with_model(
    prompt: str,
    url: str,
    api_key: str,
    model: str,
    max_attempts: int,
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
                        result = await _parse_sse_stream(resp)
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
            title = item.get("title", "Untitled")
            url = item.get("url", "")
            source = item.get("source", "")
            author = item.get("author", "")
            content = item.get("content", "")[:150]
            link = f"[{title}]({url})" if url else title
            meta = " | ".join(filter(None, [author, source]))
            parts.append(f"- **{link}**{f' \u2014 {meta}' if meta else ''}")
            if content:
                parts.append(f"  > {content}...")
            parts.append("")

    return "\n".join(parts)
