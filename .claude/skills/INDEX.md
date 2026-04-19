---
name: daily-digest-skills
description: Skills index for daily-digest. Use when asking about architecture, collectors, how to add a new data source, debugging pipeline failures, enrichment issues, or configuration.
version: 0.17.1
generated: 2026-04-12T16:30:00+08:00
context_file: .claude/project-context.local.md
---

# daily-digest 技能索引

多源每日信息聚合 pipeline：从 19 个平台采集内容，去重过滤后用 Claude Opus 生成 Markdown 摘要，推送至 RSS Worker + Feishu。

## 按场景导航
| 需要 | 读取文件 |
|------|---------|
| 常用命令/关键文件 | `quickstart.md` |
| 架构/数据流/设计模式 | `architecture.md` |
| 新增 data source | `modules/collectors.md` |
| Aggregator/Dedup/Filter | `modules/aggregator.md` |
| LLM 摘要生成/推送 | `modules/report.md` |
| 编排器/CLI 参数 | `modules/orchestrator.md` |
| Enrichment 调试 | `modules/enricher.md` |
| 新增功能/调试/部署 | `development.md` |
| 常见问题/故障排查 | `faq.md` |
| 环境变量/config.yaml | `config.md` |

## 模块索引
| 模块路径 | 用途（一行） | 文件 |
|---------|------------|------|
| `collectors/` | 19 个平台的数据采集器，BaseCollector + auto-registration | `modules/collectors.md` |
| `aggregator/` | 跨源去重、score 过滤、广告过滤、分类分组 | `modules/aggregator.md` |
| `report/` | LLM 摘要生成（Azure OpenAI Opus）+ RSS/Feishu 推送 | `modules/report.md` |
| `orchestrator.py` | Pipeline 编排入口，CLI 参数解析，阶段协调 | `modules/orchestrator.md` |
| `collectors/enricher.py` | any2summary 内容富化，熔断器，engagement 评分 | `modules/enricher.md` |
| `schema.py` | Pydantic 数据模型（ContentItem/CollectorResult/DigestReport） | `architecture.md` |
| `state.py` | SQLite 去重状态管理，seen_items + run_history | `modules/orchestrator.md` |
