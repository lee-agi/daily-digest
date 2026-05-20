"""LLM token usage and cost helpers for generated reports.

Costs are estimated from OpenClaw ``models.json`` pricing when available.
Report summaries can also merge OpenClaw's own session-token usage so Weixin
notifications do not only show tokens spent by task-local LLM API calls.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UsageEntry = dict[str, Any]
UsageCollector = list[UsageEntry]


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def extract_response_usage(data: dict[str, Any] | None) -> dict[str, int]:
    """Extract token usage from an Azure/OpenAI Responses API payload."""
    usage = (data or {}).get("usage") or {}
    input_tokens = _safe_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    output_tokens = _safe_int(usage.get("output_tokens") or usage.get("completion_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens") or (input_tokens + output_tokens))

    input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached_input_tokens = _safe_int(
        input_details.get("cached_tokens")
        or input_details.get("cache_read_tokens")
    )

    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def load_model_costs() -> dict[str, dict[str, float]]:
    """Load model cost config from OpenClaw models.json.

    Expected cost keys: input, output, cacheRead, cacheWrite. Values are treated
    as USD per 1M tokens, matching common OpenClaw/OpenAI-style model pricing.
    """
    models_path = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"
    try:
        data = json.loads(models_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    out: dict[str, dict[str, float]] = {}
    for provider in (data.get("providers") or {}).values():
        for model in provider.get("models") or []:
            model_id = model.get("id")
            if not model_id:
                continue
            cost = model.get("cost") or {}
            out[model_id] = {
                "input": _safe_float(cost.get("input")),
                "output": _safe_float(cost.get("output")),
                "cacheRead": _safe_float(cost.get("cacheRead")),
                "cacheWrite": _safe_float(cost.get("cacheWrite")),
            }
    return out


def estimate_usage_cost_usd(
    model: str,
    usage: dict[str, int],
    model_costs: dict[str, dict[str, float]] | None = None,
) -> tuple[float, bool]:
    """Return (estimated USD cost, price_configured)."""
    costs = (model_costs or load_model_costs()).get(model, {})
    input_rate = _safe_float(costs.get("input"))
    output_rate = _safe_float(costs.get("output"))
    cache_read_rate = _safe_float(costs.get("cacheRead"))
    cache_write_rate = _safe_float(costs.get("cacheWrite"))
    price_configured = any(_safe_float(v) > 0 for v in costs.values())

    input_tokens = _safe_int(usage.get("input_tokens"))
    cached_input_tokens = min(_safe_int(usage.get("cached_input_tokens")), input_tokens)
    cache_write_tokens = _safe_int(usage.get("cache_write_tokens"))
    uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
    output_tokens = _safe_int(usage.get("output_tokens"))

    # If cacheRead is not configured, fall back to normal input price for cached tokens.
    effective_cache_read_rate = cache_read_rate if cache_read_rate > 0 else input_rate
    effective_cache_write_rate = cache_write_rate if cache_write_rate > 0 else input_rate
    total = (
        uncached_input_tokens * input_rate
        + cached_input_tokens * effective_cache_read_rate
        + cache_write_tokens * effective_cache_write_rate
        + output_tokens * output_rate
    ) / 1_000_000
    return total, price_configured


def _normalize_token_usage(usage: dict[str, Any] | None, *, openclaw_shape: bool = False) -> dict[str, int]:
    usage = usage or {}
    input_tokens = _safe_int(
        usage.get("input_tokens")
        or usage.get("input")
        or usage.get("prompt_tokens")
    )
    cached_input_tokens = _safe_int(
        usage.get("cached_input_tokens")
        or usage.get("cacheRead")
        or usage.get("cache_read")
        or usage.get("cache_read_tokens")
    )
    cache_write_tokens = _safe_int(
        usage.get("cache_write_tokens")
        or usage.get("cacheWrite")
        or usage.get("cache_write")
    )
    output_tokens = _safe_int(
        usage.get("output_tokens")
        or usage.get("output")
        or usage.get("completion_tokens")
    )
    raw_total = _safe_int(usage.get("total_tokens") or usage.get("totalTokens"))
    if raw_total:
        total_tokens = raw_total
    elif openclaw_shape:
        total_tokens = input_tokens + cached_input_tokens + cache_write_tokens + output_tokens
    else:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def record_usage(
    collector: UsageCollector | None,
    *,
    model: str,
    usage: dict[str, int] | None,
    kind: str = "task_llm",
    source: str = "task",
) -> None:
    """Append one task-local LLM usage entry to a mutable collector."""
    if collector is None or not usage:
        return
    normalized = _normalize_token_usage(usage)
    if not any(normalized.values()):
        return
    cost_usd, price_configured = estimate_usage_cost_usd(model, normalized)
    collector.append({
        "kind": kind,
        "source": source,
        "model": model,
        **normalized,
        "estimated_cost_usd": cost_usd,
        "price_configured": price_configured,
    })


def _entry_cost_usd_from_openclaw_usage(usage: dict[str, Any]) -> tuple[float, bool]:
    cost = usage.get("cost")
    if isinstance(cost, dict):
        total = _safe_float(cost.get("total") or cost.get("totalCost"))
        if total == 0:
            total = sum(_safe_float(cost.get(k)) for k in ("input", "output", "cacheRead", "cacheWrite"))
        return total, total > 0
    total = _safe_float(usage.get("totalCost") or usage.get("estimated_cost_usd"))
    return total, total > 0


def _parse_timestamp_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        # OpenClaw timestamps are normally ms. Accept seconds too.
        return int(value * 1000) if value < 10_000_000_000 else int(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except Exception:
        return None


def _content_usage_records(obj: dict[str, Any]) -> list[dict[str, Any]]:
    message = obj.get("message") if isinstance(obj.get("message"), dict) else obj
    records: list[dict[str, Any]] = []
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("usage"), dict):
                records.append(item)
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        records.append(message)
    return records


def collect_openclaw_session_usage_entries(
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    session_dir: str | Path | None = None,
) -> list[UsageEntry]:
    """Collect OpenClaw's own model usage from local session logs.

    Only normal ``*.jsonl`` session files are read; checkpoint files are skipped
    to avoid double-counting. ``start_ms``/``end_ms`` make the result suitable
    for routine-report wrappers that export ``OPENCLAW_USAGE_START_MS`` at the
    beginning of a job.
    """
    if start_ms is None:
        start_ms = _safe_int(os.environ.get("OPENCLAW_USAGE_START_MS")) or None
    if end_ms is None:
        end_ms = _safe_int(os.environ.get("OPENCLAW_USAGE_END_MS")) or int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    if start_ms is None:
        lookback = _safe_float(os.environ.get("OPENCLAW_USAGE_LOOKBACK_MINUTES"))
        if lookback > 0:
            start_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000 - lookback * 60_000)
    if start_ms is None:
        return []

    base = Path(session_dir) if session_dir else Path.home() / ".openclaw" / "agents" / "main" / "sessions"
    if not base.exists():
        return []

    entries: list[UsageEntry] = []
    seen: set[tuple[str, int, int]] = set()
    for path in sorted(base.glob("*.jsonl")):
        if ".checkpoint." in path.name:
            continue
        try:
            # Fast skip for old files.
            if path.stat().st_mtime * 1000 < start_ms - 86_400_000:
                continue
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line_no, line in enumerate(lines, start=1):
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts_ms = _parse_timestamp_ms(obj.get("timestamp"))
            if ts_ms is None and isinstance(obj.get("message"), dict):
                ts_ms = _parse_timestamp_ms(obj["message"].get("timestamp"))
            if ts_ms is None or ts_ms < start_ms or (end_ms is not None and ts_ms > end_ms):
                continue
            for rec_idx, rec in enumerate(_content_usage_records(obj)):
                usage = rec.get("usage") or {}
                if not isinstance(usage, dict):
                    continue
                normalized = _normalize_token_usage(usage, openclaw_shape=True)
                if not any(normalized.values()):
                    continue
                key = (str(path), line_no, rec_idx)
                if key in seen:
                    continue
                seen.add(key)
                provider = str(rec.get("provider") or "").strip()
                model = str(rec.get("model") or usage.get("model") or "unknown").strip() or "unknown"
                model_name = f"{provider}/{model}" if provider and not model.startswith(f"{provider}/") else model
                cost_usd, price_configured = _entry_cost_usd_from_openclaw_usage(usage)
                entries.append({
                    "kind": "openclaw_self",
                    "source": "openclaw",
                    "model": model_name,
                    **normalized,
                    "estimated_cost_usd": round(cost_usd, 8),
                    "price_configured": price_configured,
                    "timestamp_ms": ts_ms,
                    "session_file": path.name,
                })
    return entries


def _new_bucket() -> dict[str, Any]:
    return {
        "calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "price_configured": False,
    }


def aggregate_usage(entries: list[UsageEntry] | None) -> dict[str, Any]:
    """Aggregate per-call entries into a report-level usage summary."""
    entries = [deepcopy(e) for e in (entries or [])]
    models: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    totals = {
        "calls": len(entries),
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "price_configured": False,
        "entries": entries,
        "models": models,
        "sources": sources,
    }
    for entry in entries:
        model = str(entry.get("model") or "unknown")
        source = str(entry.get("source") or ("openclaw" if str(entry.get("kind", "")).startswith("openclaw") else "task"))
        model_bucket = models.setdefault(model, _new_bucket())
        source_bucket = sources.setdefault(source, _new_bucket())
        model_bucket["calls"] += 1
        source_bucket["calls"] += 1
        for key in ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "total_tokens"):
            value = _safe_int(entry.get(key))
            totals[key] += value
            model_bucket[key] += value
            source_bucket[key] += value
        cost = _safe_float(entry.get("estimated_cost_usd"))
        totals["estimated_cost_usd"] += cost
        model_bucket["estimated_cost_usd"] += cost
        source_bucket["estimated_cost_usd"] += cost
        if bool(entry.get("price_configured")):
            totals["price_configured"] = True
            model_bucket["price_configured"] = True
            source_bucket["price_configured"] = True
    totals["estimated_cost_usd"] = round(float(totals["estimated_cost_usd"]), 8)
    for bucket in list(models.values()) + list(sources.values()):
        bucket["estimated_cost_usd"] = round(float(bucket["estimated_cost_usd"]), 8)
    totals["model_names"] = sorted(models.keys())
    return totals


def usage_summary_with_openclaw(
    entries: list[UsageEntry] | None = None,
    *,
    reason: str = "未调用 LLM",
    start_ms: int | None = None,
    end_ms: int | None = None,
    include_openclaw: bool | None = None,
) -> dict[str, Any]:
    """Aggregate task entries plus OpenClaw self-usage from the current job window."""
    if include_openclaw is None:
        include_openclaw = os.environ.get("OPENCLAW_INCLUDE_SELF_USAGE", "1") != "0"
    merged = list(entries or [])
    if include_openclaw:
        merged.extend(collect_openclaw_session_usage_entries(start_ms=start_ms, end_ms=end_ms))
    if not merged:
        return zero_usage(reason)
    summary = aggregate_usage(merged)
    if not entries:
        summary["reason"] = reason
    return summary


def zero_usage(reason: str = "未调用 LLM") -> dict[str, Any]:
    """Usage summary for deterministic/non-LLM reports."""
    return {
        "calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "price_configured": False,
        "reason": reason,
        "entries": [],
        "models": {},
        "model_names": [],
        "sources": {},
    }


def _fmt_int(value: Any) -> str:
    return f"{_safe_int(value):,}"


def _fmt_cost(value: Any) -> str:
    return f"${_safe_float(value):.6f}"


def _model_list(usage: dict[str, Any]) -> str:
    names = usage.get("model_names") or sorted((usage.get("models") or {}).keys())
    return "、".join(str(x) for x in names if x) or "无"


def _source_label(source: str) -> str:
    return {
        "task": "任务 LLM",
        "openclaw": "OpenClaw 本身",
    }.get(source, source)


def _format_bucket(data: dict[str, Any]) -> str:
    return (
        f"输入 {_fmt_int(data.get('input_tokens'))} / "
        f"缓存 {_fmt_int(data.get('cached_input_tokens'))} / "
        f"输出 {_fmt_int(data.get('output_tokens'))} / "
        f"总计 {_fmt_int(data.get('total_tokens'))} tokens / "
        f"费用 {_fmt_cost(data.get('estimated_cost_usd'))}"
    )


def format_usage_markdown(usage: dict[str, Any] | None, *, heading_level: int = 2) -> str:
    """Render a Markdown section for report files."""
    usage = usage or zero_usage()
    h = "#" * heading_level
    lines = [f"{h} Token 使用与费用", ""]
    calls = _safe_int(usage.get("calls"))
    if calls == 0:
        lines.append(f"- LLM 调用：0（{usage.get('reason') or '未调用 LLM'}）")
    else:
        source_bits = []
        for source, data in (usage.get("sources") or {}).items():
            source_bits.append(f"{_source_label(source)} {data.get('calls', 0)}")
        suffix = f"（{' + '.join(source_bits)}）" if source_bits else ""
        lines.append(f"- LLM / OpenClaw 调用：{_fmt_int(calls)}{suffix}")
    lines.append(f"- 模型：{_model_list(usage)}")
    lines.append(f"- 输入 tokens：{_fmt_int(usage.get('input_tokens'))}（缓存命中 {_fmt_int(usage.get('cached_input_tokens'))}）")
    lines.append(f"- 输出 tokens：{_fmt_int(usage.get('output_tokens'))}")
    lines.append(f"- 总 tokens：{_fmt_int(usage.get('total_tokens'))}")
    lines.append(f"- 估算费用：{_fmt_cost(usage.get('estimated_cost_usd'))}")
    if usage.get("sources"):
        for source, data in usage["sources"].items():
            lines.append(f"- {_source_label(source)}：{_format_bucket(data)}")
    if calls > 0 and not usage.get("price_configured"):
        lines.append("- 备注：本地模型价格未配置或为 0；费用按当前配置估算，实际账单以服务商为准。")
    if usage.get("models"):
        model_bits = []
        for model, data in usage["models"].items():
            model_bits.append(
                f"{model}: {data.get('calls', 0)} calls / {_fmt_int(data.get('total_tokens'))} tokens / {_fmt_cost(data.get('estimated_cost_usd'))}"
            )
        lines.append(f"- 模型明细：{'; '.join(model_bits)}")
    return "\n".join(lines).rstrip() + "\n"


def format_usage_footer(usage: dict[str, Any] | None) -> str:
    """Compact footer for Weixin/notification messages."""
    usage = usage or zero_usage()
    calls = _safe_int(usage.get("calls"))
    if calls == 0:
        base = "Token 使用与费用：未调用 LLM；费用 $0.000000"
    else:
        base = (
            "Token 使用与费用："
            f"输入 {_fmt_int(usage.get('input_tokens'))} / "
            f"输出 {_fmt_int(usage.get('output_tokens'))} / "
            f"总计 {_fmt_int(usage.get('total_tokens'))} tokens；"
            f"模型 {_model_list(usage)}；"
            f"估算费用 {_fmt_cost(usage.get('estimated_cost_usd'))}"
        )
        openclaw = (usage.get("sources") or {}).get("openclaw")
        if openclaw:
            base += f"；OpenClaw 本身 {_fmt_int(openclaw.get('total_tokens'))} tokens / {_fmt_cost(openclaw.get('estimated_cost_usd'))}"
    if calls > 0 and not usage.get("price_configured"):
        base += "（本地价格未配置/为 0，实际账单以服务商为准）"
    return base


def append_usage_section(markdown: str, usage: dict[str, Any] | None, *, heading_level: int = 2) -> str:
    """Append the detailed usage section unless it already exists."""
    if "Token 使用与费用" in markdown[-2000:]:
        return markdown
    return markdown.rstrip() + "\n\n---\n\n" + format_usage_markdown(usage, heading_level=heading_level)
