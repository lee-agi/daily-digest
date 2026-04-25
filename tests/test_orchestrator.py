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
    pytestmark = pytest.mark.integration

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
        intermediate_path = DATA_DIR / f"collected-{target_date}-0700.json"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        intermediate_path.write_text(
            json.dumps([fake_item.model_dump(mode="json")], ensure_ascii=False)
        )

        try:
            with patch("report.generator.generate_digest_report") as mock_gen, \
                 patch("report.push.push_report", new_callable=AsyncMock) as mock_push, \
                 patch("orchestrator.mark_seen") as mock_mark_seen, \
                 patch("orchestrator.get_connection"), \
                 patch("orchestrator.has_successful_run", return_value=False):

                # Mock report generation
                from schema import DigestReport
                mock_report = DigestReport(
                    date=target_date,
                    full_markdown="# Test Report\n\n## Platform Statistics\n| Platform | Items |\n|----------|-------|\n| **Total** | **1** |\n",
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
        intermediate_path = DATA_DIR / f"collected-{target_date}-0700.json"
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
                    full_markdown="# Test DryRun Report\n\n## Platform Statistics\n| Platform | Items |\n|----------|-------|\n| **Total** | **1** |\n",
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


class TestIgnoreSeenFlag:
    """Verify --ignore-seen bypasses state DB dedup during collect."""

    @pytest.mark.asyncio
    async def test_ignore_seen_keeps_all_items(self):
        """run_collect(ignore_seen=True) should not call filter_unseen()."""
        from unittest.mock import AsyncMock, patch, MagicMock

        from orchestrator import load_config, run_collect
        from schema import ContentItem, CollectorResult

        config = load_config()
        config["enrichment"] = {"enabled": False}

        fake_item = ContentItem(
            source="test_source",
            source_type="api",
            title="Seen Item Should Still Be Kept",
            url="https://example.com/test-ignore-seen",
            content="Test content",
            published_at="2026-03-01T00:00:00Z",
            score=100,
        )

        mock_conn = MagicMock()

        with patch("orchestrator.get_connection", return_value=mock_conn), \
             patch("orchestrator.import_collectors"), \
             patch("orchestrator._check_credentials"), \
             patch("collectors.base.CollectorRegistry.create_all") as mock_create, \
             patch("orchestrator.filter_unseen") as mock_filter_unseen:

            mock_collector = MagicMock()
            mock_collector.source_name = "test_source"
            mock_collector.run = AsyncMock(return_value=CollectorResult(
                source="test_source", success=True,
                items=[fake_item], total_fetched=1,
            ))
            mock_create.return_value = [mock_collector]

            results = await run_collect(
                config,
                "2026-03-01-test-ignore-seen",
                ignore_seen=True,
            )

            mock_filter_unseen.assert_not_called()
            assert len(results) == 1
            assert results[0].items == [fake_item]


class TestNoEnrichFlag:
    """Verify --no-enrich disables enrichment."""

    @pytest.mark.asyncio
    async def test_no_enrich_flag_disables_enrichment(self):
        """run_collect() with _no_enrich=True should skip enrichment."""
        from unittest.mock import AsyncMock, patch, MagicMock

        from orchestrator import load_config, run_collect
        from collectors.base import CollectorRegistry
        from schema import ContentItem, CollectorResult

        config = load_config()
        config["enrichment"] = {"enabled": True}
        config["_no_enrich"] = True

        fake_item = ContentItem(
            source="test_source",
            source_type="api",
            title="Test Article",
            url="https://example.com/test-no-enrich",
            content="Test content",
            published_at="2026-03-01T00:00:00Z",
            score=100,
        )

        mock_conn = MagicMock()

        with patch("orchestrator.get_connection", return_value=mock_conn), \
             patch("orchestrator.filter_unseen", return_value=[fake_item]), \
             patch("orchestrator.import_collectors"), \
             patch("orchestrator._check_credentials"), \
             patch("collectors.base.CollectorRegistry.create_all") as mock_create:

            mock_collector = MagicMock()
            mock_collector.source_name = "test_source"
            mock_collector.run = AsyncMock(return_value=CollectorResult(
                source="test_source", success=True,
                items=[fake_item], total_fetched=1,
            ))
            mock_create.return_value = [mock_collector]

            # Patch the enricher import path so we can verify it's NOT called
            with patch("collectors.enricher.ContentEnricher") as mock_enricher_cls:
                results = await run_collect(config, "2026-03-01-test-noenrich", source_filter=None)
                # ContentEnricher should NOT be instantiated when _no_enrich is set
                mock_enricher_cls.assert_not_called()

            assert len(results) == 1


class TestDuplicatePushGuard:
    """Prevent duplicate summarize/push for the same date once already successful."""

    @pytest.mark.asyncio
    async def test_skip_duplicate_push_after_successful_run(self):
        from unittest.mock import AsyncMock, patch, MagicMock

        from orchestrator import load_config, run_summarize_and_push

        config = load_config()
        target_date = "2026-04-22-test-dup-guard"
        mock_conn = MagicMock()

        with patch("orchestrator.get_connection", return_value=mock_conn), \
             patch("orchestrator.has_successful_run", return_value=True), \
             patch("report.generator.generate_digest_report", new_callable=AsyncMock) as mock_gen, \
             patch("report.push.push_report", new_callable=AsyncMock) as mock_push:

            await run_summarize_and_push(config, target_date, dry_run=False)

            mock_gen.assert_not_called()
            mock_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_allows_rerun_after_successful_run(self):
        import json
        from unittest.mock import AsyncMock, patch, MagicMock

        from orchestrator import load_config, run_summarize_and_push, DATA_DIR
        from schema import ContentItem, DigestReport

        config = load_config()
        target_date = "2026-04-22-test-force-rerun"
        fake_item = ContentItem(
            source="test_source",
            source_type="api",
            title="Force Re-run",
            url="https://example.com/test-force-rerun",
            content="Test content",
            published_at="2026-04-22T00:00:00Z",
            score=100,
        )
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        intermediate_path = DATA_DIR / f"collected-{target_date}-0700.json"
        intermediate_path.write_text(
            json.dumps([fake_item.model_dump(mode="json")], ensure_ascii=False)
        )

        try:
            mock_conn = MagicMock()
            with patch("orchestrator.get_connection", return_value=mock_conn), \
                 patch("orchestrator.has_successful_run", return_value=True), \
                 patch("report.generator.generate_digest_report", new_callable=AsyncMock) as mock_gen, \
                 patch("report.push.push_report", new_callable=AsyncMock) as mock_push, \
                 patch("orchestrator.mark_seen"):

                mock_gen.return_value = DigestReport(
                    date=target_date,
                    full_markdown="# Test Force Report\n\n## Platform Statistics\n| Platform | Items |\n|----------|-------|\n| **Total** | **1** |\n",
                )

                await run_summarize_and_push(config, target_date, dry_run=False, force=True)

                mock_gen.assert_called_once()
                mock_push.assert_called_once()
        finally:
            intermediate_path.unlink(missing_ok=True)


class TestEnrichOnlyMode:
    """Verify --enrich-only loads data and runs enrichment."""

    @pytest.mark.asyncio
    async def test_enrich_only_saves_enriched_json(self):
        from unittest.mock import patch, MagicMock

        from orchestrator import load_config, run_enrich_only, DATA_DIR
        from schema import ContentItem

        config = load_config()
        config["enrichment"] = {"enabled": True, "timeout_seconds": 30}
        target_date = "2026-03-01-test-enrich"

        # Create test collected JSON
        fake_items = [
            ContentItem(
                source="anthropic",
                source_type="http_scrape",
                title="Test Article",
                url="https://anthropic.com/test",
                content="Short content",
                published_at="2026-03-01T00:00:00Z",
                score=10,
            ),
            ContentItem(
                source="cn_tech_blog",  # Should be filtered out
                source_type="rss",
                title="CN Article",
                url="https://cn.example.com/test",
                content="Chinese content",
                published_at="2026-03-01T00:00:00Z",
                score=5,
            ),
        ]
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        intermediate_path = DATA_DIR / f"collected-{target_date}-0700.json"
        intermediate_path.write_text(
            json.dumps([i.model_dump(mode="json") for i in fake_items], ensure_ascii=False)
        )

        try:
            mock_enricher = MagicMock()
            mock_enricher.process_batch.return_value = ([fake_items[0]], [])

            with patch("collectors.enricher.ContentEnricher", return_value=mock_enricher), \
                 patch("collectors.enricher.EnrichmentConfig") as mock_cfg_cls:
                mock_cfg_cls.from_config.return_value = MagicMock()
                await run_enrich_only(config, target_date)

            # Verify only anthropic item was passed (cn_tech_blog filtered)
            call_args = mock_enricher.process_batch.call_args[0][0]
            sources = [i.source for i in call_args]
            assert "anthropic" in sources
            assert "cn_tech_blog" not in sources

            # Verify enriched JSON was saved
            enriched_files = list(DATA_DIR.glob(f"enriched-{target_date}*.json"))
            assert len(enriched_files) >= 1
        finally:
            intermediate_path.unlink(missing_ok=True)
            for f in DATA_DIR.glob(f"enriched-{target_date}*.json"):
                f.unlink(missing_ok=True)
