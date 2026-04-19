---
name: daily-digest-module-enricher
description: collectors/enricher.py — any2summary 内容富化，熔断器，engagement 评分
---

# collectors/enricher.py

**用途：** 通过调用 any2summary CLI 对 articles/YouTube/podcasts 补全全文或字幕，提升 LLM 摘要质量。含熔断器防止 pipeline 整体卡死。（414 LOC）

## 关键文件
| 文件 | 说明 |
|------|------|
| `collectors/enricher.py` | ContentEnricher 主类 |
| `config.yaml` `enrichment:` | 超时、top_n、auto_threshold 配置 |

## 核心抽象
- `ContentEnricher` (`enricher.py:40`) — 主类，持 `EnrichmentConfig`
- `EnrichmentConfig.from_config()` (`enricher.py:20`) — 从 config dict 构建
- `enrich_batch()` (`enricher.py:120`) — 入口：按类型分组（articles/youtube/podcasts）→ 各自 enrich
- `process_batch()` (`enricher.py:200`) — 含熔断器：连续 3 次失败后设 `_circuit_open[category] = True` 跳过剩余
- `_enrich_article()` (`enricher.py:240`) — content < auto_threshold chars 自动触发；否则按 top_n 限制
- `_call_any2summary()` (`enricher.py:280`) — subprocess 调用 any2summary CLI，180s timeout
- `_compute_engagement_rate()` (`enricher.py:160`) — YouTube 调用 Data API 获取 views/likes，计算 engagement rate 用于排序
- `_select_top_n()` (`enricher.py:180`) — 按 engagement rate / duration / score 排序，取 top_n

## 开发指引
1. 调整 enrichment 范围：修改 `config.yaml` `enrichment.sources.<type>.top_n`
2. 跳过 enrichment 的条件：content 已 >= `article_auto_threshold` 且不在 top_n 内
3. 熔断器重置：每次调用 `enrich_batch()` 时重置（非跨 run 持久化）
4. 新增 enrichment 类型：在 `enrich_batch()` 添加新分支，实现 `_enrich_xxx()` 方法

## ⚠️ 已知问题
- any2summary subprocess 可能在网络慢时 hang（180s 超时是外层安全网，实际 hang 原因仍在调试中）
- `enrichment.enabled: true` 生产偶发阻塞，建议监控 `process_batch()` 的完成时间
- 熔断器状态不跨 run 持久化 — 每次全量 pipeline 都会重试失败的类别
