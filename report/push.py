"""Distribution: push digest to RSS Worker, Feishu, and Weixin."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from schema import DigestReport
from report.usage import format_usage_footer

logger = logging.getLogger(__name__)

_MARKDOWN_LINK_RE = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])+)]\((?:<([^>]+)>|([^\)]+))\)")


def _clean_link_label(label: str) -> str:
    return (label or "link").replace("[", "").replace("]", "").strip() or "link"


def _markdown_destination_url(url: str) -> str:
    """Encode URL characters that break non-CommonMark link parsers."""
    return quote((url or "").strip(), safe=":/?#@!$&'*,;=%+")


def _rss_worker_markdown(markdown: str) -> str:
    """Render links for the current simple RSS Worker Markdown parser.

    The worker parser historically supports `[title](url)` but not CommonMark
    angle-bracket destinations `[title](<url>)`. Normalize before sending so
    existing deployed workers generate `href="https://..."`, not
    `href="<https://...>"`.
    """

    def repl(match: re.Match[str]) -> str:
        label = _clean_link_label(match.group(1) or "link")
        url = (match.group(2) or match.group(3) or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return match.group(0)
        return f"[{label}]({_markdown_destination_url(url)})"

    return _MARKDOWN_LINK_RE.sub(repl, markdown)


def _openclaw_bin() -> str:
    candidates = [
        Path.home() / '.openclaw' / 'workspace' / 'scripts' / 'openclaw-safe.sh',
        Path.home() / '.npm-global' / 'bin' / 'openclaw',
    ]
    for path in candidates:
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    found = shutil.which('openclaw')
    if found:
        return found
    return 'openclaw'


def _weixin_target(config: dict) -> tuple[str, str, str] | None:
    wx_config = config.get("distribution", {}).get("weixin", {})
    if not wx_config.get("enabled", False):
        logger.info("Weixin push disabled")
        return None

    channel = wx_config.get("channel", "openclaw-weixin")
    to = wx_config.get("to", "")
    account_id = wx_config.get("account_id", "")
    if not to or not account_id:
        logger.error("Weixin target or account_id not configured")
        return None
    return channel, to, account_id


def _send_weixin_text(message: str, config: dict) -> bool:
    target = _weixin_target(config)
    if not target:
        return False
    channel, to, account_id = target

    try:
        proc = subprocess.run(
            [
                _openclaw_bin(),
                "message",
                "send",
                "--json",
                "--channel",
                channel,
                "--account",
                account_id,
                "--target",
                to,
                "--message",
                message,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            logger.info("Pushed to Weixin via OpenClaw channel: %s", to)
            return True
        logger.error("Weixin push failed: code=%s stdout=%s stderr=%s", proc.returncode, proc.stdout, proc.stderr)
        return False
    except Exception as e:
        logger.error("Weixin push exception: %s", e)
        return False


async def _find_similar_rss_item(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict,
    payload: dict,
    max_check: int = 20,
) -> dict | None:
    """Try to find an existing RSS item that is similar to payload.

    Strategy (best-effort):
    - Query recent items tagged with 'digest' (if the worker supports it).
    - Fallback to GET /items and scan returned list.
    - Compare by exact title match first, then by simple content similarity
      using difflib.SequenceMatcher on the first 200 chars.

    Returns the existing item dict if found, otherwise None.
    """
    import difflib

    try:
        # Try a filtered query first
        resp = await client.get(f"{base_url}/items", params={"tags": "digest", "limit": max_check}, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            candidates = data if isinstance(data, list) else data.get("items", [])
        else:
            # Fallback: try plain /items
            resp = await client.get(f"{base_url}/items", params={"limit": max_check}, headers=headers)
            if resp.status_code != 200:
                logger.warning("Unable to list RSS items for similarity check: %d %s", resp.status_code, resp.text)
                return None
            data = resp.json()
            candidates = data if isinstance(data, list) else data.get("items", [])
    except Exception as e:
        logger.warning("RSS worker items list failed: %s", e)
        return None

    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "")[:200]
    for item in candidates:
        it_title = str(item.get("title") or "").strip()
        it_content = str(item.get("content") or "")[:200]
        if not it_title and not it_content:
            continue
        # Exact title match
        if it_title == title:
            return item
        # Content similarity
        ratio = difflib.SequenceMatcher(a=content, b=it_content).ratio()
        if ratio >= 0.85:
            return item
    return None


async def push_to_rss_worker(report: DigestReport, config: dict) -> bool:
    """Push digest report to RSS Worker as a feed item.

    PoC behavior: if the RSS worker already contains a very similar item (by
    title or content), attempt to update it via PUT /items/{id}. If the worker
    does not accept updates, fall back to creating a new item. This provides
    additional protection against duplicate feed items when the pipeline
    generates patch/regenerate cycles.
    """
    rss_config = config.get("distribution", {}).get("rss_worker", {})
    if not rss_config.get("enabled", False):
        logger.info("RSS Worker push disabled")
        return False

    url = rss_config.get("url", "")
    api_key = os.environ.get("RSS_API_KEY", "")
    if not url or not api_key:
        logger.error("RSS Worker URL or API key not configured")
        return False

    ts = int(report.generated_at.timestamp())
    beijing_tz = timezone(timedelta(hours=8))
    time_suffix = report.generated_at.astimezone(beijing_tz).strftime("%H:%M")

    payload = {
        "title": f"Daily Digest - {report.date} ({time_suffix})",
        "content": _rss_worker_markdown(report.full_markdown),
        "category": "daily-digest",
        "source": "daily-digest",
        "tags": ["digest", report.date],
        "link": f"{url}/feed#digest-{report.date}-{ts}",
    }

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        # Try to find an existing similar item first
        existing = await _find_similar_rss_item(client, url, headers, payload)
        if existing and existing.get("id"):
            item_id = existing.get("id")
            try:
                resp = await client.put(f"{url}/items/{item_id}", json=payload, headers=headers)
                if resp.status_code in (200, 201):
                    logger.info("Updated existing RSS item %s: %s", item_id, resp.json().get("action", "updated"))
                    return True
                logger.warning("RSS Worker update failed (%d); falling back to create", resp.status_code)
            except Exception as e:
                logger.warning("RSS Worker update attempt failed: %s", e)

        # No suitable existing item found or update failed: create
        try:
            resp = await client.post(f"{url}/items", json=payload, headers=headers)
            if resp.status_code in (200, 201):
                logger.info("Pushed to RSS Worker: %s", resp.json().get("action", "created"))
                return True
            logger.error("RSS Worker push failed: %d %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logger.error("RSS Worker push exception: %s", e)
            return False


async def push_items_batch(items: list[dict], config: dict) -> bool:
    """Push individual items to RSS Worker via batch endpoint."""
    rss_config = config.get("distribution", {}).get("rss_worker", {})
    if not rss_config.get("enabled", False):
        return False

    url = rss_config.get("url", "")
    api_key = os.environ.get("RSS_API_KEY", "")
    if not url or not api_key:
        return False

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{url}/items/batch",
            json={"items": items},
            headers=headers,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            logger.info("Batch pushed: %d created, %d updated", data.get("created", 0), data.get("updated", 0))
            return True
        logger.error("Batch push failed: %d %s", resp.status_code, resp.text)
        return False



def _heading_level(line: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line or "")
    if not match:
        return None
    return len(match.group(1))


def _heading_title(line: str) -> str:
    return re.sub(r"^#{1,6}\s+", "", line or "").strip()


def _extract_heading_section(markdown: str, wanted: set[str]) -> str:
    """Extract a Markdown section by exact heading title.

    The section runs until the next heading at the same or higher level. This is
    enough for the generated digest structure and avoids sending the entire
    report to short-message channels.
    """
    lines = markdown.splitlines()
    start = None
    start_level = None
    for idx, line in enumerate(lines):
        level = _heading_level(line)
        if level is None:
            continue
        if _heading_title(line) in wanted:
            start = idx
            start_level = level
            break
    if start is None or start_level is None:
        return ""

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        level = _heading_level(lines[idx])
        if level is not None and level <= start_level:
            end = idx
            break
    section_lines = [line for line in lines[start:end] if line.strip() != "---"]
    return "\n".join(section_lines).strip()


def _weixin_other_headings(markdown: str, max_headings: int = 18) -> list[str]:
    """Return remaining report headings as a compact table-of-contents list."""
    skip = {
        "平台统计",
        "信息源可用性与自修复",
        "今日精选日报",
        "1. Today's Top 10 Headlines",
        "Token 使用与费用",
    }
    out: list[str] = []
    for line in markdown.splitlines():
        level = _heading_level(line)
        if level not in (2, 3):
            continue
        title = _heading_title(line)
        if title in skip:
            continue
        prefix = "- " if level == 2 else "  - "
        out.append(prefix + title)
        if len(out) >= max_headings:
            break
    return out


def _weixin_compact_digest_markdown(report: DigestReport, config: dict) -> str:
    """Build a concise Weixin version; RSS keeps the full report.

    Lee wants Weixin to be a quick glance: platform stats, source availability,
    today's selected headlines, and only headings for the rest of the report.
    """
    markdown = report.full_markdown or ""
    beijing_tz = timezone(timedelta(hours=8))
    generated = report.generated_at.astimezone(beijing_tz).strftime("%Y-%m-%d %H:%M")

    parts = [
        f"# AI 早报 {report.date}\n\n"
        f"> Generated at {generated} | {report.items_count} items from {report.sources_count} sources",
    ]
    content_sections: list[str] = []

    for wanted in [
        {"平台统计"},
        {"信息源可用性与自修复"},
        {"1. Today's Top 10 Headlines"},
    ]:
        section = _extract_heading_section(markdown, wanted)
        if section:
            content_sections.append(section)

    if not content_sections:
        # Backward-compatible fallback for tests/manual reports with no known
        # digest sections: send the original body through the existing guardrails.
        return markdown

    parts.extend(content_sections)

    headings = _weixin_other_headings(markdown)
    if headings:
        parts.append("## 其余内容标题\n\n" + "\n".join(headings))

    rss_url = (config.get("distribution", {}).get("rss_worker", {}) or {}).get("url", "")
    if rss_url:
        parts.append(f"完整版见 RSS：{rss_url.rstrip('/')}/feed")

    return "\n\n---\n\n".join(parts)


def _weixin_clickable_text(markdown: str) -> str:
    """Render Markdown links as plain clickable URLs for Weixin.

    Weixin receives this digest as plain text, not as a Markdown renderer. If we
    send `[title](<https://...>)`, the mobile client may auto-detect the literal
    URL including the trailing `>)`, causing every click to open a 404 URL. Put
    the URL in the clear and terminate it with whitespace instead.
    """

    def repl(match: re.Match[str]) -> str:
        label = _clean_link_label(match.group(1) or "link")
        url = (match.group(2) or match.group(3) or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return match.group(0)
        return f"{label}：{url} "

    text = _MARKDOWN_LINK_RE.sub(repl, markdown)
    # Weixin receives plain text; Markdown emphasis markers add noise around URLs.
    return text.replace("**", "")


def push_to_weixin(report: DigestReport, config: dict) -> bool:
    """Push digest report to Weixin via OpenClaw channel delivery."""
    if os.environ.get("DIGEST_DISABLE_WEIXIN", "0") == "1":
        logger.info("Weixin digest push disabled by DIGEST_DISABLE_WEIXIN=1")
        return False
    usage_footer = format_usage_footer(report.stats.get("llm_usage") if report.stats else None)
    text = _weixin_clickable_text(_weixin_compact_digest_markdown(report, config))
    if len(text) > 12000:
        # Keep usage/cost visible even when the report body must be truncated.
        budget = max(12000 - len(usage_footer) - 20, 1000)
        text = text[:budget] + "\n\n[truncated]\n\n" + usage_footer
    elif "Token 使用与费用" not in text[-2000:]:
        text = text.rstrip() + "\n\n" + usage_footer
    return _send_weixin_text(text, config)


def push_weixin_reminder(report: DigestReport, config: dict) -> bool:
    """Send a short reminder after the digest push completes."""
    if os.environ.get("DIGEST_DISABLE_WEIXIN", "0") == "1":
        logger.info("Weixin reminder disabled by DIGEST_DISABLE_WEIXIN=1")
        return False
    if os.environ.get("DIGEST_ENABLE_WEIXIN_REMINDER", "0") != "1":
        logger.info("Weixin reminder disabled by default")
        return False
    beijing_tz = timezone(timedelta(hours=8))
    ts = report.generated_at.astimezone(beijing_tz).strftime("%H:%M")
    usage_footer = format_usage_footer(report.stats.get("llm_usage") if report.stats else None)
    reminder = (
        f"AI 早报已推送（{report.date} {ts}）。\n"
        f"今天共整理 {report.items_count} 条内容，建议优先看最上面的核心摘要。\n"
        f"{usage_footer}"
    )
    return _send_weixin_text(reminder, config)


async def push_report(report: DigestReport, config: dict) -> None:
    """Push report to all enabled distribution channels."""
    results = {}

    results["rss_worker"] = await push_to_rss_worker(report, config)

    feishu_config = config.get("distribution", {}).get("feishu", {})
    if feishu_config.get("enabled", False):
        logger.info("Feishu delivery will be handled by OpenClaw agent")
        results["feishu"] = True

    results["weixin"] = push_to_weixin(report, config)
    results["weixin_reminder"] = push_weixin_reminder(report, config)

    success_count = sum(1 for v in results.values() if v)
    logger.info("Push results: %d/%d channels succeeded", success_count, len(results))
