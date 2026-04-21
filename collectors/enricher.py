"""Content enrichment via any2summary integration.

Enriches high-quality ContentItems with full text / transcripts by calling
the any2summary CLI as a subprocess.  Handles article, video, and podcast
content types with configurable quality gates.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from schema import ContentItem

logger = logging.getLogger(__name__)

# Default any2summary location
_DEFAULT_ANY2SUMMARY_DIR = Path.home() / "Documents" / "Code" / "any2summary"


@dataclass
class EnrichmentConfig:
    """Configuration for content enrichment."""

    enabled: bool = False
    timeout_seconds: int = 300
    large_download_minutes: int = 30
    content_max_chars: int = 3000
    article_auto_threshold: int = 5000
    any2summary_dir: str = str(_DEFAULT_ANY2SUMMARY_DIR)

    # Per-source top-N limits
    youtube_top_n: int = 5
    youtube_min_engagement_rate: float = 0.0
    podcast_top_n: int = 3
    article_top_n: int = 10

    # Per-type timeout (seconds)
    article_timeout: int = 30
    youtube_timeout: int = 600
    podcast_timeout: int = 180

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> EnrichmentConfig:
        """Build from the ``enrichment`` section of config.yaml."""
        sources = cfg.get("sources", {})
        yt = sources.get("youtube", {})
        pod = sources.get("apple_podcast", {})
        art = sources.get("articles", {})
        xiao = sources.get("xiaoyuzhou", {})
        return cls(
            enabled=cfg.get("enabled", False),
            timeout_seconds=cfg.get("timeout_seconds", 300),
            large_download_minutes=cfg.get("large_download_minutes", 30),
            content_max_chars=cfg.get("content_max_chars", 3000),
            article_auto_threshold=cfg.get("article_auto_threshold", 5000),
            any2summary_dir=str(
                Path(cfg.get("any2summary_dir", str(_DEFAULT_ANY2SUMMARY_DIR))).expanduser()
            ),
            youtube_top_n=yt.get("top_n", 5),
            youtube_min_engagement_rate=yt.get("min_engagement_rate", 0.0),
            podcast_top_n=pod.get("top_n", 3),
            article_top_n=art.get("top_n", 10),
            article_timeout=art.get("timeout", 30),
            youtube_timeout=yt.get("timeout", 600),
            podcast_timeout=pod.get("timeout", xiao.get("timeout", 180)),
        )


class ContentEnricher:
    """Enriches ContentItems by calling any2summary for full text / transcripts."""

    def __init__(self, config: EnrichmentConfig) -> None:
        self.config = config
        self._any2summary_dir = Path(config.any2summary_dir)

    # ------------------------------------------------------------------
    # Duration helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_duration_seconds(duration_str: str | None) -> int:
        """Parse various duration formats into total seconds.

        Supported formats:
        - "HH:MM:SS"
        - "MM:SS"
        - Plain integer string (seconds)
        - Empty / None → 0
        """
        if not duration_str:
            return 0
        duration_str = str(duration_str).strip()
        if not duration_str:
            return 0

        # Plain integer
        if duration_str.isdigit():
            return int(duration_str)

        parts = duration_str.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, TypeError):
            pass
        return 0

    def is_large_download(self, item: ContentItem) -> bool:
        """Return True if the item represents a large download (>threshold minutes)."""
        duration = item.extra.get("duration", "")
        seconds = self.parse_duration_seconds(duration)
        return seconds > self.config.large_download_minutes * 60

    # ------------------------------------------------------------------
    # YouTube engagement rate
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_engagement_rate(item: ContentItem) -> float:
        """Calculate engagement rate from YouTube stats.

        If ``youtube_stats`` exists in extra (from Data API), uses
        ``(likeCount + commentCount) / max(viewCount, 1)``.
        Otherwise falls back to raw ``score`` (viewCount from RSS).
        """
        stats = item.extra.get("youtube_stats")
        if stats:
            views = max(int(stats.get("viewCount", 0)), 1)
            likes = int(stats.get("likeCount", 0))
            comments = int(stats.get("commentCount", 0))
            return (likes + comments) / views
        # Fallback: score is viewCount
        return item.score

    # ------------------------------------------------------------------
    # any2summary invocation
    # ------------------------------------------------------------------

    def _build_env(self) -> dict[str, str]:
        """Build environment for any2summary subprocess."""
        env = os.environ.copy()
        env.setdefault(
            "AZURE_OPENAI_SUMMARY_DEPLOYMENT", "llab-gpt-5-pro"
        )
        cookies_path = Path.home() / ".cache" / "any2summary" / "cookies.txt"
        env.setdefault("ANY2SUMMARY_YTDLP_COOKIES", str(cookies_path))
        if not cookies_path.exists():
            logger.warning(
                "[enricher] YouTube cookies file not found: %s "
                "(yt-dlp may fail for age-restricted videos)",
                cookies_path,
            )
        return env

    def _resolve_python(self) -> str:
        """Resolve the Python interpreter for any2summary.

        Prefers the any2summary project's own .venv, falls back to system python3.
        """
        venv_python = self._any2summary_dir / ".venv" / "bin" / "python3"
        if venv_python.exists():
            return str(venv_python)
        return "python3"

    def _build_cmd(self, url: str) -> list[str]:
        """Build the any2summary CLI command list."""
        prompts_dir = self._any2summary_dir / "prompts"
        cmd = [
            self._resolve_python(),
            "-m", "any2summary.cli",
            "--url", url,
            "--language", "zh",
            "--azure-summary",
            "--no-azure-streaming",
            "--summary-length", "standard",
        ]
        summary_prompt = prompts_dir / "summary_prompt.txt"
        if summary_prompt.exists():
            cmd.extend(["--summary-prompt-file", str(summary_prompt)])
        article_prompt = prompts_dir / "article_summary_prompt.txt"
        if article_prompt.exists():
            cmd.extend(["--article-summary-prompt-file", str(article_prompt)])
        return cmd

    def call_any2summary(self, url: str, timeout: int | None = None) -> dict[str, Any] | None:
        """Call any2summary CLI and return parsed JSON payload, or None on failure."""
        effective_timeout = timeout or self.config.timeout_seconds
        cmd = self._build_cmd(url)
        env = self._build_env()

        try:
            result = subprocess.run(
                cmd,
                env=env,
                cwd=str(self._any2summary_dir),
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
            if result.returncode != 0:
                logger.warning(
                    "[enricher] any2summary failed for %s (rc=%d): %s",
                    url, result.returncode,
                    (result.stderr or "")[:300],
                )
        except subprocess.TimeoutExpired:
            logger.warning("[enricher] any2summary timed out for %s", url)
        except json.JSONDecodeError as exc:
            logger.warning("[enricher] Invalid JSON from any2summary for %s: %s", url, exc)
        except Exception as exc:
            logger.warning("[enricher] Unexpected error calling any2summary for %s: %s", url, exc)
        return None

    # ------------------------------------------------------------------
    # Item enrichment
    # ------------------------------------------------------------------

    def enrich_item(self, item: ContentItem, timeout: int | None = None) -> bool:
        """Enrich a single ContentItem via any2summary.

        Returns True if the item content was updated.
        For podcast sources, prefers ``extra.audio_url`` over the webpage URL
        so that any2summary downloads audio directly instead of scraping.
        """
        if not item.url:
            return False

        # Podcast: prefer audio_url (direct audio file) over webpage URL
        url = item.url
        if item.source in ("apple_podcast", "xiaoyuzhou"):
            audio_url = item.extra.get("audio_url")
            if audio_url:
                url = audio_url

        data = self.call_any2summary(url, timeout=timeout)
        if data is None:
            return False

        # Try to extract summary text
        summary_text = self._extract_summary_text(data)
        if summary_text:
            item.content = summary_text[: self.config.content_max_chars]
            item.extra["enriched"] = True
            item.extra["any2summary_total_words"] = data.get("total_words", 0)
            return True

        return False

    @staticmethod
    def _extract_summary_text(data: dict[str, Any]) -> str:
        """Extract summary text from any2summary JSON payload."""
        # Primary: summary markdown string
        summary = data.get("summary")
        if summary and isinstance(summary, str) and len(summary.strip()) > 50:
            return summary.strip()

        # Secondary: read from summary_path file
        summary_path = data.get("summary_path")
        if summary_path:
            p = Path(summary_path)
            if p.exists():
                text = p.read_text(encoding="utf-8").strip()
                if text:
                    return text

        # Tertiary: concatenate segments
        segments = data.get("segments")
        if segments and isinstance(segments, list):
            texts = [s.get("text", "") for s in segments if isinstance(s, dict)]
            joined = " ".join(t for t in texts if t)
            if joined:
                return joined

        return ""

    # ------------------------------------------------------------------
    # Selection logic
    # ------------------------------------------------------------------

    def select_youtube_items(
        self, items: list[ContentItem],
    ) -> tuple[list[ContentItem], list[ContentItem]]:
        """Select YouTube items for enrichment.

        Returns (enrichable, manual_suggestions).
        """
        yt_items = [i for i in items if i.source == "youtube"]
        if not yt_items:
            return [], []

        # Sort by engagement rate descending
        yt_items.sort(key=self.calculate_engagement_rate, reverse=True)

        # Filter by min engagement rate
        min_rate = self.config.youtube_min_engagement_rate
        if min_rate > 0:
            yt_items = [
                i for i in yt_items
                if self.calculate_engagement_rate(i) >= min_rate
            ]

        enrichable = yt_items[: self.config.youtube_top_n]
        return enrichable, []

    def select_podcast_items(
        self, items: list[ContentItem],
    ) -> tuple[list[ContentItem], list[ContentItem]]:
        """Select podcast items for enrichment.

        Returns (enrichable, manual_suggestions) where manual_suggestions
        are items exceeding the large download threshold.
        """
        podcast_items = [
            i for i in items
            if i.source in ("apple_podcast", "xiaoyuzhou") and "podcast" in i.tags
        ]
        if not podcast_items:
            return [], []

        enrichable = []
        manual = []
        for item in podcast_items:
            if self.is_large_download(item):
                manual.append(item)
            else:
                enrichable.append(item)

        enrichable = enrichable[: self.config.podcast_top_n]
        return enrichable, manual

    def select_article_items(self, items: list[ContentItem]) -> list[ContentItem]:
        """Select article items for enrichment.

        Articles with content < article_auto_threshold chars are all selected.
        Remaining articles are selected by score up to article_top_n.
        """
        # Only English sources — cn_tech_blog and baoyu_blog are excluded
        # because they are RSS full-text and don't need enrichment.
        article_sources = {
            "anthropic", "openai", "google_blog", "manual_urls",
        }
        article_items = [
            i for i in items
            if i.source in article_sources or i.source_type.value == "http_scrape"
        ]
        if not article_items:
            return []

        threshold = self.config.article_auto_threshold
        auto_enrich = [i for i in article_items if len(i.content) < threshold]

        remaining = [i for i in article_items if len(i.content) >= threshold]
        remaining.sort(key=lambda x: x.score, reverse=True)
        top_remaining = remaining[: self.config.article_top_n]

        return auto_enrich + top_remaining

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------

    # Maximum consecutive failures before skipping remaining items in a category
    MAX_CONSECUTIVE_FAILURES = 3

    def _enrich_with_circuit_breaker(
        self, items: list[ContentItem], category: str,
        timeout: int | None = None,
    ) -> int:
        """Enrich items with circuit breaker — skip remaining after N consecutive failures."""
        consecutive_failures = 0
        enriched_count = 0
        for idx, item in enumerate(items):
            if consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                remaining = len(items) - idx
                logger.warning(
                    "[enricher] %d consecutive failures, skipping remaining %d %s items",
                    consecutive_failures, remaining, category,
                )
                break
            if self.enrich_item(item, timeout=timeout):
                consecutive_failures = 0
                enriched_count += 1
            else:
                consecutive_failures += 1
        return enriched_count

    def process_batch(
        self, all_items: list[ContentItem],
    ) -> tuple[list[ContentItem], list[ContentItem]]:
        """Process all items for enrichment.

        Returns (all_items_updated_in_place, manual_suggestions).

        Each category (articles, youtube, podcasts) has a circuit breaker:
        after ``MAX_CONSECUTIVE_FAILURES`` consecutive failures the remaining
        items in that category are skipped to avoid blocking the pipeline
        when Azure is temporarily unavailable.
        """
        manual_suggestions: list[ContentItem] = []

        # Articles
        article_items = self.select_article_items(all_items)
        enriched_count = self._enrich_with_circuit_breaker(
            article_items, "article", timeout=self.config.article_timeout,
        )
        if article_items:
            logger.info(
                "[enricher] Articles: %d/%d enriched",
                enriched_count, len(article_items),
            )

        # YouTube
        yt_enrichable, _ = self.select_youtube_items(all_items)
        yt_count = self._enrich_with_circuit_breaker(
            yt_enrichable, "youtube", timeout=self.config.youtube_timeout,
        )
        if yt_enrichable:
            logger.info(
                "[enricher] YouTube: %d/%d enriched",
                yt_count, len(yt_enrichable),
            )

        # Podcasts
        pod_enrichable, pod_manual = self.select_podcast_items(all_items)
        pod_count = self._enrich_with_circuit_breaker(
            pod_enrichable, "podcast", timeout=self.config.podcast_timeout,
        )
        manual_suggestions.extend(pod_manual)
        if pod_enrichable or pod_manual:
            logger.info(
                "[enricher] Podcasts: %d/%d enriched, %d manual suggestions",
                pod_count, len(pod_enrichable), len(pod_manual),
            )

        return all_items, manual_suggestions
