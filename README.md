# Daily Digest - Multi-Source Daily Information Aggregator

每日多源信息聚合系统，自动从 18 个平台采集内容，去重过滤后生成 LLM 摘要报告。

**当前版本**: v0.12.0 | **活跃源**: 15/18 | **典型产出**: ~360 items/run

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
python orchestrator.py --collect-only     # 仅采集
python orchestrator.py --collect-only --source huggingface  # 单源测试
```

## Data Sources

| Platform | Method | Auth Required |
|----------|--------|---------------|
| X/Twitter | Browser Relay | Chrome login |
| GitHub | REST API | GITHUB_TOKEN (optional) |
| Reddit | OAuth2/Public API | REDDIT_CLIENT_ID/SECRET (optional) |
| YouTube | Native RSS | None |
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
