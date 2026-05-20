"""Tests for report/usage.py token/cost helpers."""

from __future__ import annotations


def test_extract_response_usage_responses_api_shape() -> None:
    from report.usage import extract_response_usage

    usage = extract_response_usage({
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 345,
            "total_tokens": 1545,
            "input_tokens_details": {"cached_tokens": 200},
        }
    })

    assert usage == {
        "input_tokens": 1200,
        "cached_input_tokens": 200,
        "output_tokens": 345,
        "total_tokens": 1545,
    }


def test_estimate_usage_cost_usd_with_cache_read_price() -> None:
    from report.usage import estimate_usage_cost_usd

    cost, configured = estimate_usage_cost_usd(
        "test-model",
        {"input_tokens": 1000, "cached_input_tokens": 250, "output_tokens": 500},
        model_costs={"test-model": {"input": 2.0, "output": 10.0, "cacheRead": 0.5}},
    )

    # (750 * 2 + 250 * 0.5 + 500 * 10) / 1M
    assert configured is True
    assert cost == 0.006625


def test_format_usage_markdown_contains_cost_and_model_detail() -> None:
    from report.usage import aggregate_usage, format_usage_markdown

    summary = aggregate_usage([
        {
            "model": "llab-gpt-5.4",
            "input_tokens": 1000,
            "cached_input_tokens": 100,
            "output_tokens": 200,
            "total_tokens": 1200,
            "estimated_cost_usd": 0.00123,
            "price_configured": True,
        }
    ])
    text = format_usage_markdown(summary)

    assert "## Token 使用与费用" in text
    assert "输入 tokens：1,000" in text
    assert "输出 tokens：200" in text
    assert "估算费用：$0.001230" in text
    assert "模型：llab-gpt-5.4" in text
    assert "模型明细：llab-gpt-5.4" in text


def test_format_usage_footer_includes_model_list_and_openclaw_self() -> None:
    from report.usage import aggregate_usage, format_usage_footer

    summary = aggregate_usage([
        {
            "source": "task",
            "model": "llab-gpt-5.4",
            "input_tokens": 100,
            "cached_input_tokens": 0,
            "output_tokens": 20,
            "total_tokens": 120,
            "estimated_cost_usd": 0.0001,
            "price_configured": True,
        },
        {
            "source": "openclaw",
            "model": "microsoft-foundry/llab-gpt-5.5-1",
            "input_tokens": 50,
            "cached_input_tokens": 500,
            "output_tokens": 10,
            "total_tokens": 560,
            "estimated_cost_usd": 0.0002,
            "price_configured": True,
        },
    ])

    footer = format_usage_footer(summary)

    assert "llab-gpt-5.4" in footer
    assert "microsoft-foundry/llab-gpt-5.5-1" in footer
    assert "总计 680 tokens" in footer
    assert "OpenClaw 本身 560 tokens" in footer


def test_collect_openclaw_session_usage_entries_parses_session_log(tmp_path) -> None:
    from report.usage import collect_openclaw_session_usage_entries

    session = tmp_path / "abc.jsonl"
    session.write_text(
        '{"timestamp":"2026-04-28T00:00:01.000Z","message":{"role":"assistant","content":[{"type":"text","provider":"microsoft-foundry","model":"llab-gpt-5.5-1","usage":{"input":10,"output":3,"cacheRead":20,"totalTokens":33,"cost":{"input":0.1,"output":0.2,"cacheRead":0.05}}}]}}\n',
        encoding="utf-8",
    )

    entries = collect_openclaw_session_usage_entries(
        start_ms=1777334400000,
        end_ms=1777334402000,
        session_dir=tmp_path,
    )

    assert len(entries) == 1
    assert entries[0]["source"] == "openclaw"
    assert entries[0]["model"] == "microsoft-foundry/llab-gpt-5.5-1"
    assert entries[0]["total_tokens"] == 33
    assert entries[0]["estimated_cost_usd"] == 0.35


def test_format_usage_footer_for_zero_usage() -> None:
    from report.usage import format_usage_footer, zero_usage

    footer = format_usage_footer(zero_usage("规则报告"))

    assert "未调用 LLM" in footer
    assert "$0.000000" in footer
