#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'data'
OUT_DIR = ROOT / 'reports' / 'ai' / 'monthly-v2'
CACHE_DIR = ROOT / 'reports' / 'ai' / 'monthly-v2-cache'
CONFIG_PATH = ROOT / 'config.yaml'

EN_STOP = {
    'today', 'headlines', 'summary', 'platform', 'statistics', 'developer', 'tools', 'research',
    'social', 'community', 'books', 'reading', 'the', 'and', 'for', 'with', 'from', 'that', 'this',
    'are', 'was', 'you', 'your', 'data', 'raw', 'unavailable', 'http', 'https', 'www', 'link', 'links',
    'com', 'org', 'net', 'can', 'have', 'has', 'had', 'more', 'most', 'less', 'but', 'not', 'all', 'new',
    'use', 'using', 'used', 'into', 'over', 'under', 'than', 'their', 'them', 'they', 'will', 'would',
    'about', 'after', 'before', 'across', 'through', 'during', 'localllama', 'reddit', 'github', 'zhihu',
    'producthunt', 'anthropic', 'openai', 'google', 'twitter', 'xiaoyuzhou', 'podcast', 'technology',
    'like', 'our', 'now', 'just', 'what', 'amp', 'paper', 'papers', 'post', 'posts', 'thread', 'threads'
}
CN_STOP = {'平台', '统计', '摘要', '主题', '内容', '链接', '本期', '本月', '本周', '来源', '数据', '原始', '继续', '调研', '值得', '核心', '观察', '讨论', '发布'}
GENERIC_TECH = {'model', 'models', 'agent', 'agents', 'code', 'work', 'one', 'out', 'been', 'how', 'tool', 'tools', 'app', 'apps', 'ai', 'pro', 'release', 'preview'}
ENTITY_ALIASES: list[tuple[str, str]] = [
    (r'chatgpt', 'ChatGPT'),
    (r'gpt[- ]?5\.5', 'GPT-5.5'),
    (r'workspace agents?', 'Workspace Agents'),
    (r'claude code', 'Claude Code'),
    (r'claude design', 'Claude Design'),
    (r'claude opus\s*4\.7', 'Claude Opus 4.7'),
    (r'claude opus', 'Claude Opus'),
    (r'managed agents?', 'Managed Agents'),
    (r'codex', 'Codex'),
    (r'ollama', 'Ollama'),
    (r'autogpt', 'AutoGPT'),
    (r'open[- ]webui', 'Open-WebUI'),
    (r'deepseek[- ]?v?4', 'DeepSeek-V4'),
    (r'gemini', 'Gemini'),
    (r'gemma\s*4', 'Gemma 4'),
    (r'gemma', 'Gemma'),
    (r'qwen\s*3\.5', 'Qwen 3.5'),
    (r'qwen', 'Qwen'),
    (r'langchain', 'LangChain'),
    (r'langflow', 'Langflow'),
    (r'transformers?', 'Transformers'),
    (r'kimi\s*cli', 'Kimi CLI'),
    (r'project glasswing', 'Project Glasswing'),
    (r'freecodecamp', 'freeCodeCamp'),
    (r'developer-roadmap|roadmap\.sh', 'developer-roadmap'),
    (r'awesome-selfhosted', 'awesome-selfhosted'),
]
ENTITY_STOP = {'Pro', 'Release', 'Preview', 'Desktop', 'Studio', 'Word', 'Managed', 'Agents'}


def daterange(ref: dt.date, completed_period: bool = False) -> tuple[dt.date, dt.date, str]:
    if completed_period:
        ref = ref.replace(day=1) - dt.timedelta(days=1)
    start = ref.replace(day=1)
    next_month = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    end = next_month - dt.timedelta(days=1)
    key = f'{start.year:04d}-{start.month:02d}'
    return start, end, key


def load_items(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen = set()
    for p in sorted(DATA_DIR.glob('collected-*.json')):
        m = re.search(r'collected-(\d{4}-\d{2}-\d{2})-\d{4}\.json$', p.name)
        if not m:
            continue
        d = dt.date.fromisoformat(m.group(1))
        if not (start <= d <= end):
            continue
        try:
            arr = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        for it in arr:
            key = it.get('url') or f"{it.get('source','')}::{it.get('title','')}"
            if key in seen:
                continue
            seen.add(key)
            items.append(it)
    return items


def trim_text(s: str, n: int) -> str:
    s = re.sub(r'\s+', ' ', (s or '')).strip()
    return s[:n]


def normalize_title(s: str) -> str:
    s = trim_text(s, 200)
    s = re.sub(r'https?://\S+', '', s)
    s = re.sub(r'[`*_>#]', '', s)
    return s.strip().lower()


def keywordize(text: str) -> list[str]:
    toks = re.findall(r'[A-Za-z][A-Za-z0-9.+-]{2,}|[\u4e00-\u9fff]{2,}', text or '')
    out = []
    for t in toks:
        low = t.lower()
        if low in EN_STOP or low in GENERIC_TECH or t in CN_STOP:
            continue
        if low.startswith(('http', 'www', 't.co')) or '.' in low or '/' in low:
            continue
        if re.fullmatch(r'[A-Za-z]\d+', t):
            continue
        out.append(t)
    return out


def extract_entities(text: str) -> list[str]:
    text = (text or '').replace('–', '-').replace('—', '-')
    low_text = text.lower()
    found: list[str] = []
    for pattern, canonical in ENTITY_ALIASES:
        if re.search(pattern, low_text, flags=re.I):
            found.append(canonical)

    candidates = re.findall(r'(?:[A-Z][A-Za-z0-9.+-]{1,}|[A-Z]{2,}[A-Za-z0-9.+-]*)(?:\s+(?:[A-Z][A-Za-z0-9.+-]{1,}|[A-Z]{2,}[A-Za-z0-9.+-]*|Code|Design|CLI|SDK|API|Agents?|Desktop|Studio)){0,2}', text)
    for c in candidates:
        c = re.sub(r'\s+', ' ', c).strip(' -')
        low = c.lower()
        if low in EN_STOP or low in GENERIC_TECH:
            continue
        if low.startswith(('http', 'www', 't.co')):
            continue
        if len(c) < 3 or c in ENTITY_STOP:
            continue
        found.append(c)

    out = []
    seen = set()
    for ent in found:
        key = ent.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(ent)
    return out


def preprocess(
    items: list[dict[str, Any]],
    max_items: int = 180,
    max_clusters: int = 12,
    max_items_per_cluster: int = 8,
) -> dict[str, Any]:
    source_counter = Counter()
    keyword_counter = Counter()
    keyword_sources: dict[str, set[str]] = defaultdict(set)
    entity_counter = Counter()
    entity_sources: dict[str, set[str]] = defaultdict(set)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    scored = []
    for it in items:
        title = trim_text(it.get('title', ''), 220)
        content = trim_text(it.get('content', ''), 400)
        source = it.get('source', '')
        score = float(it.get('score', 0) or 0)
        source_counter[source] += 1
        text = f"{title} {content} {' '.join(it.get('tags', []))}"
        kws = keywordize(text)
        ents = extract_entities(f"{title} {' '.join(it.get('tags', []))}")
        keyword_counter.update(kws)
        entity_counter.update(ents)
        for kw in set(kws):
            keyword_sources[kw].add(source)
        for ent in set(ents):
            entity_sources[ent].add(source)
        scored.append({
            'title': title,
            'content': content,
            'source': source,
            'url': it.get('url', ''),
            'score': score,
            'tags': it.get('tags', []),
            'keywords': kws,
            'entities': ents,
        })

    scored.sort(key=lambda x: (-x['score'], len(x['title'] or '')))

    seen_titles = set()
    kept = []
    for it in scored:
        norm = normalize_title(it['title'])
        if not norm or norm in seen_titles:
            continue
        seen_titles.add(norm)
        kept.append(it)
        if len(kept) >= max_items:
            break

    entity_rank = sorted(
        entity_counter,
        key=lambda k: (-(entity_counter[k] + 2 * len(entity_sources[k])), -len(k), k.lower())
    )
    keyword_rank = sorted(
        keyword_counter,
        key=lambda k: (-(keyword_counter[k] + 2 * len(keyword_sources[k])), -len(k), k.lower())
    )
    top_entities = [
        k for k in entity_rank
        if entity_counter[k] >= 2 and (len(entity_sources[k]) >= 2 or entity_counter[k] >= 4)
    ][:24]
    top_keywords = top_entities + [
        k for k in keyword_rank
        if k.lower() not in {e.lower() for e in top_entities}
        and keyword_counter[k] >= 2 and (len(keyword_sources[k]) >= 2 or keyword_counter[k] >= 4)
    ][:24]

    top_entity_set = {e.lower() for e in top_entities[:16]}
    top_keyword_set = {k.lower() for k in top_keywords[:24]}
    for it in kept:
        entity_candidates = [e for e in it['entities'] if e.lower() in top_entity_set]
        keyword_candidates = [k for k in it['keywords'] if k.lower() in top_keyword_set]
        if entity_candidates:
            bucket = sorted(
                set(entity_candidates),
                key=lambda k: (-(entity_counter[k] + 2 * len(entity_sources[k])), k.lower())
            )[0]
        elif keyword_candidates:
            bucket = sorted(
                set(keyword_candidates),
                key=lambda k: (-(keyword_counter[k] + 2 * len(keyword_sources[k])), k.lower())
            )[0]
        else:
            bucket = it['source'] or 'other'
        grouped[bucket].append(it)

    clusters = []
    for name, arr in grouped.items():
        arr.sort(key=lambda x: -x['score'])
        clusters.append({
            'name': name,
            'count': len(arr),
            'source_variety': len({i['source'] for i in arr}),
            'items': arr[:max_items_per_cluster],
        })
    clusters.sort(key=lambda x: (-x['source_variety'], -x['count'], -sum(i['score'] for i in x['items'])))

    return {
        'total_items': len(items),
        'selected_items': len(kept),
        'sources': dict(source_counter),
        'top_keywords': top_keywords[:12],
        'clusters': clusters[:max_clusters],
    }


def chunk_clusters(clusters: list[dict[str, Any]], batch_size: int = 4) -> list[list[dict[str, Any]]]:
    return [clusters[i:i + batch_size] for i in range(0, len(clusters), batch_size)]


async def call_llm(prompt: str, timeout_seconds: int) -> str | None:
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from report import generator as mod
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))
    try:
        result, fallback = await asyncio.wait_for(mod._call_llm(prompt, cfg, None), timeout=timeout_seconds)
    except TimeoutError:
        return None
    if fallback:
        return None
    return result


async def summarize_batch(
    batch_idx: int,
    batch: list[dict[str, Any]],
    key: str,
    batch_timeout: int,
    use_cache: bool,
) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f'{key}-batch-{batch_idx:02d}.json'
    if use_cache and cache_path.exists():
        return json.loads(cache_path.read_text(encoding='utf-8'))

    compact = []
    for c in batch:
        compact.append({
            'theme': c['name'],
            'count': c['count'],
            'items': [
                {
                    'title': i['title'],
                    'source': i['source'],
                    'score': i['score'],
                    'url': i['url'],
                    'content': trim_text(i['content'], 220),
                }
                for i in c['items'][:6]
            ],
        })

    prompt = f"""你在生成 AI 月报的中间摘要。请只基于以下 JSON，输出简洁 Markdown：\n\n要求：\n1. 先写“### 本批核心主题”\n2. 给出 3-5 条要点，每条一句话\n3. 再写“### 值得继续跟踪”\n4. 给出 2-3 条后续建议\n5. 不要输出多余前言\n\nJSON:\n{json.dumps(compact, ensure_ascii=False, indent=2)}\n"""
    text = await call_llm(prompt, timeout_seconds=batch_timeout)
    if not text:
        bullet_lines = []
        for c in compact[:5]:
            bullet_lines.append(f"- {c['theme']}：{c['count']} 条，代表项：{c['items'][0]['title']}")
        text = '### 本批核心主题\n' + '\n'.join(bullet_lines or ['- 无']) + '\n\n### 值得继续跟踪\n- 继续观察高频主题与跨来源重复出现的信号。\n- 优先补读高分代表项的一手资料。\n'

    obj = {'batch_index': batch_idx, 'themes': [c['theme'] for c in compact], 'summary': text}
    cache_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    return obj


def extract_md_section_bullets(md: str, title: str) -> list[str]:
    m = re.search(rf'(?ms)^###\s+{re.escape(title)}\n(.*?)(?=^###\s+|\Z)', md)
    if not m:
        return []
    out: list[str] = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if s.startswith('- '):
            out.append(s[2:].strip())
    return out


def unique_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def build_conclusion_bullets(batch_summaries: list[dict[str, Any]], pre: dict[str, Any]) -> list[str]:
    bullets: list[str] = []
    for s in batch_summaries:
        bullets.extend(extract_md_section_bullets(s['summary'], '本批核心主题'))
    bullets = unique_keep_order(bullets)
    if bullets:
        return bullets[:5]
    out = []
    for c in pre['clusters'][:5]:
        if c['items']:
            out.append(f"{c['name']}：{c['count']} 条，代表案例是《{c['items'][0]['title']}》。")
    return out[:5] or ['本月暂无足够数据。']


def build_watch_bullets(batch_summaries: list[dict[str, Any]]) -> list[str]:
    bullets: list[str] = []
    for s in batch_summaries:
        bullets.extend(extract_md_section_bullets(s['summary'], '值得继续跟踪'))
    bullets = unique_keep_order(bullets)
    if bullets:
        return bullets[:4]
    return [
        '继续观察跨来源反复出现的主题。',
        '优先补读高分代表条目的一手资料。',
        '对噪音高、重复低的主题降权处理。',
    ]


def top_case_rows(pre: dict[str, Any], max_clusters: int = 5, per_cluster: int = 2) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in pre['clusters'][:max_clusters]:
        for item in c['items'][:per_cluster]:
            rows.append({
                'theme': c['name'],
                'count': c['count'],
                'source_variety': c['source_variety'],
                'title': item['title'],
                'source': item['source'],
                'url': item['url'],
                'content': trim_text(item['content'], 160),
                'score': item['score'],
            })
    return rows


def top_reading_items(pre: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen = set()
    for c in pre['clusters']:
        for item in c['items']:
            key = item['url'] or item['title']
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({
                'theme': c['name'],
                'title': item['title'],
                'source': item['source'],
                'url': item['url'],
                'content': trim_text(item['content'], 120),
            })
            if len(out) >= limit:
                return out
    return out


async def build_monthly_report(
    ref: dt.date,
    max_items: int,
    max_clusters: int,
    batch_size: int,
    batch_timeout: int,
    final_timeout: int,
    use_cache: bool,
    completed_period: bool = False,
) -> Path:
    started = time.time()
    start, end, key = daterange(ref, completed_period=completed_period)
    items = load_items(start, end)
    pre = preprocess(items, max_items=max_items, max_clusters=max_clusters)
    batches = chunk_clusters(pre['clusters'], batch_size=batch_size)
    batch_summaries = []
    for idx, batch in enumerate(batches, start=1):
        batch_summaries.append(await summarize_batch(idx, batch, key, batch_timeout=batch_timeout, use_cache=use_cache))

    conclusion_bullets = build_conclusion_bullets(batch_summaries, pre)
    watch_bullets = build_watch_bullets(batch_summaries)
    case_rows = top_case_rows(pre)
    reading_items = top_reading_items(pre)
    top_sources = sorted(pre['sources'].items(), key=lambda kv: (-kv[1], kv[0]))[:6]

    lines = [
        f'# AI 早报月报｜{key}',
        '',
        f'- 覆盖周期：{start.isoformat()} ~ {end.isoformat()}',
        f'- 原始条目数：{pre["total_items"]}',
        f'- 代表条目数：{pre["selected_items"]}',
        f'- 主要来源：' + (' / '.join(f'{k} {v}' for k, v in top_sources) if top_sources else '暂无'),
        '',
        '## 本期结论',
    ]
    for b in conclusion_bullets:
        lines.append(f'- {b}')

    lines += ['', '## 重点主题与案例']
    for c in pre['clusters'][:5]:
        lines.append(f"### {c['name']}（{c['count']} 条，{c['source_variety']} 个来源）")
        if not c['items']:
            lines.append('- 暂无代表案例')
            lines.append('')
            continue
        for item in c['items'][:2]:
            lines.append(f"- [{item['source'] or 'unknown'}] {item['title']}")
            if item['content']:
                lines.append(f"  - 摘要：{trim_text(item['content'], 150)}")
            if item['url']:
                lines.append(f"  - 原文：{item['url']}")
        lines.append('')

    lines += ['## 建议精读（原文入口）']
    if reading_items:
        for item in reading_items:
            lines.append(f"- [{item['source'] or 'unknown'}] {item['title']}")
            if item['content']:
                lines.append(f"  - 提示：{item['content']}")
            if item['url']:
                lines.append(f"  - 原文：{item['url']}")
    else:
        lines.append('- 暂无可用原文入口。')

    lines += ['', '## 后续观察']
    for b in watch_bullets:
        lines.append(f'- {b}')

    final_text = '\n'.join(lines).rstrip() + '\n'
    structured_cache = CACHE_DIR / f'{key}-final-structured.md'
    structured_cache.write_text(final_text, encoding='utf-8')

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f'ai-monthly-v2-{key}.md'
    out_path.write_text(final_text, encoding='utf-8')

    meta = {
        'schema_version': 2,
        'month': key,
        'total_items': pre['total_items'],
        'selected_items': pre['selected_items'],
        'cluster_count': len(pre['clusters']),
        'batch_count': len(batches),
        'top_keywords': pre['top_keywords'],
        'focus_themes': [c['name'] for c in pre['clusters'][:5]],
        'conclusion_bullets': conclusion_bullets,
        'watch_bullets': watch_bullets,
        'top_cases': case_rows[:8],
        'max_items': max_items,
        'max_clusters': max_clusters,
        'batch_size': batch_size,
        'batch_timeout': batch_timeout,
        'final_timeout': final_timeout,
        'use_cache': use_cache,
        'elapsed_seconds': round(time.time() - started, 2),
    }
    (CACHE_DIR / f'{key}-meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--max-items', type=int, default=180)
    ap.add_argument('--max-clusters', type=int, default=12)
    ap.add_argument('--batch-size', type=int, default=4)
    ap.add_argument('--batch-timeout', type=int, default=180)
    ap.add_argument('--final-timeout', type=int, default=240)
    ap.add_argument('--completed-period', action='store_true')
    ap.add_argument('--no-cache', action='store_true')
    args = ap.parse_args()
    ref = dt.date.fromisoformat(args.date)
    out = asyncio.run(build_monthly_report(
        ref,
        max_items=args.max_items,
        max_clusters=args.max_clusters,
        batch_size=args.batch_size,
        batch_timeout=args.batch_timeout,
        final_timeout=args.final_timeout,
        use_cache=not args.no_cache,
        completed_period=args.completed_period,
    ))
    print(out)


if __name__ == '__main__':
    main()
