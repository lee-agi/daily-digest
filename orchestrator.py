#!/usr/bin/env python3
"""Daily Digest Orchestrator.

Usage:
    python orchestrator.py --collect-only          # Phase 1: Collect from all sources
    python orchestrator.py --summarize-and-push    # Phase 2: LLM summarize + distribute
    python orchestrator.py --full                  # Both phases in sequence
    python orchestrator.py --full --dry-run        # Full run without pushing

Options:
    --date YYYY-MM-DD    Target date (default: today)
    --source NAME        Run only a specific source
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
from datetime import datetime, timezone
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


async def run_collect(
    config: dict,
    target_date: str,
    source_filter: str | None = None,
) -> list[CollectorResult]:
    """Phase 1: Run all enabled collectors and save intermediate JSON."""
    from collectors.base import CollectorRegistry

    import_collectors()

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

    # Dedup against state DB
    conn = get_connection()
    all_items: list[ContentItem] = []
    for result in results:
        if result.success and result.items:
            unseen = filter_unseen(conn, result.items)
            logger.info("[%s] %d unseen out of %d items",
                        result.source, len(unseen), len(result.items))
            result.items = unseen
            all_items.extend(unseen)

    # Save intermediate JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    intermediate_path = DATA_DIR / f"collected-{target_date}.json"
    items_data = [item.model_dump(mode="json") for item in all_items]
    intermediate_path.write_text(json.dumps(items_data, ensure_ascii=False, indent=2))
    logger.info("Saved %d items to %s", len(all_items), intermediate_path)

    # Mark all collected items as seen
    mark_seen(conn, all_items)
    conn.close()

    return results


async def run_summarize_and_push(
    config: dict,
    target_date: str,
    dry_run: bool = False,
) -> None:
    """Phase 2: Load intermediate JSON, generate LLM summary, distribute."""
    intermediate_path = DATA_DIR / f"collected-{target_date}.json"
    if not intermediate_path.exists():
        logger.error("No collected data for %s. Run --collect-only first.", target_date)
        sys.exit(1)

    items_data = json.loads(intermediate_path.read_text())
    items = [ContentItem.model_validate(d) for d in items_data]
    logger.info("Loaded %d items for summarization", len(items))

    if not items:
        logger.warning("No items to summarize for %s", target_date)
        return

    # Generate report
    from report.generator import generate_digest_report
    report = await generate_digest_report(items, config, target_date)

    # Save markdown report
    output_dir = Path(config["general"]["output_dir"]).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"digest-{target_date}.md"
    report_path.write_text(report.full_markdown, encoding="utf-8")
    logger.info("Report saved to %s", report_path)

    if dry_run:
        logger.info("[DRY-RUN] Skipping push to RSS/Feishu")
        return

    # Push to RSS Worker + Feishu
    from report.push import push_report
    await push_report(report, config)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Digest Orchestrator")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--collect-only", action="store_true",
                            help="Only collect from sources")
    mode_group.add_argument("--summarize-and-push", action="store_true",
                            help="Only summarize and push (requires prior collect)")
    mode_group.add_argument("--full", action="store_true",
                            help="Collect + summarize + push")

    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="Target date (default: today)")
    parser.add_argument("--source", default=None,
                        help="Run only a specific source")
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

    # Create run record
    run_id = str(uuid.uuid4())[:8]
    phase = "collect" if args.collect_only else "summarize" if args.summarize_and_push else "full"
    record = RunRecord(
        run_id=run_id,
        date=target_date,
        phase=phase,
        started_at=datetime.now(timezone.utc),
    )

    conn = get_connection()
    save_run(conn, record)

    try:
        if args.collect_only or args.full:
            results = await run_collect(config, target_date, args.source)
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
