---
name: daily-digest-config
description: Environment variables and config.yaml settings for daily-digest
---

# 配置说明

## 环境变量（from `~/.secrets` or `.env`）
| 变量 | 用途 | 默认值 |
|------|------|--------|
| `GITHUB_TOKEN` | GitHub REST API PAT | 必填 |
| `TWITTER_API_IO_KEY` | TwitterAPI.io 主路径 | 必填（X 采集）|
| `X_AUTH_TOKEN` | twikit fallback cookie | 可选 |
| `X_CT0` | twikit fallback cookie | 可选 |
| `YOUTUBE_DATA_API_KEY` | YouTube Data API v3 | 必填（YouTube 采集）|
| `PRODUCTHUNT_API_TOKEN` | Product Hunt GraphQL Bearer | 必填（PH 采集）|
| `RSS_API_KEY` | Cloudflare RSS Worker API key | 必填（推送）|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | 必填（摘要）|
| `AZURE_OPENAI_DEPLOYMENT` | 模型部署名 | 必填（摘要）|
| `AZURE_OPENAI_API_KEY` | Azure API key | 必填（摘要）|
| `REDDIT_CLIENT_ID` | Reddit OAuth（当前未用，走 public API）| 可选 |
| `REDDIT_CLIENT_SECRET` | Reddit OAuth | 可选 |
| Feishu webhook | 飞书推送 URL | `distribution.feishu` in config.yaml |

## config.yaml 关键字段
| 字段路径 | 用途 | 默认值 |
|---------|------|--------|
| `general.lookback_hours` | 全局回看时间窗口 | 24h |
| `general.output_dir` | digest 输出目录 | `~/Developer/research/daily-digest` |
| `general.intermediate_dir` | 中间 JSON 目录 | `./data` |
| `sources.<name>.enabled` | 启用/禁用该 source | - |
| `sources.<name>.score_threshold` | 最低 score 过滤 | 视 source 而定 |
| `sources.<name>.lookback_hours` | 覆盖全局回看窗口 | 继承 general |
| `sources.<name>.max_items` | 每 source 最大条目数 | 100 |
| `enrichment.enabled` | 启用 any2summary 富化 | true |
| `enrichment.timeout_seconds` | any2summary 超时 | 180s |
| `enrichment.article_auto_threshold` | content < N chars 自动富化 | 5000 |
| `enrichment.content_max_chars` | ContentItem.content 截断 | 3000 |
| `ad_filter.enabled` | 广告过滤开关 | true |
| `summary.model` | LLM 摘要模型 | opus |
| `summary.top_headlines` | 头条数量 | 10 |
| `distribution.rss_worker.enabled` | RSS Worker 推送开关 | true |
| `distribution.feishu.enabled` | 飞书推送开关 | true |

## 配置优先级
1. CLI 参数（`--lookback-hours N` 覆盖所有 source）
2. `config.yaml` per-source 配置（`sources.<name>.lookback_hours`）
3. `config.yaml` `general.lookback_hours` 全局默认
