---
name: daily-digest-module-report
description: report/ — Azure OpenAI Opus SSE 摘要生成 + RSS Worker/Feishu 推送
---

# report/

**用途：** 将分类整理后的 `ContentItem` 列表通过 Claude Opus（Azure OpenAI SSE）生成 Markdown 摘要，然后推送到 RSS Worker 和 Feishu。（~740 LOC）

## 关键文件
| 文件 | 说明 |
|------|------|
| `generator.py` | LLM 调用（SSE 流式）、prompt 构建、`DigestReport` 生成 |
| `push.py` | RSS Worker POST + Feishu webhook 推送 |
| `template.py` | RSS item payload 格式化 |

## 核心抽象
- `generate_digest()` (`generator.py:80`) — 主入口：group_by_category → build prompt → LLM → DigestReport
- `DIGEST_PROMPT` (`generator.py:25`) — 中文摘要 prompt，含 Today's Top N、跨平台分析、分类详情、平台统计
- `_call_llm_streaming()` (`generator.py:140`) — Azure OpenAI SSE 流式调用，含 retry
- `DigestReport` (`schema.py:70`) — `full_markdown` + `date` + `generated_at` + `source_stats`
- `push_to_rss_worker()` (`push.py:20`) — POST `/items` 到 Cloudflare RSS Worker，`X-API-Key` 认证
- `push_to_feishu()` (`push.py:60`) — Feishu webhook POST，发送 Markdown 消息
- `push_items_batch()` (`push.py:80`) — 批量推送 individual items（备用路径）

## 开发指引
1. 修改摘要风格：编辑 `generator.py:DIGEST_PROMPT`（结构化 prompt，含翻译规范）
2. 新增 distribution channel：在 `push.py` 新增 `async def push_to_xxx()` 函数，在 orchestrator 调用
3. 修改 top_headlines 数量：`config.yaml` `summary.top_headlines` 字段
4. 添加 category：`aggregator/merger.py:CATEGORY_RULES` + `config.yaml` `summary.categories`
5. 常见陷阱：RSS Worker 的 `link` 字段用 `{url}/feed#digest-{date}-{ts}` 格式确保每次推送唯一

## ⚠️ 已知问题
- Azure OpenAI SSE 偶发 timeout（已有 retry 保护），若摘要模型调整需同步更新 `generator.py` 中 deployment 名称
