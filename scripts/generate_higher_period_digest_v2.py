#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'config.yaml'
CACHE_DIR = ROOT / 'reports' / 'ai' / 'higher-period-v2-cache'
MONTHLY_DIR = ROOT / 'reports' / 'ai' / 'monthly-v2'
MONTHLY_CACHE_DIR = ROOT / 'reports' / 'ai' / 'monthly-v2-cache'


def period_cn(period: str) -> str:
    return {'quarterly': '季报', 'semiannual': '半年报', 'annual': '年报'}[period]


def month_key(d: dt.date) -> str:
    return f'{d.year:04d}-{d.month:02d}'


def shift_month(d: dt.date, delta: int) -> dt.date:
    month0 = d.month - 1 + delta
    year = d.year + month0 // 12
    month = month0 % 12 + 1
    return dt.date(year, month, 1)


def extract_bullets(md: str, title: str, limit: int) -> list[str]:
    m = re.search(rf'(?ms)^##\s+{re.escape(title)}\n(.*?)(?=^##\s+|\Z)', md)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if s.startswith('- '):
            out.append(s[2:].strip())
        if len(out) >= limit:
            break
    return out


def daterange(period: str, ref: dt.date, completed_period: bool = True) -> tuple[dt.date, dt.date, str, str, list[str]]:
    if completed_period:
        if period == 'quarterly':
            current_q_start = dt.date(ref.year, ((ref.month - 1) // 3) * 3 + 1, 1)
            ref = current_q_start - dt.timedelta(days=1)
        elif period == 'semiannual':
            ref = dt.date(ref.year - 1, 12, 31) if ref.month <= 6 else dt.date(ref.year, 6, 30)
        elif period == 'annual':
            ref = dt.date(ref.year - 1, 12, 31)

    if period == 'quarterly':
        q = (ref.month - 1) // 3 + 1
        sm = (q - 1) * 3 + 1
        start = dt.date(ref.year, sm, 1)
        end = shift_month(start, 3) - dt.timedelta(days=1)
        key = f'{ref.year:04d}-Q{q}'
        months = [month_key(shift_month(start, i)) for i in range(3)]
        return start, end, key, key, months
    if period == 'semiannual':
        if ref.month <= 6:
            start = dt.date(ref.year, 1, 1)
            end = dt.date(ref.year, 6, 30)
            key = f'{ref.year:04d}-H1'
            months = [f'{ref.year:04d}-{m:02d}' for m in range(1, 7)]
        else:
            start = dt.date(ref.year, 7, 1)
            end = dt.date(ref.year, 12, 31)
            key = f'{ref.year:04d}-H2'
            months = [f'{ref.year:04d}-{m:02d}' for m in range(7, 13)]
        return start, end, key, key, months
    if period == 'annual':
        start = dt.date(ref.year, 1, 1)
        end = dt.date(ref.year, 12, 31)
        key = f'{ref.year:04d}'
        months = [f'{ref.year:04d}-{m:02d}' for m in range(1, 13)]
        return start, end, key, key, months
    raise ValueError(period)


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


async def ensure_monthlies(months: list[str], use_cache: bool, max_items: int, max_clusters: int, batch_size: int, batch_timeout: int, final_timeout: int) -> list[Path]:
    import sys
    if str(ROOT / 'scripts') not in sys.path:
        sys.path.insert(0, str(ROOT / 'scripts'))
    import generate_monthly_digest_v2 as monthly

    paths: list[Path] = []
    for mk in months:
        path = MONTHLY_DIR / f'ai-monthly-v2-{mk}.md'
        meta_path = MONTHLY_CACHE_DIR / f'{mk}-meta.json'
        schema_ok = False
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
                schema_ok = int(meta.get('schema_version', 0)) >= 2 and bool(meta.get('top_cases'))
            except Exception:
                schema_ok = False
        if path.exists() and schema_ok:
            paths.append(path)
            continue
        year, mon = map(int, mk.split('-'))
        ref = dt.date(year, mon, 15)
        out = await monthly.build_monthly_report(
            ref,
            max_items=max_items,
            max_clusters=max_clusters,
            batch_size=batch_size,
            batch_timeout=batch_timeout,
            final_timeout=final_timeout,
            use_cache=use_cache,
        )
        paths.append(out)
    return paths


def load_monthly_compact(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding='utf-8', errors='ignore')
    mk = re.search(r'ai-monthly-v2-(\d{4}-\d{2})\.md$', path.name)
    key = mk.group(1) if mk else path.stem
    meta_path = MONTHLY_CACHE_DIR / f'{key}-meta.json'
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception:
            meta = {}
    return {
        'month': key,
        'conclusions': meta.get('conclusion_bullets') or extract_bullets(text, '本期结论', 4) or extract_bullets(text, '核心观察', 4),
        'focus_themes': meta.get('focus_themes') or extract_bullets(text, '主题聚焦', 5),
        'watch_bullets': meta.get('watch_bullets') or extract_bullets(text, '后续观察', 3) or extract_bullets(text, '值得继续调研', 3),
        'top_cases': meta.get('top_cases') or [],
        'top_keywords': meta.get('top_keywords', []),
        'selected_items': meta.get('selected_items'),
        'cluster_count': meta.get('cluster_count'),
        'batch_count': meta.get('batch_count'),
        'report': str(path),
    }


def chunks(arr: list[Any], n: int) -> list[list[Any]]:
    return [arr[i:i + n] for i in range(0, len(arr), n)]


async def summarize_batch(batch_idx: int, period: str, key: str, batch: list[dict[str, Any]], batch_timeout: int, use_cache: bool) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f'{key}-batch-{batch_idx:02d}.json'
    if use_cache and cache_path.exists():
        return json.loads(cache_path.read_text(encoding='utf-8'))

    compact = [
        {
            'month': x['month'],
            'conclusions': x['conclusions'],
            'focus_themes': x['focus_themes'],
            'watch_bullets': x['watch_bullets'],
            'top_cases': x['top_cases'][:4],
            'top_keywords': x['top_keywords'][:8],
        }
        for x in batch
    ]
    prompt = f"""你在生成 AI{period_cn(period)} 的中间摘要。输入是若干份月报 v2 的压缩信息，请只基于以下 JSON 输出简洁 Markdown。\n\n要求：\n1. 先写“### 本批跨月主题”\n2. 给出 3-5 条要点，强调跨月延续/变化\n3. 再写“### 值得继续跟踪”\n4. 给出 2-3 条后续建议\n5. 不要输出多余前言\n\nJSON:\n{json.dumps(compact, ensure_ascii=False, indent=2)}\n"""
    text = await call_llm(prompt, timeout_seconds=batch_timeout)
    if not text:
        lines = ['### 本批跨月主题']
        for x in compact[:4]:
            top = x['focus_themes'][0] if x['focus_themes'] else (x['conclusions'][0] if x['conclusions'] else '无明显主题')
            lines.append(f"- {x['month']}：{top}")
        lines += ['', '### 值得继续跟踪', '- 继续比较跨月反复出现的主题与叙事变化。', '- 对只出现一次的热点维持低权重。']
        text = '\n'.join(lines)
    obj = {'batch_index': batch_idx, 'months': [x['month'] for x in batch], 'summary': text}
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


def build_conclusion_bullets(batch_summaries: list[dict[str, Any]], monthlies: list[dict[str, Any]]) -> list[str]:
    bullets: list[str] = []
    for s in batch_summaries:
        bullets.extend(extract_md_section_bullets(s['summary'], '本批跨月主题'))
    bullets = unique_keep_order(bullets)
    if bullets:
        return bullets[:6]
    out = []
    for m in monthlies[:6]:
        if m['conclusions']:
            out.append(f"{m['month']}：{m['conclusions'][0]}")
    return out[:6] or ['本周期暂无足够月报 v2 数据。']


def build_watch_bullets(batch_summaries: list[dict[str, Any]]) -> list[str]:
    bullets: list[str] = []
    for s in batch_summaries:
        bullets.extend(extract_md_section_bullets(s['summary'], '值得继续跟踪'))
    bullets = unique_keep_order(bullets)
    if bullets:
        return bullets[:4]
    return [
        '继续比较跨月重复主题与叙事变化。',
        '关注只在单月爆发但后续消失的噪音热点。',
        '重点追踪闭源模型、代理平台与开源工具链的相对节奏。',
    ]


def top_reading_cases(monthlies: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen = set()
    for m in monthlies:
        for case in m['top_cases']:
            key = case.get('url') or case.get('title')
            if not key or key in seen:
                continue
            seen.add(key)
            item = dict(case)
            item['month'] = m['month']
            out.append(item)
            if len(out) >= limit:
                return out
    return out


async def build_report(period: str, ref: dt.date, completed_period: bool, ensure_monthly: bool, monthly_max_items: int, monthly_max_clusters: int, monthly_batch_size: int, batch_timeout: int, final_timeout: int, use_cache: bool, high_batch_size: int) -> Path:
    started = time.time()
    start, end, key, label, months = daterange(period, ref, completed_period=completed_period)
    if ensure_monthly:
        monthly_paths = await ensure_monthlies(months, use_cache=use_cache, max_items=monthly_max_items, max_clusters=monthly_max_clusters, batch_size=monthly_batch_size, batch_timeout=batch_timeout, final_timeout=final_timeout)
    else:
        monthly_paths = [MONTHLY_DIR / f'ai-monthly-v2-{m}.md' for m in months if (MONTHLY_DIR / f'ai-monthly-v2-{m}.md').exists()]

    monthlies = [load_monthly_compact(p) for p in monthly_paths if p.exists()]
    month_batches = chunks(monthlies, high_batch_size)
    batch_summaries = []
    for idx, batch in enumerate(month_batches, start=1):
        batch_summaries.append(await summarize_batch(idx, period, key, batch, batch_timeout=batch_timeout, use_cache=use_cache))

    conclusion_bullets = build_conclusion_bullets(batch_summaries, monthlies)
    watch_bullets = build_watch_bullets(batch_summaries)
    reading_cases = top_reading_cases(monthlies)

    lines = [
        f'# AI 早报{period_cn(period)}｜{label}',
        '',
        f'- 覆盖周期：{start.isoformat()} ~ {end.isoformat()}',
        f'- 纳入月报数：{len(monthlies)}',
        f'- 覆盖月份：' + (' / '.join(m['month'] for m in monthlies) if monthlies else '暂无'),
        '',
        '## 本期结论',
    ]
    for b in conclusion_bullets:
        lines.append(f'- {b}')

    lines += ['', '## 月度切片与代表案例']
    for m in monthlies:
        lines.append(f"### {m['month']}")
        if m['conclusions']:
            lines.append(f"- 月度结论：{m['conclusions'][0]}")
        for case in m['top_cases'][:2]:
            lines.append(f"- [{case.get('source') or 'unknown'}] {case.get('title') or '（无标题）'}")
            if case.get('content'):
                lines.append(f"  - 摘要：{case['content']}")
            if case.get('url'):
                lines.append(f"  - 原文：{case['url']}")
        lines.append(f"- 月报：{m['report']}")
        lines.append('')

    lines += ['## 建议精读（原文入口）']
    if reading_cases:
        for case in reading_cases:
            lines.append(f"- [{case.get('month')}] [{case.get('source') or 'unknown'}] {case.get('title') or '（无标题）'}")
            if case.get('content'):
                lines.append(f"  - 提示：{case['content']}")
            if case.get('url'):
                lines.append(f"  - 原文：{case['url']}")
    else:
        lines.append('- 暂无可用原文入口。')

    lines += ['', '## 后续观察']
    for b in watch_bullets:
        lines.append(f'- {b}')

    final_text = '\n'.join(lines).rstrip() + '\n'
    structured_cache = CACHE_DIR / f'{key}-final-structured.md'
    structured_cache.write_text(final_text, encoding='utf-8')

    out_dir = ROOT / 'reports' / 'ai' / f'{period}-v2'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'ai-{period}-v2-{key}.md'
    out_path.write_text(final_text, encoding='utf-8')

    meta = {
        'schema_version': 2,
        'period': period,
        'key': key,
        'covered_months': [m['month'] for m in monthlies],
        'monthly_report_count': len(monthlies),
        'batch_count': len(batch_summaries),
        'conclusion_bullets': conclusion_bullets,
        'watch_bullets': watch_bullets,
        'top_cases': reading_cases[:10],
        'high_batch_size': high_batch_size,
        'monthly_max_items': monthly_max_items,
        'monthly_max_clusters': monthly_max_clusters,
        'monthly_batch_size': monthly_batch_size,
        'batch_timeout': batch_timeout,
        'final_timeout': final_timeout,
        'use_cache': use_cache,
        'elapsed_seconds': round(time.time() - started, 2),
    }
    (CACHE_DIR / f'{key}-meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--period', required=True, choices=['quarterly', 'semiannual', 'annual'])
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--completed-period', action='store_true', default=True)
    ap.add_argument('--ensure-monthly', action='store_true', default=True)
    ap.add_argument('--monthly-max-items', type=int, default=160)
    ap.add_argument('--monthly-max-clusters', type=int, default=10)
    ap.add_argument('--monthly-batch-size', type=int, default=4)
    ap.add_argument('--high-batch-size', type=int, default=3)
    ap.add_argument('--batch-timeout', type=int, default=150)
    ap.add_argument('--final-timeout', type=int, default=210)
    ap.add_argument('--no-cache', action='store_true')
    args = ap.parse_args()
    ref = dt.date.fromisoformat(args.date)
    out = asyncio.run(build_report(
        period=args.period,
        ref=ref,
        completed_period=args.completed_period,
        ensure_monthly=args.ensure_monthly,
        monthly_max_items=args.monthly_max_items,
        monthly_max_clusters=args.monthly_max_clusters,
        monthly_batch_size=args.monthly_batch_size,
        batch_timeout=args.batch_timeout,
        final_timeout=args.final_timeout,
        use_cache=not args.no_cache,
        high_batch_size=args.high_batch_size,
    ))
    print(out)


if __name__ == '__main__':
    main()
