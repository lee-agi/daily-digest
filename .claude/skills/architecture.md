---
name: daily-digest-architecture
description: Architecture overview, data flow, and design patterns for daily-digest
---

# 架构

## 整体结构
```
daily-digest/
├── orchestrator.py          ← Pipeline 编排入口 (CLI)
├── schema.py                ← Pydantic 数据模型
├── state.py                 ← SQLite 去重 + 运行历史
├── config.yaml              ← 所有配置（源阈值、enrichment、ad_filter）
├── collectors/
│   ├── base.py              ← BaseCollector ABC + CollectorRegistry
│   ├── enricher.py          ← any2summary 内容富化 + 熔断器
│   ├── manual_url_collector.py  ← 手动 URL + Mac Reminders
│   └── [19 source collectors]
├── aggregator/
│   ├── dedup.py             ← URL/ArXiv/标题相似度去重
│   ├── filter.py            ← score 阈值过滤
│   ├── merger.py            ← 分类分组（6 个 category）
│   └── ad_filter.py         ← 三层广告过滤
├── report/
│   ├── generator.py         ← Azure OpenAI Opus SSE 摘要生成
│   ├── push.py              ← RSS Worker + Feishu 推送
│   └── template.py          ← RSS item 格式
└── data/
    └── pending_urls.yaml    ← 手动注入 URL 队列
```

## 数据流
1. `orchestrator.py --full` 启动
2. `CollectorRegistry.create_all(config)` 实例化所有 enabled collector
3. 所有 collector 并发执行 `async collect()` → `list[ContentItem]`
4. `BaseCollector.run()` 内部：score 过滤 → ad 过滤 → max_items 截断
5. `ContentEnricher.enrich_batch()` 对文章/YouTube/播客调用 any2summary 补全内容
6. `state.filter_unseen()` 过滤 SQLite 中已见 URL/ArXiv ID
7. `aggregator.dedup()` 跨源去重（URL exact > ArXiv ID > title 相似度 0.85）
8. `report/generator.py` 调用 Azure OpenAI Opus（SSE 流式）生成 Markdown
9. `state.mark_seen()` 仅在 push 成功后标记（dry-run 不标记）
10. `report/push.py` 推送到 RSS Worker + Feishu

## 核心设计模式（6条）
- **Registry Pattern**：`CollectorRegistry._registry` dict + `__init_subclass__` 自动注册（`collectors/base.py:24`）
- **Template Method**：`BaseCollector.run()` 定义采集骨架，子类实现 `async collect()`（`collectors/base.py:100`）
- **Exponential Backoff Retry**：`BaseCollector._request_with_retry()` 共享重试（5xx/429/timeout）（`collectors/base.py:150`）
- **Circuit Breaker**：`ContentEnricher.process_batch()` 每类别连续 3 次失败后跳过（`collectors/enricher.py:200`）
- **Pipeline Stage Separation**：`--collect-only` / `--summarize-and-push` / `--full` 三段式 CLI
- **Idempotent Dedup**：`state.mark_seen()` 在 push 后执行，确保 dry-run 不污染 SQLite 状态
