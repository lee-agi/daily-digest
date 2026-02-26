# Daily Digest - Multi-Source Daily Information Aggregator

## Version History
- v0.12.0 (2026-02-26): Add Product Hunt data source
  - New collector: `collectors/producthunt_collector.py` — ProductHuntCollector via official GraphQL API
  - Fetches top 10 products by vote count using Pacific Time (America/Los_Angeles) day boundaries
  - Bearer Token auth via `PRODUCTHUNT_API_TOKEN` environment variable
  - `votesCount` mapped to `score`; topics mapped to `tags`; extra fields: tagline, thumbnail, ph_id
  - 18 data sources total; 27 unit tests + 2 integration tests (skipped without token)
- v0.11.0 (2026-02-24): Fix xiaoyuzhou/apple_podcast config mismatch
  - Refactored `XiaoyuzhouCollector` from `RSSHubCollector` subclass to `BaseCollector` subclass
  - Now reads `feeds` list (direct xyzfm.space RSS URLs) instead of `podcast_ids` via RSSHub
  - Moved 13 xyzfm.space feeds from `apple_podcast.feeds` to `xiaoyuzhou.feeds` in config.yaml
  - `apple_podcast` now only contains 5 non-Xiaoyuzhou feeds (晚点聊, 科技早知道, Lex Fridman, AI炼金术, 硅谷101)
  - `xiaoyuzhou` re-enabled (true), `lookback_hours` extended to 168h (weekly window for podcasts)
  - Added `TestXiaoyuzhouCollector` (4 unit tests); fixed missing `zhihu`/`cn_tech_blog` imports in test_collectors.py
- v0.10.0 (2026-02-24): Replace Zhihu RSSHub with direct API collector
  - New collector: `zhihu_collector.py` — ZhihuCliCollector using direct Zhihu API
  - Supports 3 feed types: hot, recommend, follow (configurable via `feed_types`)
  - Reuses zhihu-cli cookies from `~/.zhihu-cli/cookies.json`
  - Cross-feed URL dedup, heat score parsing ("万热度"), ad filtering
  - Removed RSSHub dependency for Zhihu (no longer needs localhost:1200 for zhihu)
  - Deleted old `ZhihuCollector` from `rsshub_collector.py` and related tests
  - 34 new tests (31 unit + 3 integration), all passing
- v0.9.0 (2026-02-23): Add 4 new data sources (机器之心, 量子位, AI洞察日报, 宝玉博客)
  - New collector: `cn_tech_blog.py` — multi-feed RSS for 3 Chinese tech/AI blog sources
  - Feeds: Wechat-Scholar GitHub RSS (机器之心 gh_dbc0a5474692, 量子位 gh_114e76fd6e5d) + CloudFlare AI Insight Daily
  - New RSSHub subclass: `BaoyuBlogCollector` — 宝玉博客 via RSSHub /baoyu/blog
  - Cross-feed URL dedup, 168h lookback window, 30+20 max_items
  - 17 data sources total, 140 tests passing
- v0.8.0 (2026-02-23): Add reviewer rating-based quality filter for OpenReview papers
  - New static method: `_extract_avg_rating()` — extracts avg rating from Official_Review replies
  - Supports OpenReview API v2 `invitations` (list) and v1 `invitation` (string) formats
  - Rating value parsing: dict `{"value": 8}`, direct int `8`, string `"8: Strong Accept"`
  - `_fetch_openreview_orals()`: `details=replies` param, limit=25 with 0.5s rate-limit
  - `_parse_openreview_note()`: populates `score`, `extra.avg_rating`, `extra.num_reviews`, `extra.individual_ratings`
  - Configurable `min_avg_rating` per conference in `config.yaml` (ICLR: 7, NeurIPS: 5, ICML: 5)
  - Papers without public reviews (`avg_rating=None`) pass through filter
  - 36 tests passing (9 new unit tests + 5 new API tests + 22 existing)
- v0.7.0 (2026-02-23): Fix remaining issues — RSSHub proxy, YouTube cleanup, RSS link
  - Fixed RSSHub proxy bypass: `AsyncHTTPTransport()` replaces `proxy=None` (httpx 0.28 + socks issue)
  - Zhihu collector now works: 200 OK, 3-4 items/run (score_threshold lowered to 0)
  - Removed 3 dead YouTube channels (Wong Snooker Coach, 象棋徐教头, 世界趣闻), 42 active
  - Fixed RSS Worker push `link` field: unique per-digest URL for proper RSS reader display
  - Fixed `CollectorRegistry.create_all` ad_filter compatibility with legacy collectors
  - Reddit API: kept public API mode (Responsible Builder Policy blocks app registration)
  - 12 active sources, 115 tests passing
- v0.6.0 (2026-02-23): Add ad content detection and filtering module
  - New module: `aggregator/ad_filter.py` — rule-based three-layer ad filter
  - Layer 1: Keyword matching (EN/CN) on title + content with regex patterns
  - Layer 2: URL domain blocklist (affiliate links, URL shorteners, e-commerce)
  - Layer 3: Per-source rules (Reddit upvote_ratio, YouTube #ad/#sponsored tags)
  - Integrated into `BaseCollector.run()` between score filtering and max_items truncation
  - Configurable via `config.yaml` `ad_filter` section (enabled flag, custom keywords, per-source overrides)
  - `CollectorRegistry.create_all()` auto-creates shared AdFilter instance from config
- v0.5.0 (2026-02-23): Implement AAAI/CVPR/ACL Best Paper website scraping
  - AAAI: single-page multi-year awards scraping (Outstanding Paper, track awards, demos, posters)
  - CVPR: per-year BestPapersDemos page (Best Paper, Best Student Paper, Honorable Mentions)
  - ACL: per-year best_papers page (Best Paper, Social Impact, Resource, Theme, Outstanding, SAC)
  - IJCAI: remains stub (no reliable best paper page)
  - Graceful 404 handling for future conference years not yet published
  - `_fetch_website_awards` refactored from stub to dispatcher pattern
- v0.4.0 (2026-02-23): Add CCF Best Paper Collector
  - New collector: `ccf_bestpaper` — monitors CCF-A AI conferences + ICLR
  - Uses ccf-deadlines YAML for conference metadata, OpenReview API for oral papers
  - Covers NeurIPS, ICML, ICLR via OpenReview; AAAI/CVPR/IJCAI/ACL as stubs
  - 15 data sources total
- v0.3.0 (2026-02-23): End-to-end verification & production deployment
  - Fixed HuggingFace date parsing: use `submittedOnDailyAt` instead of `publishedAt`
  - Fixed coolpaper collector: rewrite to scrape `/arxiv/cs.AI` etc. category pages
  - Fixed RSSHub collector proxy bypass (`proxy=None` for localhost)
  - Fixed Azure OpenAI env var compatibility (`AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT`)
  - Added YouTube channel subscriptions (45 channels) and podcast feeds (18 feeds)
  - Disabled stub sources (x_twitter, weread, jike, xiaoyuzhou)
  - Created wrapper scripts for launchd (scripts/run-collect.sh, run-summarize.sh)
  - Installed launchd plist for automated 06:30 CST collection
  - 10 active sources, 80 items per run, LLM summary + RSS Worker push verified
- v0.2.0 (2026-02-23): Add 3 blog collectors (Anthropic, OpenAI, Google Blog) - 14 data sources total
- v0.1.0 (2026-02-23): Initial implementation - 11 data sources, pluggable collector architecture, SQLite dedup, LLM summary via Opus, RSS Worker + Feishu distribution

## Architecture

### Overview
Multi-source daily digest pipeline that collects content from 17 platforms, deduplicates, filters by configurable thresholds, generates LLM-powered summaries, and distributes via RSS and Feishu.

### Key Components
- **orchestrator.py**: Entry point with `--collect-only`, `--summarize-and-push`, `--full` modes
- **collectors/base.py**: `BaseCollector` ABC + `CollectorRegistry` with auto-registration via `__init_subclass__`
- **schema.py**: Pydantic models (`ContentItem`, `CollectorResult`, `DigestReport`, `RunRecord`)
- **state.py**: SQLite-based dedup (URL hash, ArXiv ID) and run history
- **aggregator/**: Cross-source dedup, score filtering, ad filtering, topic categorization
- **report/**: LLM summary generation (Opus via Azure OpenAI) and distribution (RSS Worker, Feishu)

### Data Sources (18)
| Source | Collector | Type |
|--------|-----------|------|
| X/Twitter | `x_twitter.py` | Browser Relay |
| GitHub | `github_collector.py` | REST API |
| Reddit | `reddit_collector.py` | OAuth2/Public API |
| YouTube | `youtube_collector.py` | Native RSS |
| 知乎 | `zhihu_collector.py` (ZhihuCliCollector) | Direct API |
| 即刻 | `rsshub_collector.py` (JikeCollector) | RSSHub |
| 小宇宙 | `rsshub_collector.py` (XiaoyuzhouCollector) | RSSHub |
| HuggingFace | `huggingface_papers.py` | HTTP API |
| papers.cool | `coolpaper_collector.py` | HTTP Scrape |
| Apple Podcast | `apple_podcast.py` | Native RSS |
| WeRead | `weread_collector.py` | Browser Relay |
| Anthropic Blog | `anthropic_blog.py` | HTTP Scrape |
| OpenAI Blog | `openai_blog.py` | Native RSS |
| Google Blog | `google_blog.py` | Native RSS |
| CCF Best Paper | `ccf_bestpaper.py` | OpenReview API + YAML |
| 机器之心/量子位/AI洞察日报 | `cn_tech_blog.py` | RSS (GitHub/CloudFlare) |
| 宝玉博客 | `rsshub_collector.py` (BaoyuBlogCollector) | RSSHub |
| Product Hunt | `producthunt_collector.py` | GraphQL API |

### Adding a New Data Source
1. Create `collectors/my_source.py` with a class that:
   - Inherits `BaseCollector`
   - Sets `source_name = "my_source"` and `source_type`
   - Implements `async def collect() -> list[ContentItem]`
2. Add config section in `config.yaml` under `sources.my_source`
3. The collector auto-registers via `__init_subclass__` — no other changes needed

### Triggering
- **launchd** (06:30 CST): `scripts/run-collect.sh` → `orchestrator.py --collect-only`
- **OpenClaw cron** (07:00 CST): `scripts/run-summarize.sh` → `orchestrator.py --summarize-and-push`
- Wrapper scripts handle env var loading (.env + ~/.bashrc Azure vars)

### Active Sources (as of v0.12.0)
| Source | Status | Notes |
|--------|--------|-------|
| github | ✅ Active | 30 items/run, PAT in .env |
| reddit | ✅ Active | 13 items/run, public API (OAuth blocked by policy) |
| huggingface | ✅ Active | Uses submittedOnDailyAt |
| coolpaper | ✅ Active | Scrapes cs.AI/CL/LG categories |
| youtube | ✅ Active | 42 channels (3 stale removed) |
| zhihu | ✅ Active | Direct API (hot/recommend/follow), zhihu-cli cookies |
| anthropic | ✅ Active | Blog scraping with RSC fallback |
| openai | ✅ Active | RSS feed |
| google_blog | ✅ Active | Multi-feed RSS |
| apple_podcast | ✅ Active | 18 podcast feeds |
| ccf_bestpaper | ✅ Active | OpenReview oral papers + AAAI/CVPR/ACL scraping |
| cn_tech_blog | ✅ Active | 机器之心 + 量子位 + AI洞察日报 RSS, 30 items/run |
| baoyu_blog | ✅ Active | 宝玉博客 via RSSHub /baoyu/blog, 8 items/run |
| x_twitter | ❌ Disabled | Needs browser relay |
| weread | ❌ Disabled | Needs browser relay |
| jike | ❌ Disabled | No user IDs configured |
| xiaoyuzhou | ✅ Active | Direct RSS via xyzfm.space, 13 feeds, 168h window |
| producthunt | ✅ Active | Top 10 products by votes, PST day boundaries, PRODUCTHUNT_API_TOKEN |

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
- `ad_filter`: Ad content filtering (see below)

### Ad Filter (`config.yaml` → `ad_filter`)
- `enabled`: Toggle ad filtering on/off (default: true)
- `title_keywords` / `content_keywords`: Extra regex patterns appended to built-in defaults
- `url_blocklist`: Extra URL patterns to block
- `per_source.<source>`: Source-specific overrides
  - `title_keywords`: Extra title keyword patterns
  - `min_upvote_ratio` (Reddit): Block posts below this ratio (default: 0.4)

## Dependencies
- Python 3.11+ with venv at `.venv/`
- RSSHub Docker instance at `localhost:1200`
- Azure OpenAI API (for LLM summarization)
- RSS Worker at Cloudflare Workers
