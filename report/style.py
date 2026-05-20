"""Shared report language/style rules.

These rules are intentionally small and reusable: daily AI reports, higher-period
AI reports, and finance/Futu reports should all keep technical terminology
natural instead of over-translating it into awkward Chinese.
"""

from __future__ import annotations

SUMMARY_LANGUAGE_POLICY = """- 表达原则：常见口语和连接句可自然中文化，读起来像中文信息整理，不像逐句翻译稿。
- 术语原则：专业词汇、产品名、人名、机构名、ticker、模型名、API/协议/框架名、金融术语和缩写尽量保留原文；必要时只在首次出现时补一个简短中文解释。
- 不硬翻：不要把 `agent`、`LLM`、`inference`、`CUDA`、`ETF`、`SEC filing`、`guidance`、`buyback` 等术语强行翻成生硬中文。
- 一致性：同一术语在全文中保持同一种写法。"""

GENERAL_LANGUAGE_POLICY_LINE = "- 表达口径：常见口语自然中文化；专业词汇、产品名、人名、模型/API/框架名保留原文，不硬翻。"
FINANCE_LANGUAGE_POLICY_LINE = "- 表达口径：常见表述自然中文化；ticker、order type、ETF、SEC filing、guidance、buyback 等金融/交易术语保留原文，不硬翻。"


def language_style_section(title: str = "表达与术语原则") -> str:
    """Return a Markdown section for LLM prompts or generated reports."""
    return f"## {title}\n{SUMMARY_LANGUAGE_POLICY}"


def language_style_prompt_lines() -> str:
    """Return compact prompt lines suitable for an existing numbered list."""
    return SUMMARY_LANGUAGE_POLICY
