from __future__ import annotations

from report.source_validation import load_source_registry


def test_load_source_registry_uses_yaml_overrides(tmp_path) -> None:
    registry = tmp_path / "source_registry.yaml"
    registry.write_text(
        """
default_source_weight: 0.42
source_weights:
  example: 1.7
tier_weights:
  T1: 1.8
tiers:
  T1:
    sources: [example]
    hosts: [example.com]
""",
        encoding="utf-8",
    )

    loaded = load_source_registry(registry)

    assert loaded["default_source_weight"] == 0.42
    assert loaded["source_weights"]["example"] == 1.7
    assert loaded["tier_weights"]["T1"] == 1.8
    assert "example" in loaded["tier_sources"]["T1"]
    assert "example.com" in loaded["tier_hosts"]["T1"]


def test_load_source_registry_falls_back_on_invalid_yaml(tmp_path) -> None:
    registry = tmp_path / "source_registry.yaml"
    registry.write_text("source_weights: [", encoding="utf-8")

    loaded = load_source_registry(registry)

    assert loaded["default_source_weight"] == 0.6
    assert loaded["source_weights"]["openai"] == 1.6
    assert loaded["tier_weights"]["T3"] == 0.90
