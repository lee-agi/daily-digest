---
name: daily-digest-module-orchestrator
description: orchestrator.py + state.py — Pipeline 编排入口，CLI 参数解析，SQLite 状态管理
---

# orchestrator.py + state.py

**用途：** Pipeline 编排入口（CLI）+ SQLite 去重状态管理。控制 collect/summarize/push 三个阶段。（~520 LOC）

## 关键文件
| 文件 | 说明 |
|------|------|
| `orchestrator.py` | CLI 入口、`run_collect()` / `run_summarize_and_push()` 、阶段协调 |
| `state.py` | SQLite `seen_items` + `run_history` 表，去重核心 |
| `data/pending_urls.yaml` | 手动注入 URL 队列（`_inject_urls()` 写入）|

## 核心抽象
- `main()` (`orchestrator.py:250`) — argparse 解析，dispatch 到 collect / summarize / full
- `run_collect()` (`orchestrator.py:100`) — 并发运行所有 collector，序列化中间 JSON
- `run_summarize_and_push()` (`orchestrator.py:160`) — glob 最新 JSON → enrich → dedup → generate → push → mark_seen
- `_inject_urls()` (`orchestrator.py:50`) — URL 注入到 `pending_urls.yaml`
- `filter_unseen()` (`state.py:60`) — 对比 `seen_items`，过滤已处理 URL/ArXiv ID
- `mark_seen()` (`state.py:80`) — 批量写入 `seen_items`（仅 push 成功后调用）
- `save_run()` (`state.py:100`) — 写 `run_history`（source/count/duration）
- `get_latest_run()` (`state.py:110`) — 读取上次运行记录（用于增量模式）

## 开发指引
1. 新增 CLI 参数：在 `main()` 的 `argparse.ArgumentParser` 添加 `add_argument`，在对应 `run_*` 函数使用
2. `--lookback-hours N` 实现：collect 时把 N 注入所有 source config 的 `lookback_hours`
3. 中间文件命名：`collected-{date}-{HHMM}.json`，`--summarize-and-push` 用 `glob` 按 mtime 取最新
4. 常见陷阱：`mark_seen()` 必须在 push **之后**调用；dry-run 跳过 push 也跳过 mark_seen

## ⚠️ 已知问题
- `run_summarize_and_push()` glob 取最新 JSON 靠 mtime 排序；若系统时间回拨可能取错文件
- SQLite WAL 模式已启用，多进程并发写安全，但不建议同时运行两个 orchestrator 实例
