# Daily Digest - Multi-Source Daily Information Aggregator

## Version History
- v0.2.0 (2026-02-23): Add 3 blog collectors (Anthropic, OpenAI, Google Blog) - 14 data sources total
- v0.1.0 (2026-02-23): Initial implementation - 11 data sources, pluggable collector architecture, SQLite dedup, LLM summary via Opus, RSS Worker + Feishu distribution

## Architecture

### Overview
Multi-source daily digest pipeline that collects content from 14 platforms, deduplicates, filters by configurable thresholds, generates LLM-powered summaries, and distributes via RSS and Feishu.

### Key Components
- **orchestrator.py**: Entry point with `--collect-only`, `--summarize-and-push`, `--full` modes
- **collectors/base.py**: `BaseCollector` ABC + `CollectorRegistry` with auto-registration via `__init_subclass__`
- **schema.py**: Pydantic models (`ContentItem`, `CollectorResult`, `DigestReport`, `RunRecord`)
- **state.py**: SQLite-based dedup (URL hash, ArXiv ID) and run history
- **aggregator/**: Cross-source dedup, score filtering, topic categorization
- **report/**: LLM summary generation (Opus via Azure OpenAI) and distribution (RSS Worker, Feishu)

### Data Sources (14)
| Source | Collector | Type |
|--------|-----------|------|
| X/Twitter | `x_twitter.py` | Browser Relay |
| GitHub | `github_collector.py` | REST API |
| Reddit | `reddit_collector.py` | OAuth2/Public API |
| YouTube | `youtube_collector.py` | Native RSS |
| 知乎 | `rsshub_collector.py` (ZhihuCollector) | RSSHub |
| 即刻 | `rsshub_collector.py` (JikeCollector) | RSSHub |
| 小宇宙 | `rsshub_collector.py` (XiaoyuzhouCollector) | RSSHub |
| HuggingFace | `huggingface_papers.py` | HTTP API |
| papers.cool | `coolpaper_collector.py` | HTTP Scrape |
| Apple Podcast | `apple_podcast.py` | Native RSS |
| WeRead | `weread_collector.py` | Browser Relay |
| Anthropic Blog | `anthropic_blog.py` | HTTP Scrape |
| OpenAI Blog | `openai_blog.py` | Native RSS |
| Google Blog | `google_blog.py` | Native RSS |

### Adding a New Data Source
1. Create `collectors/my_source.py` with a class that:
   - Inherits `BaseCollector`
   - Sets `source_name = "my_source"` and `source_type`
   - Implements `async def collect() -> list[ContentItem]`
2. Add config section in `config.yaml` under `sources.my_source`
3. The collector auto-registers via `__init_subclass__` — no other changes needed

### Triggering
- **launchd** (06:30 CST): `orchestrator.py --collect-only` — data collection
- **OpenClaw cron** (07:00 CST): `orchestrator.py --summarize-and-push` — LLM + distribution

### Testing
```bash
# Run all tests
.venv/bin/python -m pytest tests/ -v

# Dry run (collect + summarize, no push)
.venv/bin/python orchestrator.py --full --dry-run

# Single source test
.venv/bin/python orchestrator.py --collect-only --source huggingface
```

## Config
All filter thresholds are configurable per source in `config.yaml`:
- `score_threshold`: Minimum engagement score
- `lookback_hours`: Time window (default 24h)
- `max_items`: Max items per source

## Dependencies
- Python 3.11+ with venv at `.venv/`
- RSSHub Docker instance at `localhost:1200`
- Azure OpenAI API (for LLM summarization)
- RSS Worker at Cloudflare Workers
