#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V1_DIR = ROOT / 'reports' / 'ai' / 'monthly'
V2_DIR = ROOT / 'reports' / 'ai' / 'monthly-v2'
OUT_DIR = ROOT / 'reports' / 'ai' / 'monthly-compare'


def read(p: Path) -> str:
    return p.read_text(encoding='utf-8', errors='ignore')


def extract_section(md: str, title: str) -> str:
    m = re.search(rf'(?ms)^##\s+{re.escape(title)}\n(.*?)(?=^##\s+|\Z)', md)
    return m.group(1).strip() if m else ''


def bullets(block: str, limit: int = 8) -> list[str]:
    out = []
    for line in block.splitlines():
        s = line.strip()
        if s.startswith('- '):
            out.append(s)
        if len(out) >= limit:
            break
    return out


def first_para(block: str) -> str:
    paras = [p.strip() for p in block.split('\n\n') if p.strip()]
    return paras[0] if paras else ''


def compare(month: str) -> Path:
    v1 = V1_DIR / f'ai-monthly-{month}.md'
    v2 = V2_DIR / f'ai-monthly-v2-{month}.md'
    if not v1.exists():
        raise FileNotFoundError(v1)
    if not v2.exists():
        raise FileNotFoundError(v2)

    t1 = read(v1)
    t2 = read(v2)
    v1_core = bullets(extract_section(t1, '核心观察'))
    v2_core = bullets(extract_section(t2, '核心观察'))
    v1_focus = bullets(extract_section(t1, '主题聚焦')) or bullets(extract_section(t1, '值得继续调研'))
    v2_focus = bullets(extract_section(t2, '主题聚焦'))
    v1_summary = first_para(extract_section(t1, '分主题回看')) or first_para(extract_section(t1, '结论'))
    v2_summary = first_para(extract_section(t2, '分批摘要整合')) or first_para(extract_section(t2, '结论'))

    lines = [f'# AI 月报 v1 vs v2 对比｜{month}', '']
    lines += ['## 文件', f'- v1: `{v1}`', f'- v2: `{v2}`', '']
    lines += ['## 核心观察对比', '### v1'] + (v1_core or ['- （未提取到）']) + ['', '### v2'] + (v2_core or ['- （未提取到）']) + ['']
    lines += ['## 主题聚焦对比', '### v1'] + (v1_focus[:8] or ['- （未提取到）']) + ['', '### v2'] + (v2_focus[:8] or ['- （未提取到）']) + ['']
    lines += ['## 摘要段落对比', '### v1', v1_summary or '（未提取到）', '', '### v2', v2_summary or '（未提取到）', '']
    lines += ['## 判断', '- v2 当前优势：直接基于原始 JSON 预聚合，主题聚焦更稳定，受 markdown 模板污染更少。', '- v2 当前风险：实体归一化仍需持续打磨，且依赖分批 LLM 汇总，需继续观察成本/耗时。', '- 建议：当前已采用 v2 作为月报正式主链；保留静默 v1 回退 / 对照链路，用于异常时快速回退和横向比较。']

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f'ai-monthly-compare-{month}.md'
    out.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--month', default=dt.date.today().strftime('%Y-%m'))
    args = ap.parse_args()
    print(compare(args.month))


if __name__ == '__main__':
    main()
