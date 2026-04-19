---
name: daily-digest-quickstart
description: Common commands and key files for daily-digest
---

# 快速参考

## 常用命令
| 任务 | 命令 |
|------|-----|
| 全量运行（dry-run，不推送） | `.venv/bin/python orchestrator.py --full --dry-run` |
| 全量运行（推送） | `scripts/run.sh --full` |
| 仅采集 | `.venv/bin/python orchestrator.py --collect-only` |
| 仅摘要+推送 | `.venv/bin/python orchestrator.py --summarize-and-push` |
| 单 source 测试 | `.venv/bin/python orchestrator.py --collect-only --source huggingface` |
| 注入 URL | `.venv/bin/python orchestrator.py --collect-only --inject-url "https://..."` |
| 自定义回看窗口 | `.venv/bin/python orchestrator.py --full --dry-run --lookback-hours 48` |
| 运行所有测试 | `.venv/bin/python -m pytest tests/ -v` |
| 仅运行 unit 测试 | `.venv/bin/python -m pytest tests/ -v -m "not integration"` |

## 关键文件
| 用途 | 路径 | 说明 |
|------|------|------|
| Pipeline 入口 | `orchestrator.py` | CLI 参数、采集→摘要→推送协调 |
| Base 采集器 | `collectors/base.py` | ABC + Registry + retry wrapper |
| 数据模型 | `schema.py` | ContentItem, DigestReport 等 Pydantic models |
| 去重状态 | `state.py` | SQLite seen_items + run_history |
| 总配置 | `config.yaml` | 所有 source 阈值、enrichment、ad_filter、distribution |
| 手动 URL | `data/pending_urls.yaml` | 待处理 URL 队列 |
| Digest 输出目录 | `~/Developer/research/daily-digest/` | `collected-{date}-{HHMM}.json` + `digest-{date}-{HHMM}.md` |
| Cron 脚本 | `scripts/run.sh` | 生产用 wrapper，source ~/.secrets |
| 状态 DB | `state.db` | SQLite，seen_items + run_history |
