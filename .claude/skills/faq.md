---
name: daily-digest-faq
description: FAQ and troubleshooting for daily-digest
---

# FAQ 与故障排查

## 常见问题

### 问：为什么今天的 digest 只有几条内容？
可能原因：(1) `mark_seen()` 过早标记导致 `filter_unseen()` 过滤大部分内容（v0.16.1 已修复，确认用 `--full` 单次运行而非两阶段 cron）；(2) 某 collector 静默失败；(3) `lookback_hours` 太短，用 `--lookback-hours 48` 扩大窗口。

### 问：同日运行多次会覆盖之前的文件吗？
不会。v0.17.1 起文件名带时间戳：`collected-{date}-{HHMM}.json` 和 `digest-{date}-{HHMM}.md`。`--summarize-and-push` 会 glob 取最新 JSON 文件（按 mtime 排序）。

### 问：如何只测试某个 source，不影响 seen_items？
使用 `--dry-run`：`.venv/bin/python orchestrator.py --collect-only --source github --dry-run`（dry-run 模式下 `mark_seen()` 不执行）。

### 问：enrichment 一直超时，怎么临时关掉？
在 `config.yaml` 设 `enrichment.enabled: false`，或在 CLI 中暂无直接 flag，需改 config。

### 问：如何向 digest 临时注入一个 URL？
`python orchestrator.py --collect-only --inject-url "https://example.com/article"`，会写入 `data/pending_urls.yaml`，下次采集时被 `ManualURLCollector` 处理。

### 问：RSSHub 相关 source（baoyu_blog）采不到内容？
先检查 RSSHub 是否运行：`curl http://localhost:1200/healthz`。若无，用 `docker compose up -d`（`docker/docker-compose.yml`）启动。

## 故障排查

### 常见错误
- **`KeyError: source_name`**：新 collector 未在 `collectors/__init__.py` import，导致 auto-registration 未触发
- **`No items after filter_unseen`**：前一次运行的 `mark_seen()` 已标记，用 `--lookback-hours 48` 或清理 `state.db`
- **`Azure OpenAI 401/403`**：`AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` 未 export；确认 `scripts/run.sh` 中 eval grep 语句命中
- **`twikit auth failed`**：X cookie 过期，需重新登录并更新 `~/.secrets` 中的 `X_AUTH_TOKEN` / `X_CT0`
- **`ccf_bestpaper OpenReview 429`**：速率限制，collector 内已有 0.5s delay，偶发可忽略；持续则降低 `limit` 参数

### 调试技巧
- 查看完整日志：加 `--verbose` flag
- 检查上次运行结果：`sqlite3 state.db "SELECT * FROM run_history ORDER BY started_at DESC LIMIT 5;"`
- 单 source dry-run：`python orchestrator.py --collect-only --source zhihu --dry-run --verbose`
- 检查中间 JSON：`ls -lt data/collected-*.json | head -3` 然后 `cat data/collected-latest.json | python -m json.tool | head -50`
- 检查 collector 注册：`python -c "from collectors.base import CollectorRegistry; import collectors; print(CollectorRegistry.all_names())"`
