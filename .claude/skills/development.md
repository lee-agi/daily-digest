---
name: daily-digest-development
description: How to add collectors, debug failures, test, and deploy daily-digest
---

# 开发指引

## 新增 Data Source（Collector）
1. 创建 `collectors/my_source.py`，继承 `BaseCollector`，设置 `source_name = "my_source"` 和 `source_type`
2. 实现 `async def collect(self) -> list[ContentItem]`，用 `self._request_with_retry()` 发 HTTP 请求
3. 在 `config.yaml` 的 `sources:` 下添加同名配置块（`enabled`, `score_threshold`, `lookback_hours`, `max_items`）
4. 在 `collectors/__init__.py` import 新 collector（触发 auto-registration）
5. 写测试：`tests/test_my_source.py`，mock `httpx.AsyncClient` 或用 `pytest-httpx`
6. 验证：`.venv/bin/python orchestrator.py --collect-only --source my_source`

## 调试 Collector 失败
1. 检查 `--verbose` 输出：`.venv/bin/python orchestrator.py --collect-only --source X --verbose`
2. 确认环境变量已加载：`python -c "import os; print(os.environ.get('GITHUB_TOKEN'))"`
3. 检查 RSSHub 是否运行：`curl http://localhost:1200/healthz`（baoyu_blog/xiaoyuzhou 依赖）
4. 检查 any2summary 是否可用：`~/Documents/Code/any2summary/venv/bin/any2summary --help`
5. 看 SQLite 状态：`sqlite3 state.db "SELECT * FROM seen_items ORDER BY first_seen_at DESC LIMIT 10;"`

## 调试 Enrichment 问题
1. 单独测试 enrichment：在 Python REPL 中调用 `ContentEnricher` 的 `enrich_item()`
2. 检查熔断状态：enricher 连续 3 次失败后会跳过该类别（日志中有 `circuit breaker` 关键字）
3. 超时链：subprocess 180s（外层）→ httpx 30s（内层）；hang 通常在 subprocess 层
4. 临时禁用：`config.yaml` 设 `enrichment.enabled: false`

## 测试策略
- 框架：pytest + pytest-asyncio（`asyncio_mode = "auto"`）
- 测试路径：`tests/`（22 文件，~4,600 LOC）
- 运行 unit：`.venv/bin/python -m pytest tests/ -v -m "not integration"`
- 运行全部：`.venv/bin/python -m pytest tests/ -v`
- Integration 测试需要真实 API key，`@pytest.mark.integration` 标记，默认跳过
- mock 工具：`pytest-httpx` 拦截 `httpx` 请求

## 部署
1. 确保 `~/.secrets` 包含所有 API key（`scripts/run.sh` 会 `source ~/.secrets`）
2. OpenClaw cron 07:00 CST：`scripts/run.sh --full`
3. 手动触发：`scripts/run.sh --full --dry-run`（验证不推送）
4. Launchd plist 位于 `launchd/com.openclaw.digest-collect.plist`（已卸载，改用 cron）
