---
name: daily-digest-module-aggregator
description: aggregator/ — 跨源去重、score 过滤、广告过滤、分类分组
---

# aggregator/

**用途：** 将多 collector 的 `ContentItem` 列表合并、去重、过滤广告、按话题分类。（~560 LOC）

## 关键文件
| 文件 | 说明 |
|------|------|
| `dedup.py` | 跨源去重：URL exact > ArXiv ID > 标题相似度 0.85 |
| `filter.py` | score 阈值过滤、按 source 过滤、排序 |
| `merger.py` | 6个 category 分类分组（`CATEGORY_RULES`）|
| `ad_filter.py` | 三层广告过滤：关键词 + URL blocklist + per-source 规则 |

## 核心抽象
- `deduplicate()` (`dedup.py:22`) — 按 score 降序排后去重，优先保留高分版本
- `extract_arxiv_id()` (`dedup.py:14`) — 从 URL/文本提取 ArXiv ID（用于跨源 paper 去重）
- `filter_by_score()` (`filter.py:10`) — score >= threshold 过滤
- `group_by_category()` (`merger.py:35`) — 按 CATEGORY_RULES 映射 source → category
- `categorize_item()` (`merger.py:27`) — 有 arxiv_id 的条目强制归 "Research Papers"
- `AdFilter` (`ad_filter.py:20`) — 三层过滤器，`filter()` 方法返回 bool
- `AdFilter._check_keywords()` — 关键词正则匹配（EN/CN title + content）
- `AdFilter._check_url()` — domain blocklist 检查

## 开发指引
1. 新增 category：在 `merger.py:CATEGORY_RULES` 添加 `"New Category": ["source_name"]`
2. 新增广告关键词：`config.yaml` `ad_filter.title_keywords` 追加（不改代码）
3. 新增 URL blocklist：`config.yaml` `ad_filter.url_blocklist` 追加
4. 调整去重相似度：`deduplicate(items, title_threshold=0.9)` 传参（当前默认 0.85）
5. 常见陷阱：`deduplicate()` 先按 score 排序，再去重 — 相同内容保留分数最高的那条

## ⚠️ 已知问题
- 标题相似度去重（O(n²) SequenceMatcher）在条目数 >1000 时性能下降，当前 ~100 条/run 无影响
