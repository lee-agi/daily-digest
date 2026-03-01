"""Distribution: push digest to RSS Worker and Feishu."""

from __future__ import annotations

import logging
import os
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

    success_count = sum(1 for v in results.values() if v)
    logger.info("Push results: %d/%d channels succeeded", success_count, len(results))
