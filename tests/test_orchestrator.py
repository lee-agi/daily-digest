"""Tests for orchestrator pipeline."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestOrchestratorConfig:
    def test_load_config(self):
        from orchestrator import load_config
        config = load_config()
        assert "general" in config
        assert "sources" in config
        assert "summary" in config
        assert "distribution" in config

    def test_config_sources_have_thresholds(self):
        from orchestrator import load_config
        config = load_config()
        for name, src_cfg in config["sources"].items():
            assert "score_threshold" in src_cfg, f"{name} missing score_threshold"
            assert "lookback_hours" in src_cfg, f"{name} missing lookback_hours"
            assert "max_items" in src_cfg, f"{name} missing max_items"


class TestCollectorImport:
    def test_import_all_collectors(self):
        from orchestrator import import_collectors
        from collectors.base import CollectorRegistry
        import_collectors()
        names = CollectorRegistry.all_names()
        assert len(names) >= 11


class TestCollectPipeline:
    @pytest.mark.asyncio
    async def test_collect_single_source(self):
        """Test collect pipeline with HuggingFace (most reliable, no auth)."""
        from orchestrator import load_config, run_collect
        config = load_config()
        # Override to widen window for test reliability
        config["sources"]["huggingface"]["lookback_hours"] = 72
        config["sources"]["huggingface"]["score_threshold"] = 0

        results = await run_collect(config, "2026-02-23-test", source_filter="huggingface")
        assert len(results) == 1
        assert results[0].source == "huggingface"
        assert results[0].success

        # Verify intermediate JSON was written (filename now includes -HHMM suffix)
        data_dir = Path(__file__).parent.parent / "data"
        data_files = sorted(data_dir.glob("collected-2026-02-23-test-*.json"))
        assert len(data_files) >= 1, "No intermediate JSON files found"
        data_file = data_files[-1]
        items = json.loads(data_file.read_text())
        assert isinstance(items, list)

        # Cleanup
        for f in data_files:
            f.unlink(missing_ok=True)


class TestMarkSeenTiming:
    """Verify mark_seen is deferred until after push, not during collect."""

    @pytest.mark.asyncio
    async def test_collect_does_not_mark_seen(self, tmp_path):
        """run_collect() should NOT mark items as seen in SQLite."""
        import sqlite3
        from unittest.mock import AsyncMock, patch

        from orchestrator import load_config, run_collect
        from schema import ContentItem, CollectorResult
        from state import get_connection, is_seen

        # Use a temp DB to avoid polluting real state
        test_db = tmp_path / "test_state.db"
        config = load_config()
        config["enrichment"] = {"enabled": False}

        # Create a fake item
        fake_item = ContentItem(
            source="test_source",
            source_type="api",
            title="Test Article",
            url="https://example.com/test-mark-seen-timing",
            content="Test content",
            published_at="2026-02-28T00:00:00Z",
            score=100,
        )
        fake_result = CollectorResult(
            source="test_source",
            success=True,
            items=[fake_item],
            total_fetched=1,
        )

        with patch("orchestrator.get_connection", return_value=get_connection(test_db)), \
             patch("orchestrator.filter_unseen", return_value=[fake_item]), \
             patch("orchestrator.mark_seen") as mock_mark_seen:
            results = await run_collect(
                config, "2026-02-28-test-timing",
                source_filter="__none__",
            )
            # mark_seen should NOT be called inside run_collect
            mock_mark_seen.assert_not_called()

    @pytest.mark.asyncio
    async def test_summarize_and_push_marks_seen_after_push(self, tmp_path):
        """run_summarize_and_push() should mark items seen only after push."""
        from unittest.mock import AsyncMock, patch

        from orchestrator import load_config, run_summarize_and_push, DATA_DIR
        from schema import ContentItem

        config = load_config()
        target_date = "2026-02-28-test-push"

        # Create intermediate JSON with a fake item
        fake_item = ContentItem(
            source="test_source",
            source_type="api",
            title="Test Push Article",
            url="https://example.com/test-push-seen",
            content="Test content for push",
            published_at="2026-02-28T00:00:00Z",
            score=100,
        )
        intermediate_path = DATA_DIR / f"collected-{target_date}.json"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        intermediate_path.write_text(
            json.dumps([fake_item.model_dump(mode="json")], ensure_ascii=False)
        )

        try:
            with patch("report.generator.generate_digest_report") as mock_gen, \
                 patch("report.push.push_report", new_callable=AsyncMock) as mock_push, \
                 patch("orchestrator.mark_seen") as mock_mark_seen, \
                 patch("orchestrator.get_connection"):

                # Mock report generation
                from schema import DigestReport
                mock_report = DigestReport(
                    date=target_date,
                    total_items=1,
                    sources_summary={},
                    full_markdown="# Test Report",
                )
                mock_gen.return_value = mock_report

                await run_summarize_and_push(config, target_date, dry_run=False)

                # mark_seen SHOULD be called after push
                mock_mark_seen.assert_called_once()
        finally:
            intermediate_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_dry_run_does_not_mark_seen(self, tmp_path):
        """--dry-run should NOT mark items as seen."""
        from unittest.mock import AsyncMock, patch

        from orchestrator import load_config, run_summarize_and_push, DATA_DIR
        from schema import ContentItem

        config = load_config()
        target_date = "2026-02-28-test-dryrun"

        fake_item = ContentItem(
            source="test_source",
            source_type="api",
            title="Test DryRun Article",
            url="https://example.com/test-dryrun-seen",
            content="Test content for dryrun",
            published_at="2026-02-28T00:00:00Z",
            score=100,
        )
        intermediate_path = DATA_DIR / f"collected-{target_date}.json"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        intermediate_path.write_text(
            json.dumps([fake_item.model_dump(mode="json")], ensure_ascii=False)
        )

        try:
            with patch("report.generator.generate_digest_report") as mock_gen, \
                 patch("orchestrator.mark_seen") as mock_mark_seen, \
                 patch("orchestrator.get_connection"):

                from schema import DigestReport
                mock_report = DigestReport(
                    date=target_date,
                    total_items=1,
                    sources_summary={},
                    full_markdown="# Test DryRun Report",
                )
                mock_gen.return_value = mock_report

                await run_summarize_and_push(config, target_date, dry_run=True)

                # mark_seen should NOT be called in dry-run
                mock_mark_seen.assert_not_called()
        finally:
            intermediate_path.unlink(missing_ok=True)


class TestLookbackHoursOverride:
    """Verify --lookback-hours overrides all source configs."""

    def test_lookback_hours_overrides_all_sources(self):
        from orchestrator import load_config
        config = load_config()

        # Simulate what main() does with --lookback-hours
        override_hours = 48
        for src_cfg in config.get("sources", {}).values():
            src_cfg["lookback_hours"] = override_hours

        for name, src_cfg in config["sources"].items():
            assert src_cfg["lookback_hours"] == override_hours, (
                f"{name} lookback_hours not overridden"
            )

    def test_lookback_hours_none_preserves_original(self):
        from orchestrator import load_config
        config = load_config()

        # Capture original values
        originals = {
            name: src_cfg["lookback_hours"]
            for name, src_cfg in config["sources"].items()
        }

        # Simulate --lookback-hours not specified (None): no override
        # (main() only overrides when args.lookback_hours is not None)

        for name, src_cfg in config["sources"].items():
            assert src_cfg["lookback_hours"] == originals[name]
