# Daily Digest - Multi-Source Daily Information Aggregator

每日多源信息聚合系统，自动从 19 个平台采集内容，去重过滤后通过 any2summary 深度增强高质量条目，生成 LLM 摘要报告。

**当前版本**: v0.17.0 | **活跃源**: 16/19 | **典型产出**: ~360 items/run

## Quick Start

```bash
# 1. 安装依赖
cd ~/.openclaw/daily-digest
uv venv .venv && source .venv/bin/activate
uv pip install -r <(python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print('\n'.join(d['project']['dependencies']))")

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API keys

# 3. 启动 RSSHub (可选，即刻/小宇宙/宝玉博客需要)
docker-compose -f docker/docker-compose.yml up -d

# 4. 运行
python orchestrator.py --full --dry-run  # 完整流程（不推送）
python orchestrator.py --full --dry-run --lookback-hours 48  # 自定义回溯窗口
python orchestrator.py --collect-only     # 仅采集
python orchestrator.py --collect-only --source huggingface  # 单源测试
python orchestrator.py --collect-only --inject-url "https://example.com"  # 注入 URL
```

## Data Sources

| Platform | Method | Auth Required |
|----------|--------|---------------|
| X/Twitter | TwitterAPI.io (+ twikit fallback) | TWITTER_API_IO_KEY (+ X_AUTH_TOKEN/X_CT0) |
| GitHub | REST API | GITHUB_TOKEN (optional) |
| Reddit | OAuth2/Public API | REDDIT_CLIENT_ID/SECRET (optional) |
| YouTube | Data API v3 (playlistItems) | YOUTUBE_DATA_API_KEY (required) |
| 知乎 | Direct API (hot/recommend/follow) | zhihu-cli cookies |
| 即刻 | RSSHub | JIKE_COOKIES → RSSHub |
| 小宇宙 | RSSHub | None |
| HuggingFace | HTTP API | None |
| papers.cool | HTTP Scrape | None |
| Apple Podcast | Native RSS | None |
| WeRead | Browser Relay | WeChat login |
| Anthropic Blog | HTTP Scrape | None |
| OpenAI Blog | Native RSS | None |
| Google Blog | Native RSS | None |
| CCF Best Paper | OpenReview API + YAML | None |
| 机器之心/量子位/AI洞察日报 | RSS (GitHub/CloudFlare) | None |
| 宝玉博客 | RSSHub /baoyu/blog | RSSHub |
| Product Hunt | GraphQL API (top 10/day) | PRODUCTHUNT_API_TOKEN |
| Manual URLs | any2summary + Mac Reminders T5T | None |

## Content Enrichment

v0.14.0 新增内容增强功能，在采集完成后自动对高质量条目调用 [any2summary](~/Documents/Code/any2summary) 获取全文/转录：

- **文章** (Anthropic/OpenAI/量子位等): 内容 <5k 字符的全部自动获取全文
- **YouTube**: 按互动率 (likes+comments)/views 排序，top-N 获取字幕+摘要
- **播客**: ≤30min 自动转录，>30min 输出 manual suggestion
- **手动 URL**: `data/pending_urls.yaml` 或 `--inject-url` 注入

```bash
# 注入 URL
python orchestrator.py --collect-only --inject-url "https://example.com/article"

# Mac Reminders "T5T" 清单中的 URL 自动导入 (config.yaml reminders_enabled: true)
```

配置位于 `config.yaml` 的 `enrichment` 部分。

## Adding New Sources

1. Create `collectors/my_source.py`:

```python
from collectors.base import BaseCollector
from schema import ContentItem, SourceType

class MySourceCollector(BaseCollector):
    source_name = "my_source"  # Must match config.yaml key
    source_type = SourceType.API

    async def collect(self) -> list[ContentItem]:
        # Fetch and return items
        ...
```

2. Add to `config.yaml`:

```yaml
sources:
  my_source:
    enabled: true
    score_threshold: 10
    lookback_hours: 24
    max_items: 20
```

That's it. The collector auto-registers via `__init_subclass__`.

## Configuration

All thresholds in `config.yaml` are per-source configurable:
- `score_threshold` — minimum engagement score to include
- `lookback_hours` — time window (default 24h)
- `max_items` — max items per source

## Testing

```bash
.venv/bin/python -m pytest tests/ -v
```

## Scheduling

### macOS launchd (data collection at 06:30)
```bash
cp launchd/com.openclaw.digest-collect.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.openclaw.digest-collect.plist
launchctl list | grep digest  # 验证
```

Wrapper scripts (`scripts/run-collect.sh`, `scripts/run-summarize.sh`) handle environment variable loading for launchd.

### OpenClaw cron (LLM summary at 07:00)
Configured via `~/.openclaw/cron/jobs.json`.

## Known Issues
- **Reddit**: OAuth app registration blocked by Responsible Builder Policy; using public API (13 items/run)
- **Proxy**: httpx 0.28 + `httpx[socks]` ignores `proxy=None` for localhost; RSSHub collector uses `AsyncHTTPTransport()` to bypass
- **YouTube RSS deprecated**: YouTube permanently disabled RSS feeds (404). Migrated to Data API v3 `playlistItems.list` in v0.15.0. `YOUTUBE_DATA_API_KEY` is now required.
- **RSSHub sources**: Jike disabled (no user IDs configured); Baoyu Blog depends on RSSHub at localhost:1200. RSSHub collector probes reachability before fetching.
