---
name: daily-digest-module-collectors
description: collectors/ — 19 个平台的数据采集器，BaseCollector ABC + auto-registration
---

# collectors/

**用途：** 19 个平台的数据采集器，统一 `ContentItem` 输出格式；通过 `__init_subclass__` 自动注册，无需手动配置。（~4,800 LOC）

## 关键文件
| 文件 | 说明 |
|------|------|
| `base.py` | BaseCollector ABC、CollectorRegistry、`_request_with_retry()` 共享重试 |
| `enricher.py` | ContentEnricher：any2summary 调用 + 熔断器 + engagement 评分 |
| `manual_url_collector.py` | 读 `data/pending_urls.yaml` + Mac Reminders T5T list |
| `zhihu_collector.py` | ZhihuCliCollector：hot/recommend/follow 三类 feed，cookie auth |
| `ccf_bestpaper.py` | OpenReview API v1/v2 + AAAI/CVPR/ACL 网页 scraping |
| `x_twitter.py` | TwitterAPI.io primary + twikit fallback |
| `youtube_collector.py` | YouTube Data API v3 `playlistItems.list` |
| `rsshub_collector.py` | RSSHub 基类 + Jike/Xiaoyuzhou/BaoyuBlog 子类 |

## 核心抽象
- `BaseCollector` (`base.py:20`) — ABC，子类设 `source_name` + 实现 `async collect()`
- `CollectorRegistry` (`base.py:24`) — `_registry` dict，`create_all()` 批量实例化
- `BaseCollector.run()` (`base.py:100`) — 模板方法：collect → score filter → ad filter → max_items
- `BaseCollector._request_with_retry()` (`base.py:150`) — 指数退避重试（5xx/429/timeout），max_retries=3
- `ContentItem` (`schema.py:30`) — 全源通用 schema：title/url/content/score/published_at/extra
- `ContentEnricher.enrich_batch()` (`enricher.py:120`) — 分批 enrich articles/youtube/podcasts
- `ContentEnricher.process_batch()` (`enricher.py:200`) — 含熔断器，连续3次失败跳过该类别
- `ZhihuCliCollector.collect()` (`zhihu_collector.py:50`) — cookie auth，热榜+推荐+关注三路 feed
- `XiaoyuzhouCollector` (`rsshub_collector.py:180`) — 继承 BaseCollector，直接解析 xyzfm.space RSS

## 开发指引
1. 新增 collector：创建 `collectors/my_source.py`，继承 `BaseCollector`，实现 `async collect()`
2. 在 `collectors/__init__.py` 添加 import（必须，否则 auto-registration 不触发）
3. 在 `config.yaml` `sources:` 添加同名配置块
4. 测试：`tests/test_my_source.py`，用 `pytest-httpx` mock HTTP
5. 常见陷阱：`_request_with_retry()` 默认跟随 httpx client 代理设置；RSSHub 本地 source 需跳过代理（`AsyncHTTPTransport()` 替代 `proxy=None`）

## ⚠️ 已知问题
- enricher any2summary subprocess 可能 hang（180s 外层超时保护中）；`enrichment.enabled: true` 生产有风险
- WeRead collector 禁用（需改用 obsidian-weread-plugin REST API 重建）
- Jike 无 official API，长期禁用
