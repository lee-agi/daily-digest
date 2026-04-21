# Daily Digest - Multi-Source Daily Information Aggregator

## Version History
- v0.18.1 (2026-03-01): Pipeline 分步执行 + Enrichment 优化 + LLM Refusal 防护
  - **Pipeline 拆分**: `--no-enrich` 跳过 enrichment，`--enrich-only` 独立运行 enrichment
  - `scripts/run.sh` 默认传 `--no-enrich`，cron 主 pipeline 不被 enrichment 阻塞
  - `run_enrich_only()`: 从 collected JSON 加载，过滤可 enrich 源，输出 `enriched-{date}-{HHMM}.json`
  - **Enrichment 优化**: per-type timeout (article=30s, youtube=600s, podcast=180s)
  - Podcast 优先使用 `extra.audio_url` (enclosure href) 而非网页 URL
  - 排除中文源 (`cn_tech_blog`, `baoyu_blog`) 的 article enrichment
  - **LLM Refusal 检测**: `_is_refusal()` 检测短文本 refusal，触发模型降级
  - **Fallback 报告结构化**: LLM 不可用时输出按分类的原始数据+链接（替代空模板）
  - **Streaming 渐进降级**: attempt 1-2 streaming → attempt 3 non-streaming (绕过 Azure 代理层 TTFT 空闲超时)
  - `_ATTEMPT_CONFIGS`: 逐次增加 read timeout (600→900s)，第3次切非流式
  - 新增 19 个测试 (generator: 9, enricher: 10, orchestrator: 2)，383 tests passed
- v0.18.0 (2026-03-01): LLM 模型降级链 + 网络重试扩展 + enrichment 超时调整
  - **模型降级链**: `llab-gpt-5.2-codex` (3次) → `llab-gpt-5-mini` (2次) → fallback 模板
  - 重构 `_call_llm()` 为调度层 + `_call_llm_with_model()` 单模型尝试逻辑
  - `_request_with_retry()` 从 `httpx.TimeoutException` 扩展为 `httpx.TransportError` (覆盖 RemoteProtocolError/ConnectError/ReadError)
  - Enrichment subprocess 超时 180s → 300s (`config.yaml` + `enricher.py` 默认值)
  - YouTube cookies 缺失时 log warning 提示
  - SSE 解析器增加 debug 级别逐行日志
  - 新增 7 个测试 (generator: 3, base_collector: 4)，71 tests passed
- v0.17.1 (2026-03-01): 本地文件名加时间戳 + `--lookback-hours` CLI 参数 + `/digest-run` skill
  - `collected-{date}.json` → `collected-{date}-{HHMM}.json`，同日多次执行不覆盖
  - `digest-{date}.md` → `digest-{date}-{HHMM}.md`，使用 `report.generated_at` 北京时间
  - `run_summarize_and_push()` 加载中间 JSON 改为 glob 最新匹配（按 mtime 排序）
  - 新增 `--lookback-hours N` CLI 参数：运行时覆盖所有 source 的 `lookback_hours`
  - 新增 `/digest-run` Claude Code skill（`.claude/commands/digest-run.md`）
  - 2 个新测试（lookback_hours override/preserve），9 tests passed
- v0.17.0 (2026-03-01): 修复 any2summary Responses API 路径 + enricher 熔断器，重新启用 enrichment
  - 根因修复（any2summary v1.6.2）：`_call_responses_api()` 用 raw httpx 替换 OpenAI SDK，修复 URL/auth/proxy 三重 bug
  - enricher 熔断器：`process_batch()` 每个类别（articles/youtube/podcasts）连续 3 次失败后跳过剩余 items
  - `config.yaml` enrichment 重新启用（`enabled: true`）
  - 新增 2 个测试（circuit_breaker_skips + circuit_breaker_resets）
  - 超时链：subprocess 180s 外层安全网 → httpx 600s/30s 内层 → 正常 30-120s 完成
- v0.16.1 (2026-02-28): 统一脚本入口，修复两阶段调度里的 seen 标记问题
  - 合并 `run-collect.sh` + `run-summarize.sh` → `scripts/run.sh`（支持 `--collect-only` / `--summarize-and-push` / `--full`）
  - launchd 调度：06:30 `--collect-only`，07:00 `--summarize-and-push`
  - 禁用 enrichment（any2summary subprocess hang）
  - `mark_seen()` 从 `run_collect()` 移至 `run_summarize_and_push()` push 成功后，dry-run 不标记 seen
  - 根因：旧实现里 collect 阶段会提前标记 seen，导致 summary 阶段再次 collect 时仅获取 ~8 items
  - 350 tests passed, 3 skipped
- v0.16.0 (2026-02-28): 全局重试机制 + 三源分页修复
  - `BaseCollector._request_with_retry()`: 共享指数退避重试（5xx/429/timeout），max_retries=3, base_delay=1s
  - 全量迁移：17 个 collector 的 `client.get/post()` → `self._request_with_retry()`
  - Zhihu 分页：recommend `page_number` 翻页（最多5页），follow `paging.next` URL 翻页
  - Reddit 分页：OAuth/public 路径 `after` cursor 分页（最多3页）
  - GitHub 分页：trending 搜索 `page` 参数分页（最多3页，30条/页）
  - 新增测试：`test_base_collector.py`(5), `test_zhihu_collector.py`(4), `test_reddit_collector.py`(2), `test_github_collector.py`(2)
  - 347 tests passed, 3 skipped
- v0.15.0 (2026-02-28): Fix collector failures — YouTube RSS→API, wrapper script secrets, RSSHub reachability
  - YouTube collector rewritten: RSS feeds (404) → Data API v3 `playlistItems.list`; `YOUTUBE_DATA_API_KEY` now required
  - Wrapper scripts (`run-collect.sh`, `run-summarize.sh`): `source ~/.secrets` for API tokens (root cause fix)
  - RSSHub collector: quick reachability probe (HEAD, 3s timeout) before fetching feeds
  - Jike disabled in config (`user_ids: []` empty, no official API)
  - Orchestrator: `_check_credentials()` logs env var status at collect start
  - Test fixes: timeout assertion 120→600s, AAAI network skip
  - 16 active sources, 327 tests (311 passed + 3 skipped + network-dependent)
- v0.14.0 (2026-02-28): Content enrichment via any2summary + Manual URL injection + Mac Reminders T5T
  - New module: `collectors/enricher.py` — ContentEnricher calls any2summary CLI for full text/transcripts
  - New collector: `collectors/manual_url_collector.py` — ManualURLCollector reads `data/pending_urls.yaml` + Mac Reminders T5T list
  - YouTube Data API v3 integration: `_fetch_video_stats_batch()` for engagement rate scoring (optional `YOUTUBE_DATA_API_KEY`)
  - Enrichment quality gates: articles (<5k chars auto), YouTube (top-N by engagement), podcasts (>30min → manual suggestion)
  - `--inject-url URL` CLI flag for one-off URL injection into pending_urls.yaml
  - Apple Podcast/Xiaoyuzhou: parse enclosure file_size_bytes + duration for enrichment decisions
  - `config.yaml` enrichment section with per-source top-N limits
  - 50 new tests (36 enricher + 14 manual URL), all passing
  - 19 data sources total
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
Multi-source daily digest pipeline that collects content from 19 platforms, deduplicates, filters by configurable thresholds, enriches high-quality items via any2summary, generates LLM-powered summaries, and distributes via RSS and Feishu.

### Key Components
- **orchestrator.py**: Entry point with `--collect-only`, `--summarize-and-push`, `--full`, `--enrich-only`, `--no-enrich`, `--inject-url` modes
- **collectors/base.py**: `BaseCollector` ABC + `CollectorRegistry` with auto-registration via `__init_subclass__`; `_request_with_retry()` shared exponential backoff retry (5xx/429/timeout)
- **collectors/enricher.py**: Content enrichment via any2summary (articles, YouTube, podcasts)
- **collectors/manual_url_collector.py**: Manual URL injection + Mac Reminders T5T integration
- **schema.py**: Pydantic models (`ContentItem`, `CollectorResult`, `DigestReport`, `RunRecord`)
- **state.py**: SQLite-based dedup (URL hash, ArXiv ID) and run history
- **aggregator/**: Cross-source dedup, score filtering, ad filtering, topic categorization
- **report/**: LLM summary generation (Opus via Azure OpenAI) and distribution (RSS Worker, Feishu)

### Data Sources (19)
| Source | Collector | Type |
|--------|-----------|------|
| Manual URLs | `manual_url_collector.py` | HTTP Scrape + any2summary |
| X/Twitter | `x_twitter.py` | API (TwitterAPI.io + twikit) |
| GitHub | `github_collector.py` | REST API |
| Reddit | `reddit_collector.py` | OAuth2/Public API |
| YouTube | `youtube_collector.py` | Data API v3 |
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
- **launchd collect** (06:30 CST): `scripts/run.sh --collect-only`
- **launchd summarize** (07:00 CST): `scripts/run.sh --summarize-and-push`
- Unified wrapper script handles env var loading (.env + ~/.secrets + ~/.bashrc Azure vars)
- Usage: `scripts/run.sh [--collect-only | --summarize-and-push | --full] [extra args...]` (default: `--full --no-enrich`)
- **Enrichment 单独执行**: `python orchestrator.py --enrich-only` (加载最新 collected JSON，仅 enrich 英文 Articles/YouTube/Podcast)

### Active Sources (as of v0.16.0)
| Source | Status | Notes |
|--------|--------|-------|
| manual_urls | ✅ Active | pending_urls.yaml + Mac Reminders T5T, enriched via any2summary |
| github | ✅ Active | 30 items/run, PAT in .env |
| reddit | ✅ Active | 13 items/run, public API (OAuth blocked by policy) |
| huggingface | ✅ Active | Uses submittedOnDailyAt |
| coolpaper | ✅ Active | Scrapes cs.AI/CL/LG categories |
| youtube | ✅ Active | 27 channels, Data API v3 playlistItems (YOUTUBE_DATA_API_KEY required) |
| zhihu | ✅ Active | Direct API (hot/recommend/follow), zhihu-cli cookies |
| anthropic | ✅ Active | Blog scraping with RSC fallback |
| openai | ✅ Active | RSS feed |
| google_blog | ✅ Active | Multi-feed RSS |
| apple_podcast | ✅ Active | 5 podcast feeds, enclosure file_size_bytes |
| ccf_bestpaper | ✅ Active | OpenReview oral papers + AAAI/CVPR/ACL scraping |
| cn_tech_blog | ✅ Active | 机器之心 + 量子位 + AI洞察日报 RSS, 30 items/run |
| baoyu_blog | ✅ Active | 宝玉博客 via RSSHub /baoyu/blog, 8 items/run (requires RSSHub) |
| x_twitter | ✅ Active | TwitterAPI.io + twikit fallback |
| weread | ❌ Disabled | Needs browser relay |
| jike | ❌ Disabled | No user IDs, no official API |
| xiaoyuzhou | ✅ Active | Direct RSS via xyzfm.space, 13 feeds, enclosure file_size_bytes |
| producthunt | ✅ Active | Top 10 products by votes, PST day boundaries, PRODUCTHUNT_API_TOKEN |

### Testing
```bash
# Run all tests
.venv/bin/python -m pytest tests/ -v

# Dry run (collect + summarize, no push, no enrichment)
.venv/bin/python orchestrator.py --full --dry-run --no-enrich

# Dry run with custom lookback window (48 hours)
.venv/bin/python orchestrator.py --full --dry-run --lookback-hours 48

# Single source test
.venv/bin/python orchestrator.py --collect-only --source huggingface

# Inject URL and collect
.venv/bin/python orchestrator.py --collect-only --inject-url "https://example.com/article" --source manual_urls

# Standalone enrichment (on latest collected data)
.venv/bin/python orchestrator.py --enrich-only
```

### Claude Code Skill
- `/digest-run [N] [PUSH]` — 重跑 digest pipeline，N=lookback hours（默认24），PUSH=0/1（默认0=dry-run）

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

### Content Enrichment (`config.yaml` → `enrichment`)
- `enabled`: Toggle enrichment on/off (enabled since v0.17.0)
- `timeout_seconds`: any2summary subprocess timeout (default: 180)
- `large_download_minutes`: Threshold for "manual suggestion" podcasts (default: 30)
- `article_auto_threshold`: Articles with content < this many chars are auto-enriched (default: 5000)
- `content_max_chars`: Truncation limit for enriched ContentItem.content (default: 3000)
- `any2summary_dir`: Path to any2summary installation
- `sources.youtube.top_n`: Max YouTube videos to enrich per run
- `sources.apple_podcast.top_n` / `sources.xiaoyuzhou.top_n`: Max podcast episodes
- `sources.articles.top_n`: Max article items (beyond auto-threshold)

### Manual URL Injection (`config.yaml` → `sources.manual_urls`)
- `file`: Path to `data/pending_urls.yaml`
- `reminders_list`: macOS Reminders list name (default: "T5T")
- `reminders_enabled`: Auto-import URLs from Reminders (default: true)
- CLI: `--inject-url URL` flag (repeatable) appends to pending_urls.yaml

## Dependencies
- Python 3.11+ with venv at `.venv/`
- RSSHub Docker instance at `localhost:1200`
- Azure OpenAI API (for LLM summarization)
- RSS Worker at Cloudflare Workers
- any2summary at `~/Documents/Code/any2summary` (for content enrichment)
- `YOUTUBE_DATA_API_KEY` (required for YouTube collector since v0.15.0)
