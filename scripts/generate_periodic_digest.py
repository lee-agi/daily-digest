#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DAILY_DIR = Path.home() / 'Developer' / 'research' / 'daily-digest'
OUTPUT_BASE = ROOT / 'reports' / 'ai'


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='ignore')


def _daterange(period: str, ref: dt.date, completed_period: bool = False) -> tuple[dt.date, dt.date, str, str]:
    if period == 'weekly':
        start = ref - dt.timedelta(days=ref.weekday())
        end = start + dt.timedelta(days=6)
        iso = ref.isocalendar()
        key = f'{iso.year:04d}-W{iso.week:02d}'
        label = f'{start.isoformat()} ~ {end.isoformat()}'
        return start, end, key, label
    if completed_period:
        if period == 'monthly':
            ref = ref.replace(day=1) - dt.timedelta(days=1)
        elif period == 'quarterly':
            quarter = (ref.month - 1) // 3 + 1
            current_q_start = dt.date(ref.year, (quarter - 1) * 3 + 1, 1)
            ref = current_q_start - dt.timedelta(days=1)
        elif period == 'semiannual':
            ref = dt.date(ref.year - 1, 12, 31) if ref.month <= 6 else dt.date(ref.year, 6, 30)
        elif period == 'annual':
            ref = dt.date(ref.year - 1, 12, 31)
    if period == 'monthly':
        start = ref.replace(day=1)
        next_month = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        end = next_month - dt.timedelta(days=1)
        key = f'{start.year:04d}-{start.month:02d}'
        label = f'{start.year:04d}-{start.month:02d}'
        return start, end, key, label
    quarter = (ref.month - 1) // 3 + 1
    if period == 'quarterly':
        sm = (quarter - 1) * 3 + 1
        start = dt.date(ref.year, sm, 1)
        em = sm + 2
        next_month = (dt.date(ref.year, em, 28) + dt.timedelta(days=4)).replace(day=1)
        end = next_month - dt.timedelta(days=1)
        key = f'{ref.year:04d}-Q{quarter}'
        label = key
        return start, end, key, label
    if period == 'semiannual':
        sm = 1 if ref.month <= 6 else 7
        start = dt.date(ref.year, sm, 1)
        em = 6 if sm == 1 else 12
        if em == 12:
            end = dt.date(ref.year, 12, 31)
            half = 'H2'
        else:
            end = dt.date(ref.year, 6, 30)
            half = 'H1'
        key = f'{ref.year:04d}-{half}'
        label = key
        return start, end, key, label
    if period == 'annual':
        start = dt.date(ref.year, 1, 1)
        end = dt.date(ref.year, 12, 31)
        key = f'{ref.year:04d}'
        label = key
        return start, end, key, label
    raise ValueError(f'unsupported period: {period}')


def _collect_files(start: dt.date, end: dt.date, daily_dir: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(daily_dir.glob('digest-*.md')):
        m = re.search(r'digest-(\d{4}-\d{2}-\d{2})-\d{4}\.md$', p.name)
        if not m:
            continue
        d = dt.date.fromisoformat(m.group(1))
        if start <= d <= end:
            files.append(p)
    return files


def _normalize_point(line: str) -> str:
    line = re.sub(r'^[-*]\s+', '', line.strip())
    line = re.sub(r'^\d+\.\s*', '', line)
    line = re.sub(r'https?://\S+', '', line)
    line = re.sub(r'[`*_>#\[\]]', '', line)
    line = re.sub(r'\s+', ' ', line)
    return line.strip().lower()


def _extract_keywords(text: str) -> list[str]:
    tokens = re.findall(r'[A-Za-z][A-Za-z0-9.+-]{2,}|[\u4e00-\u9fff]{2,}', text)
    stop = {
        'today', 'headlines', 'platform', 'statistics', 'summary', 'research', 'developer', 'tools',
        'videos', 'podcasts', 'social', 'community', 'books', 'reading', 'the', 'and', 'for', 'with',
        'from', 'this', 'that', 'will', 'more', 'most', 'less', 'have', 'has', 'had', 'data', 'report',
        'daily', 'weekly', 'monthly', 'quarterly', 'semiannual', 'annual', 'models', 'model', 'agents',
        'agent', 'release', 'preview', 'status', 'github', 'reddit', 'twitter', 'producthunt', 'https',
        'http', 'www', 'top', 'papers', 'paper', 'link', 'links', '本周期', '本周', '本月', '本季', '半年', '全年', '观察', '继续', '调研', '平台',
        '统计', '日报', '周报', '月报', '季报', '年报', '案例', '原文', '链接', '入口', '建议', '精读', 'AI', 'LLM'
    }
    stop_lower = {s.lower() for s in stop}
    out = []
    for t in tokens:
        if t.lower() in stop_lower:
            continue
        out.append(t)
    return out


def _clean_case_title(text: str) -> str:
    text = re.sub(r'^[-*]\s+', '', text.strip())
    text = re.sub(r'^\d+\.\s*', '', text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = text.replace('**', '').replace('`', '')
    text = re.sub(r'^高亮[:：]?\s*', '', text)
    text = re.sub(r'(原文|链接|讨论|详情|技术报告|Link|更多信息)[:：].*$', '', text, flags=re.I)
    text = re.sub(r'(—\s*Link|。链接|链接)$', '', text, flags=re.I)
    text = re.sub(r'（[^（）]{1,20}）$', '', text)
    text = re.sub(r'\([^()]{1,20}\)$', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(' -：:;；，。,')


def _all_urls(text: str) -> list[str]:
    urls = []
    seen = set()
    for raw in re.findall(r'https?://[^\s\u3000)）\]】,，;；]+', text):
        url = raw.rstrip('),.，。;；]）')
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _first_url(text: str) -> str:
    urls = _all_urls(text)
    return urls[0] if urls else ''


def _extract_case_items(md: str, path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    lines = md.splitlines()
    current_section = ''
    pending: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        title = pending.get('title', '').strip()
        if not title or not pending.get('url'):
            pending = None
            return
        if current_section.lower().startswith('platform statistics'):
            pending = None
            return
        items.append(pending)
        pending = None

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith('## '):
            flush()
            current_section = stripped[3:].strip()
            continue
        if stripped.startswith('### '):
            flush()
            current_section = stripped[4:].strip()
            continue
        if stripped.startswith('|'):
            continue

        is_top_bullet = bool(re.match(r'^\s*(?:- |\d+\.\s+)', line))
        is_sub_bullet = bool(re.match(r'^\s{2,}-\s+', line))

        if is_top_bullet and not is_sub_bullet:
            flush()
            title = _clean_case_title(stripped)
            if not title.startswith(('原文：', '链接：', '链接/作者：', '作者/链接：', '提示：', '摘要：')):
                pending = {
                    'title': title,
                    'url': _first_url(stripped),
                    'section': current_section,
                    'report': path.name,
                    'snippet': '',
                }
            continue

        if pending is None:
            continue

        if not pending.get('url'):
            url = _first_url(stripped)
            if url and any(tag in stripped for tag in ('原文', '链接', '作者', '详见', '详情')):
                pending['url'] = url
                continue

        if not pending.get('snippet') and any(tag in stripped for tag in ('摘要：', '提示：', '总结', '简述')):
            snippet = re.sub(r'^[-*]\s*', '', stripped)
            pending['snippet'] = snippet

    flush()
    return items


def _build_followups(top_keywords: list[str], period: str) -> list[str]:
    base = [
        '优先补读上面案例的一手原文，确认哪些只是舆论热度，哪些已经进入真实产品/工程落地。',
        '把跨多天重复出现、且来自不同来源的平台/模型/工具单独拉成持续跟踪名单。',
    ]
    if top_keywords:
        focus = '、'.join(top_keywords[:3])
        base.append(f'下一个{_period_cn(period)}优先继续追踪：{focus}。')
    else:
        base.append(f'下一个{_period_cn(period)}继续观察高频重复主题与一次性热点的分化。')
    return base[:3]


def build_report(period: str, ref: dt.date, daily_dir: Path, completed_period: bool = False) -> tuple[str, Path]:
    start, end, key, label = _daterange(period, ref, completed_period=completed_period)
    files = _collect_files(start, end, daily_dir)
    out_dir = OUTPUT_BASE / period
    out_dir.mkdir(parents=True, exist_ok=True)

    case_items: list[dict[str, Any]] = []
    keyword_counter: Counter[str] = Counter()
    for path in files:
        text = _read(path)
        extracted = _extract_case_items(text, path)
        case_items.extend(extracted)
        for item in extracted:
            keyword_counter.update(_extract_keywords(f"{item.get('title', '')} {item.get('snippet', '')} {item.get('section', '')}"))

    scored_cases: list[tuple[int, dict[str, Any]]] = []
    seen = set()
    for item in case_items:
        dedupe_key = item.get('url') or _normalize_point(item.get('title', ''))
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        score = sum(keyword_counter.get(tok, 0) for tok in _extract_keywords(f"{item.get('title', '')} {item.get('snippet', '')}"))
        score += 2 if item.get('section', '').startswith('1.') else 0
        scored_cases.append((score, item))

    scored_cases.sort(key=lambda x: (-x[0], len(x[1].get('title', ''))))
    top_cases = [item for _, item in scored_cases]
    top_keywords = [k for k, _ in keyword_counter.most_common(8)]
    followups = _build_followups(top_keywords, period)

    lines: list[str] = [f'# AI 早报{_period_cn(period)}｜{label}', '']
    lines += [
        f'- 覆盖周期：{start.isoformat()} ~ {end.isoformat()}',
        f'- 纳入日报数：{len(files)}',
        f'- 可追溯案例数：{len(top_cases)}',
        '',
        '## 本期结论',
    ]

    if top_cases:
        for item in top_cases[:6]:
            lines.append(f"- {item['title']}")
    else:
        lines.append('- 本周期暂无足够可追溯案例。')

    lines += ['', '## 代表案例与原文']
    if top_cases:
        for item in top_cases[:10]:
            section = item.get('section') or '未分类'
            lines.append(f"- [{section}] {item['title']}")
            if item.get('snippet'):
                lines.append(f"  - 摘要：{item['snippet']}")
            lines.append(f"  - 原文：{item['url']}")
            lines.append(f"  - 来源日报：{item['report']}")
    else:
        lines.append('- 暂无可用原文入口。')

    lines += ['', '## 日报入口']
    if files:
        for path in files:
            lines.append(f'- {path}')
    else:
        lines.append('- 本周期未找到匹配的日报文件。')

    lines += ['', '## 后续观察']
    for s in followups:
        lines.append(f'- {s}')

    content = '\n'.join(lines).rstrip() + '\n'
    out_path = out_dir / f'ai-{period}-{key}.md'
    out_path.write_text(content, encoding='utf-8')
    return content, out_path


def _period_cn(period: str) -> str:
    return {
        'weekly': '周报',
        'monthly': '月报',
        'quarterly': '季报',
        'semiannual': '半年报',
        'annual': '年报',
    }[period]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--period', required=True, choices=['weekly', 'monthly', 'quarterly', 'semiannual', 'annual'])
    parser.add_argument('--date', default=dt.date.today().isoformat())
    parser.add_argument('--daily-dir', default=str(DEFAULT_DAILY_DIR))
    parser.add_argument('--completed-period', action='store_true')
    args = parser.parse_args()

    ref = dt.date.fromisoformat(args.date)
    _, out_path = build_report(args.period, ref, Path(args.daily_dir).expanduser(), completed_period=args.completed_period)
    print(out_path)


if __name__ == '__main__':
    main()
