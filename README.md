# Daily Digest - Multi-Source Daily Information Aggregator

每日多源信息聚合系统，自动从 19 个平台采集内容，去重过滤后通过 any2summary 深度增强高质量条目，生成 LLM 摘要报告。

**当前版本**: v0.18.1 | **活跃源**: 16/19 | **典型产出**: ~360 items/run

## Quick Start

```bash
# 1. 安装依赖
cd ~/.openclaw/daily-digest
uv venv .venv && source .venv/bin/activate
uv pip install -r <(python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print('\n'.join(d['project']['dependencies']))")

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API keys

# 3. 启动 RSSHub（WeRead / Kindle 现在默认走本地 RSSHub）
# 先确保 Docker Desktop 已启动，再执行：
docker-compose -f docker/docker-compose.yml up -d
# 若本机不用 Docker，也可尝试单独运行 RSSHub（需自行安装）
# npx rsshub

# 4. 运行
python orchestrator.py --full --dry-run --no-enrich  # 完整流程（不推送，不 enrich）
python orchestrator.py --full --dry-run --lookback-hours 48  # 自定义回溯窗口
python orchestrator.py --collect-only     # 仅采集
python orchestrator.py --collect-only --source huggingface  # 单源测试
python orchestrator.py --collect-only --inject-url "https://example.com"  # 注入 URL
python orchestrator.py --enrich-only      # 独立运行 enrichment（基于最新 collected JSON）
```

## Data Sources

| Platform | Method | Auth Required |
|----------|--------|---------------|
| X/Twitter | TwitterAPI.io (+ twikit fallback) | TWITTER_API_IO_KEY (+ X_AUTH_TOKEN/X_CT0) |
| GitHub | REST API | GITHUB_TOKEN (optional) |
| Reddit | OAuth2/Public API | REDDIT_CLIENT_ID/SECRET (optional) |
| YouTube | Data API v3 (playlistItems + videos) | YOUTUBE_DATA_API_KEY（或兼容 YOUTUBE_API_KEY） |
| 知乎 | Direct API (hot/recommend/follow) | zhihu-cli cookies |
| 即刻 | RSSHub | JIKE_COOKIES → RSSHub |
| 小宇宙 | RSSHub | None |
| HuggingFace | HTTP API | None |
| papers.cool | HTTP Scrape | None |
| Apple Podcast | Native RSS | None |
| WeRead | RSS feeds（含微信读书热榜） | None |
| Kindle 热门图书 | RSS feeds（Kindle/ebook榜单） | None |
| Anthropic Blog | HTTP Scrape | None |
| OpenAI Blog | Native RSS | None |
| Google Blog | Native RSS | None |
| CCF Best Paper | OpenReview API + YAML | None |
| 机器之心/量子位/AI洞察日报 | RSS (GitHub/CloudFlare) | None |
| 宝玉博客 | 直连 RSS + RSSHub fallback | None（可选 RSSHub） |
| Product Hunt | GraphQL API (top 10/day) | PRODUCTHUNT_API_TOKEN |
| Manual URLs | any2summary + Mac Reminders T5T | None |

## Content Enrichment

内容增强功能，对高质量条目调用 [any2summary](~/Documents/Code/any2summary) 获取全文/转录。v0.18.1 起 enrichment 独立于主 pipeline 运行：

- **主 pipeline** (`--full`): 默认传 `--no-enrich`，快速完成采集+摘要+推送
- **Enrichment** (`--enrich-only`): 独立执行，仅处理英文 Articles / YouTube / Podcast，输出 `enriched-{date}.json`

支持的内容类型：
- **文章** (Anthropic/OpenAI/Google Blog): 内容 <5k 字符自动获取全文 (timeout: 30s)
- **YouTube**: 按互动率排序 top-N，字幕+摘要 (timeout: 600s)
- **播客**: 优先使用 `audio_url` 直传音频，≤30min 自动转录 (timeout: 180s)
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

### macOS launchd
- `com.openclaw.digest-full`: **07:00** full pipeline (`--full --no-enrich` via `scripts/run.sh` default)

```bash
cp launchd/com.openclaw.digest-full.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.openclaw.digest-collect.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.openclaw.digest-summarize.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.openclaw.digest-full.plist
launchctl list | grep digest  # 验证
```

Wrapper script (`scripts/run.sh`) handles environment variable loading.
- collect: `scripts/run.sh --collect-only`
- summarize/push: `scripts/run.sh --summarize-and-push`
- full/manual: `scripts/run.sh` (default `--full --no-enrich`)

## Known Issues
- **Reddit**: OAuth app registration blocked by Responsible Builder Policy; using public API (13 items/run)
- **Proxy**: httpx 0.28 + `httpx[socks]` ignores `proxy=None` for localhost; RSSHub collector uses `AsyncHTTPTransport()` to bypass
- **YouTube RSS deprecated**: YouTube permanently disabled RSS feeds (404). Migrated to Data API v3 `playlistItems.list` in v0.15.0. `YOUTUBE_DATA_API_KEY` is now required.
- **RSSHub sources**: Jike disabled (no user IDs configured). Baoyu Blog now supports direct RSS fallback; RSSHub is optional.
