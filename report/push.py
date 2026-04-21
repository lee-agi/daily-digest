"""Distribution: push digest to RSS Worker, Feishu, and Weixin."""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import timedelta, timezone

import httpx

from schema import DigestReport

logger = logging.getLogger(__name__)


async def push_to_rss_worker(report: DigestReport, config: dict) -> bool:
    """Push digest report to RSS Worker as a feed item."""
    rss_config = config.get("distribution", {}).get("rss_worker", {})
    if not rss_config.get("enabled", False):
        logger.info("RSS Worker push disabled")
        return False

    url = rss_config.get("url", "")
    api_key = os.environ.get("RSS_API_KEY", "")
    if not url or not api_key:
        logger.error("RSS Worker URL or API key not configured")
        return False

    # Use generated_at timestamp to make link/title unique across same-day pushes
    ts = int(report.generated_at.timestamp())
    beijing_tz = timezone(timedelta(hours=8))
    time_suffix = report.generated_at.astimezone(beijing_tz).strftime("%H:%M")

    payload = {
        "title": f"Daily Digest - {report.date} ({time_suffix})",
        "content": report.full_markdown,
        "category": "daily-digest",
        "source": "daily-digest",
        "tags": ["digest", report.date],
        "link": f"{url}/feed#digest-{report.date}-{ts}",
    }

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}/items", json=payload, headers=headers)
        if resp.status_code in (200, 201):
            logger.info("Pushed to RSS Worker: %s", resp.json().get("action", "ok"))
            return True
        else:
            logger.error("RSS Worker push failed: %d %s", resp.status_code, resp.text)
            return False


async def push_items_batch(
    items: list[dict], config: dict
) -> bool:
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
            logger.info("Batch pushed: %d created, %d updated",
                        data.get("created", 0), data.get("updated", 0))
            return True
        else:
            logger.error("Batch push failed: %d %s", resp.status_code, resp.text)
            return False


def push_to_weixin(report: DigestReport, config: dict) -> bool:
    """Push digest report to Weixin via OpenClaw channel delivery."""
    wx_config = config.get("distribution", {}).get("weixin", {})
    if not wx_config.get("enabled", False):
        logger.info("Weixin push disabled")
        return False

    channel = wx_config.get("channel", "openclaw-weixin")
    to = wx_config.get("to", "")
    account_id = wx_config.get("account_id", "")
    if not to or not account_id:
        logger.error("Weixin target or account_id not configured")
        return False

    text = report.full_markdown
    if len(text) > 12000:
        text = text[:12000] + "\n\n[truncated]"

    try:
        proc = subprocess.run(
            [
                "openclaw",
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
                text,
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


async def push_report(report: DigestReport, config: dict) -> None:
    """Push report to all enabled distribution channels."""
    results = {}

    # RSS Worker
    results["rss_worker"] = await push_to_rss_worker(report, config)

    # Feishu - delegated to OpenClaw agent (called externally)
    feishu_config = config.get("distribution", {}).get("feishu", {})
    if feishu_config.get("enabled", False):
        logger.info("Feishu delivery will be handled by OpenClaw agent")
        results["feishu"] = True  # Placeholder

    # Weixin - direct via OpenClaw channel
    results["weixin"] = push_to_weixin(report, config)

    success_count = sum(1 for v in results.values() if v)
    logger.info("Push results: %d/%d channels succeeded", success_count, len(results))
