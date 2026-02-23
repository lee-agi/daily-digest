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

        # Verify intermediate JSON was written
        data_file = Path(__file__).parent.parent / "data" / "collected-2026-02-23-test.json"
        assert data_file.exists()
        items = json.loads(data_file.read_text())
        assert isinstance(items, list)

        # Cleanup
        data_file.unlink(missing_ok=True)
