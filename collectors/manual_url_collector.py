"""Manual URL collector with Mac Reminders T5T integration.

Reads URLs from ``data/pending_urls.yaml`` and optionally imports new URLs
from the macOS Reminders app "T5T" list.  Each URL is processed through
any2summary to generate full-text content for the digest.
"""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from collectors.base import BaseCollector
from collectors.enricher import ContentEnricher, EnrichmentConfig
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https?://[^\s<>\"'|,]+")


class ManualURLCollector(BaseCollector):
    """Collect and enrich manually submitted URLs.

    Supports two input paths:
    1. Mac Reminders "T5T" list → auto-import URLs into pending_urls.yaml
    2. Direct editing of ``data/pending_urls.yaml``
    """

    source_name = "manual_urls"
    source_type = SourceType.HTTP_SCRAPE

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._pending_file = Path(
            config.get("file", "data/pending_urls.yaml")
        )
        # Make relative paths relative to project root
        if not self._pending_file.is_absolute():
            self._pending_file = Path(__file__).parent.parent / self._pending_file
        self._reminders_list = config.get("reminders_list", "T5T")
        self._reminders_enabled = config.get("reminders_enabled", False)
        # Build enricher from parent config enrichment section if available
        enrichment_cfg = config.get("_enrichment_config")
        if enrichment_cfg:
            self._enricher = ContentEnricher(enrichment_cfg)
        else:
            self._enricher = ContentEnricher(EnrichmentConfig())

    # ------------------------------------------------------------------
    # Mac Reminders import
    # ------------------------------------------------------------------

    def _import_from_reminders(self) -> list[str]:
        """Read incomplete reminders from the T5T list, extract URLs."""
        script = f'''
        tell application "Reminders"
            set resultList to {{}}
            try
                set reminderList to list "{self._reminders_list}"
                set unfinished to (get reminders of reminderList whose completed is false)
                repeat with r in unfinished
                    set rName to name of r as string
                    set rBody to ""
                    try
                        set rBody to body of r as string
                    end try
                    set end of resultList to (rName & "|||" & rBody)
                end repeat
            end try
            return resultList
        end tell
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning(
                    "[manual_urls] osascript failed: %s",
                    (result.stderr or "")[:200],
                )
                return []

            urls: list[str] = []
            for line in result.stdout.strip().split(", "):
                line = line.strip()
                if not line:
                    continue
                found = _URL_PATTERN.findall(line)
                urls.extend(found)

            return urls

        except subprocess.TimeoutExpired:
            logger.warning("[manual_urls] osascript timed out")
            return []
        except FileNotFoundError:
            logger.info("[manual_urls] osascript not available (non-macOS?)")
            return []
        except Exception as exc:
            logger.warning("[manual_urls] Reminders import error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # pending_urls.yaml management
    # ------------------------------------------------------------------

    def _load_pending(self) -> dict[str, Any]:
        """Load pending_urls.yaml, returning default structure if missing."""
        if not self._pending_file.exists():
            return {"pending": [], "processed_archive": []}
        try:
            data = yaml.safe_load(self._pending_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"pending": [], "processed_archive": []}
            data.setdefault("pending", [])
            data.setdefault("processed_archive", [])
            return data
        except Exception as exc:
            logger.warning("[manual_urls] Failed to read %s: %s", self._pending_file, exc)
            return {"pending": [], "processed_archive": []}

    def _save_pending(self, data: dict[str, Any]) -> None:
        """Write pending_urls.yaml back to disk."""
        self._pending_file.parent.mkdir(parents=True, exist_ok=True)
        self._pending_file.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )

    def _append_urls(self, urls: list[str], data: dict[str, Any]) -> int:
        """Append new URLs to pending list (deduplicated). Returns count added."""
        existing_urls = {
            entry.get("url") for entry in data.get("pending", [])
        }
        existing_urls |= {
            entry.get("url") for entry in data.get("processed_archive", [])
        }

        added = 0
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for url in urls:
            url = url.strip()
            if url and url not in existing_urls:
                data["pending"].append({
                    "url": url,
                    "added_at": today,
                    "processed": False,
                })
                existing_urls.add(url)
                added += 1
        return added

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    async def collect(self) -> list[ContentItem]:
        """Collect and enrich URLs from Reminders + pending_urls.yaml."""
        data = self._load_pending()

        # Step 1: Import from Reminders
        if self._reminders_enabled:
            reminder_urls = self._import_from_reminders()
            if reminder_urls:
                added = self._append_urls(reminder_urls, data)
                if added:
                    logger.info(
                        "[manual_urls] Imported %d new URLs from Reminders '%s'",
                        added, self._reminders_list,
                    )

        # Step 2: Process unprocessed entries
        items: list[ContentItem] = []
        pending = data.get("pending", [])
        unprocessed = [e for e in pending if not e.get("processed", False)]

        if not unprocessed:
            if data.get("pending"):
                self._save_pending(data)
            return items

        logger.info("[manual_urls] Processing %d pending URLs", len(unprocessed))

        for entry in unprocessed:
            url = entry.get("url", "").strip()
            if not url:
                continue

            # Call any2summary
            result = self._enricher.call_any2summary(url)
            content = ""
            if result:
                content = self._enricher._extract_summary_text(result)
                content = content[: self._enricher.config.content_max_chars]

            title = entry.get("title") or self._guess_title(url, result)
            item = ContentItem(
                source="manual_urls",
                source_type=SourceType.HTTP_SCRAPE,
                title=title,
                url=url,
                content=content,
                score=100.0,  # Manual items get high priority
                tags=["manual"],
                extra={
                    "enriched": bool(content),
                    "priority": entry.get("priority", "normal"),
                },
            )
            items.append(item)

            # Mark as processed
            entry["processed"] = True
            entry["processed_at"] = datetime.now(timezone.utc).isoformat()

        # Move processed entries to archive
        still_pending = []
        for entry in pending:
            if entry.get("processed", False):
                data["processed_archive"].append(entry)
            else:
                still_pending.append(entry)
        data["pending"] = still_pending

        self._save_pending(data)
        logger.info("[manual_urls] Collected %d items", len(items))
        return items

    @staticmethod
    def _guess_title(url: str, result: dict[str, Any] | None) -> str:
        """Extract a title from any2summary result or URL."""
        if result:
            meta = result.get("article_metadata") or result.get("summary_metadata")
            if meta and isinstance(meta, dict):
                title = meta.get("title", "")
                if title:
                    return str(title)
        # Fallback: use URL domain + path
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if path:
            return f"{parsed.netloc}{path}"
        return parsed.netloc or url[:80]
