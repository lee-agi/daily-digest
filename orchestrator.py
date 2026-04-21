#!/usr/bin/env python3
"""Daily Digest Orchestrator.

Usage:
    python orchestrator.py --collect-only          # Phase 1: Collect from all sources
    python orchestrator.py --summarize-and-push    # Phase 2: LLM summarize + distribute
    python orchestrator.py --full                  # Both phases in sequence
    python orchestrator.py --full --dry-run        # Full run without pushing
    python orchestrator.py --full --no-enrich      # Full run, skip enrichment
    python orchestrator.py --enrich-only           # Standalone enrichment on latest data

Options:
    --date YYYY-MM-DD    Target date (default: today)
    --source NAME        Run only a specific source
    --no-enrich          Skip content enrichment (even if config enables it)
    --dry-run            Don't push to RSS/Feishu
    --verbose            Debug logging
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from schema import CollectorResult, ContentItem, RunRecord
from state import (
    DB_PATH,
    filter_unseen,
    get_connection,
    get_latest_run,
    mark_seen,
    save_run,
)

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("daily-digest")

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"


def _inject_urls(urls: list[str]) -> None:
    """Append URLs to data/pending_urls.yaml for ManualURLCollector."""
    pending_path = DATA_DIR / "pending_urls.yaml"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    data: dict = {"pending": [], "processed_archive": []}
    if pending_path.exists():
        try:
            loaded = yaml.safe_load(pending_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
                data.setdefault("pending", [])
                data.setdefault("processed_archive", [])
        except Exception:
            pass

    existing = {e.get("url") for e in data["pending"]}
    existing |= {e.get("url") for e in data["processed_archive"]}

    today = datetime.now().strftime("%Y-%m-%d")
    added = 0
    for url in urls:
        url = url.strip()
        if url and url not in existing:
            data["pending"].append({
                "url": url,
                "added_at": today,
                "processed": False,
            })
            existing.add(url)
            added += 1

    pending_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    logger.info("Injected %d new URL(s) into pending_urls.yaml", added)


def _save_manual_suggestions(
    target_date: str, items: list,
) -> None:
    """Save items that need manual deep processing to a JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"manual_suggestions-{target_date}.json"
    suggestions = []
    for item in items:
        suggestions.append({
            "title": item.title,
            "url": item.url,
            "source": item.source,
            "duration": item.extra.get("duration", ""),
            "reason": "large_download (>{} min)".format(30),
        })
    path.write_text(json.dumps(suggestions, ensure_ascii=False, indent=2))
    logger.info(
        "📋 %d items need manual deep processing: %s",
        len(suggestions), path,
    )


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def import_collectors() -> None:
    """Import all collector modules to trigger auto-registration."""
    import importlib
    collectors_dir = PROJECT_ROOT / "collectors"
    for py_file in collectors_dir.glob("*.py"):
        if py_file.name.startswith("_") or py_file.name == "base.py":
            continue
        module_name = f"collectors.{py_file.stem}"
        try:
            importlib.import_module(module_name)
        except ImportError as e:
            logger.warning("Failed to import %s: %s", module_name, e)


def _check_credentials(config: dict) -> None:
    """Log credential status for each enabled source. INFO only, non-blocking."""
    import os

    cred_map: dict[str, list[tuple[str, str]]] = {
        "x_twitter": [
            ("TWITTER_API_IO_KEY", "TwitterAPI.io primary"),
            ("X_AUTH_TOKEN", "twikit fallback"),
        ],
        "github": [("GITHUB_TOKEN", "PAT")],
        "reddit": [
            ("REDDIT_CLIENT_ID", "OAuth (optional, public API fallback works)"),
        ],
        "youtube": [("YOUTUBE_DATA_API_KEY", "Data API v3 (required)")],
        "producthunt": [("PRODUCTHUNT_API_TOKEN", "Bearer Token")],
        "zhihu": [],  # uses cookies file, not env var
    }

    sources_cfg = config.get("sources", {})
    for source_name, creds in cred_map.items():
        src = sources_cfg.get(source_name, {})
        if not src.get("enabled", False):
            continue
        for env_var, desc in creds:
            val = os.environ.get(env_var, "").strip()
            if val:
                logger.info(
                    "[credentials] %s: %s (%s) = set", source_name, env_var, desc,
                )
            else:
                logger.warning(
                    "[credentials] %s: %s (%s) = MISSING",
                    source_name, env_var, desc,
                )


async def run_collect(
    config: dict,
    target_date: str,
    source_filter: str | None = None,
    ignore_seen: bool = False,
) -> list[CollectorResult]:
    """Phase 1: Run all enabled collectors and save intermediate JSON."""
    from collectors.base import CollectorRegistry

    import_collectors()

    # Log credential status before running collectors
    _check_credentials(config)

    collectors = CollectorRegistry.create_all(config)
    if source_filter:
        collectors = [c for c in collectors if c.source_name == source_filter]

    if not collectors:
        logger.warning("No collectors to run.")
        return []

    logger.info("Running %d collectors: %s",
                len(collectors),
                [c.source_name for c in collectors])

    # Run all collectors concurrently
    results: list[CollectorResult] = await asyncio.gather(
        *(c.run() for c in collectors)
    )

    # Dedup against state DB unless explicitly disabled (useful for historical backfills)
    conn = get_connection()
    all_items: list[ContentItem] = []
    for result in results:
        if result.success and result.items:
            if ignore_seen:
                logger.info("[%s] ignore_seen=true, keeping all %d items",
                            result.source, len(result.items))
                all_items.extend(result.items)
            else:
                unseen = filter_unseen(conn, result.items)
                logger.info("[%s] %d unseen out of %d items",
                            result.source, len(unseen), len(result.items))
                result.items = unseen
                all_items.extend(unseen)

    # Content enrichment phase (skipped when --no-enrich is active)
    manual_suggestions: list = []
    enrichment_cfg = config.get("enrichment", {})
    if enrichment_cfg.get("enabled", False) and not config.get("_no_enrich", False):
        from collectors.enricher import ContentEnricher, EnrichmentConfig
        ecfg = EnrichmentConfig.from_config(enrichment_cfg)
        enricher = ContentEnricher(ecfg)
        all_items, manual_suggestions = enricher.process_batch(all_items)
        if manual_suggestions:
            _save_manual_suggestions(target_date, manual_suggestions)

    # Save intermediate JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M")
    intermediate_path = DATA_DIR / f"collected-{target_date}-{ts}.json"
    items_data = [item.model_dump(mode="json") for item in all_items]
    intermediate_path.write_text(json.dumps(items_data, ensure_ascii=False, indent=2))
    logger.info("Saved %d items to %s", len(all_items), intermediate_path)

    # NOTE: mark_seen is deferred to run_summarize_and_push() after push success.
    # This prevents re-runs from losing items due to premature seen marking.
    conn.close()

    return results


async def run_summarize_and_push(
    config: dict,
    target_date: str,
    dry_run: bool = False,
) -> None:
    """Phase 2: Load intermediate JSON, generate LLM summary, distribute."""
    candidates = sorted(
        [p for p in DATA_DIR.glob(f"collected-{target_date}*.json") if p.name.startswith(f"collected-{target_date}-")],
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        logger.error("No collected data for %s. Run --collect-only first.", target_date)
        sys.exit(1)
    intermediate_path = candidates[-1]
    logger.info("Using intermediate file: %s", intermediate_path.name)

    items_data = json.loads(intermediate_path.read_text())
    items = [ContentItem.model_validate(d) for d in items_data]
    logger.info("Loaded %d items for summarization", len(items))

    if not items:
        logger.warning("No items to summarize for %s", target_date)
        return

    # Generate report
    from report.generator import generate_digest_report
    report = await generate_digest_report(items, config, target_date)

    # Refuse to save/push obviously truncated markdown.
    # We saw cases where a broken/partial LLM response ended mid-sentence and still
    # got written to disk + pushed to RSS. Guard before any external side effects.
    from report.generator import is_truncated_report
    if is_truncated_report(report.full_markdown):
        logger.error("Generated report looks truncated; aborting save/push for %s", target_date)
        sys.exit(2)

    # Save markdown report
    output_dir = Path(config["general"]["output_dir"]).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    beijing_tz = timezone(timedelta(hours=8))
    time_suffix = report.generated_at.astimezone(beijing_tz).strftime("%H%M")
    report_path = output_dir / f"digest-{target_date}-{time_suffix}.md"
    report_path.write_text(report.full_markdown, encoding="utf-8")
    logger.info("Report saved to %s", report_path)

    if dry_run:
        logger.info("[DRY-RUN] Skipping push to RSS/Feishu (items NOT marked seen)")
        return

    # Push to RSS Worker + Feishu
    from report.push import push_report
    await push_report(report, config)

    # Mark items as seen ONLY after successful push.
    # This prevents re-runs/dry-runs from losing items.
    conn = get_connection()
    mark_seen(conn, items)
    conn.close()
    logger.info("Marked %d items as seen after successful push", len(items))


# Sources eligible for enrichment in --enrich-only mode
_ENRICH_ONLY_SOURCES = {
    "anthropic", "openai", "google_blog", "manual_urls",  # English articles
    "youtube",
    "xiaoyuzhou",
    "apple_podcast",
}


async def run_enrich_only(config: dict, target_date: str) -> None:
    """Standalone enrichment: load latest collected JSON, enrich, save result."""
    candidates = sorted(
        [p for p in DATA_DIR.glob(f"collected-{target_date}*.json") if p.name.startswith(f"collected-{target_date}-")],
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        logger.error("No collected data for %s. Run --collect-only or --full first.", target_date)
        sys.exit(1)

    intermediate_path = candidates[-1]
    logger.info("Loading items from: %s", intermediate_path.name)

    items_data = json.loads(intermediate_path.read_text())
    all_items = [ContentItem.model_validate(d) for d in items_data]
    logger.info("Loaded %d total items", len(all_items))

    # Filter to enrichment-eligible sources only
    filtered = [i for i in all_items if i.source in _ENRICH_ONLY_SOURCES]
    logger.info("Filtered to %d enrichment-eligible items (from %d)",
                len(filtered), len(all_items))

    if not filtered:
        logger.warning("No enrichment-eligible items found for %s", target_date)
        return

    enrichment_cfg = config.get("enrichment", {})
    from collectors.enricher import ContentEnricher, EnrichmentConfig
    ecfg = EnrichmentConfig.from_config(enrichment_cfg)
    enricher = ContentEnricher(ecfg)
    enriched_items, manual_suggestions = enricher.process_batch(filtered)

    if manual_suggestions:
        _save_manual_suggestions(target_date, manual_suggestions)

    # Save enriched results
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M")
    enriched_path = DATA_DIR / f"enriched-{target_date}-{ts}.json"
    enriched_data = [item.model_dump(mode="json") for item in enriched_items]
    enriched_path.write_text(json.dumps(enriched_data, ensure_ascii=False, indent=2))
    logger.info("Saved %d enriched items to %s", len(enriched_items), enriched_path)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Digest Orchestrator")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--collect-only", action="store_true",
                            help="Only collect from sources")
    mode_group.add_argument("--summarize-and-push", action="store_true",
                            help="Only summarize and push (requires prior collect)")
    mode_group.add_argument("--full", action="store_true",
                            help="Collect + summarize + push")
    mode_group.add_argument("--enrich-only", action="store_true",
                            help="Only run content enrichment on latest collected data")

    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="Target date (default: today)")
    parser.add_argument("--source", default=None,
                        help="Run only a specific source")
    parser.add_argument("--inject-url", action="append", default=[],
                        dest="inject_urls",
                        help="Inject URL(s) into pending_urls.yaml (repeatable)")
    parser.add_argument("--lookback-hours", type=int, default=None,
                        dest="lookback_hours",
                        help="Override lookback_hours for all sources")
    parser.add_argument("--no-enrich", action="store_true",
                        dest="no_enrich",
                        help="Disable content enrichment even if config enables it")
    parser.add_argument("--ignore-seen", action="store_true",
                        dest="ignore_seen",
                        help="Skip seen-item filtering during collect (for historical backfills)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't push to RSS/Feishu")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(PROJECT_ROOT / "logs" / "digest.log"),
        ]
    )

    config = load_config()
    target_date = args.date

    # Override lookback_hours for all sources if specified
    if args.lookback_hours is not None:
        for src_cfg in config.get("sources", {}).values():
            src_cfg["lookback_hours"] = args.lookback_hours
        logger.info("Overriding lookback_hours=%d for all sources", args.lookback_hours)

    # Handle --inject-url: append to pending_urls.yaml
    if args.inject_urls:
        _inject_urls(args.inject_urls)

    # Propagate --no-enrich flag via config internal key
    if args.no_enrich:
        config["_no_enrich"] = True

    # Create run record
    run_id = str(uuid.uuid4())[:8]
    if args.enrich_only:
        phase = "enrich"
    elif args.collect_only:
        phase = "collect"
    elif args.summarize_and_push:
        phase = "summarize"
    else:
        phase = "full"
    record = RunRecord(
        run_id=run_id,
        date=target_date,
        phase=phase,
        started_at=datetime.now(timezone.utc),
    )

    conn = get_connection()
    save_run(conn, record)

    try:
        if args.enrich_only:
            await run_enrich_only(config, target_date)
        else:
            if args.collect_only or args.full:
                results = await run_collect(
                    config, target_date, args.source,
                    ignore_seen=args.ignore_seen,
                )
                record.collector_results = results

            if args.summarize_and_push or args.full:
                await run_summarize_and_push(config, target_date, args.dry_run)

        record.status = "success"
    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        record.status = "failed"
        record.error = str(e)
        raise
    finally:
        record.finished_at = datetime.now(timezone.utc)
        save_run(conn, record)
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
