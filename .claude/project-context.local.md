---
# Auto-detected context
detected_type: Python CLI Pipeline
tech_stack: Python 3.11, httpx, pydantic, feedparser, beautifulsoup4, sqlite3, pytest-asyncio
frameworks: asyncio, pydantic v2, pytest
project_name: daily-digest
version: 0.17.1
total_loc: 11995

# User-provided context
business_domain: Personal information aggregation & daily digest
target_users: Personal use (single user)
development_stage: Growth (actively adding sources, debugging enrichment)
team_size: Solo

generated: 2026-04-12T16:30:00+08:00
---

# Project Context: daily-digest

## Auto-Detected Information

### Technology Stack
- **Runtime**: Python 3.11+, venv at `.venv/`
- **HTTP**: httpx[socks] (async, with retry wrapper)
- **Data Models**: Pydantic v2 (`ContentItem`, `CollectorResult`, `DigestReport`, `RunRecord`)
- **Storage**: SQLite (`state.db`) via stdlib `sqlite3`
- **Feed Parsing**: feedparser, BeautifulSoup4, lxml
- **Testing**: pytest + pytest-asyncio + pytest-httpx
- **LLM**: Azure OpenAI (Claude Opus via Azure) for summarization
- **External Tools**: any2summary CLI for content enrichment, RSSHub Docker for some feeds

### Architecture
Pipeline pattern: Collect → Dedup → Filter → Enrich → Generate → Distribute
- Entry point: `orchestrator.py` with `--collect-only / --summarize-and-push / --full` modes
- 19 data sources via pluggable collector architecture (auto-registration via `__init_subclass__`)
- SQLite dedup prevents re-processing seen items; URL hash + ArXiv ID used as keys
- LLM summary via Azure OpenAI Opus, SSE streaming
- Distribution: Cloudflare RSS Worker + Feishu webhook

### Code Statistics
| Module | Files | LOC |
|--------|-------|-----|
| collectors/ | 19 files | ~4,800 |
| tests/ | 22 files | ~4,600 |
| aggregator/ | 4 files | ~560 |
| report/ | 3 files | ~740 |
| orchestrator.py + schema.py + state.py | 3 files | ~800 |

## User-Provided Context

### Business Context
- **Personal daily information digest** — aggregates 19 sources into a single Markdown report
- Primary consumption: RSS reader (Cloudflare Worker RSS feed)
- Secondary: Feishu channel notification
- Runs daily at 07:00 CST via OpenClaw cron → `scripts/run.sh --full`

### Development Process
- Solo developer, feature-per-session workflow
- WeRead collector planned rewrite using obsidian-weread-plugin API pattern
- Jike stays disabled (no official API)
- Enrichment (any2summary) still being debugged — circuit breaker in place

### Known Pain Points
1. **Collector reliability** — some collectors fail silently or intermittently
2. **Enrichment performance** — any2summary subprocess can hang; 180s timeout + circuit breaker mitigates but doesn't fully resolve

## Key Insights

### Strengths
- Clean pluggable architecture: adding a new collector requires only 1 file + 1 config block
- Shared retry wrapper (`_request_with_retry`) across all collectors reduces boilerplate
- Comprehensive test suite (~350 tests) with both unit and integration variants
- Circuit breaker in enricher prevents total pipeline failure

### Areas of Complexity
- `ccf_bestpaper.py` is the most complex collector (734 LOC) — OpenReview API v1/v2 + multi-conference web scraping
- `zhihu_collector.py` (412 LOC) — cookie auth + 3 feed types + pagination
- `enricher.py` (414 LOC) — subprocess management, circuit breaker, engagement scoring
- `report/generator.py` (339 LOC) — SSE streaming, Azure OpenAI retry, Markdown template

### Recommended Focus Areas
1. Stabilize enrichment: investigate any2summary hang root cause (subprocess vs asyncio event loop)
2. WeRead collector: implement based on obsidian-weread-plugin REST API
3. Better per-collector error telemetry (structured failure logs → alert on repeated failures)
