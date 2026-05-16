"""Tests for the velocity tag loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from kalshi_ws.intel.velocity import (
    Confidence,
    Velocity,
    VelocityRegistry,
    load_velocity_registry,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_missing_file_returns_high_default_registry(tmp_path: Path) -> None:
    """Failing closed: no file means every series defaults to HIGH."""
    reg = load_velocity_registry(tmp_path / "does_not_exist.yaml")
    assert reg.tagged_count == 0
    tag = reg.lookup("KXANYTHING")
    assert tag.info_velocity == Velocity.HIGH
    assert tag.confidence == Confidence.LOW
    assert tag.is_default is True


def test_loads_tagged_series_and_returns_real_tag(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "velocity.yaml",
        """
defaults:
  info_velocity: high
  confidence: low
series:
  KXHIGHNY:
    info_velocity: low
    confidence: high
    correlation_group: nyc_weather
    notes: Central Park station
""",
    )
    reg = load_velocity_registry(f)
    tag = reg.lookup("KXHIGHNY")
    assert tag.info_velocity == Velocity.LOW
    assert tag.confidence == Confidence.HIGH
    assert tag.correlation_group == "nyc_weather"
    assert tag.notes == "Central Park station"
    assert tag.is_default is False


def test_untagged_series_falls_through_to_defaults(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "velocity.yaml",
        """
defaults:
  info_velocity: high
  confidence: low
series:
  KXHIGHNY:
    info_velocity: low
    confidence: high
""",
    )
    reg = load_velocity_registry(f)
    tag = reg.lookup("KXNOTLISTED")
    assert tag.info_velocity == Velocity.HIGH
    assert tag.confidence == Confidence.LOW
    assert tag.is_default is True


def test_none_series_ticker_returns_default(tmp_path: Path) -> None:
    """Markets without an event_ticker (and thus no series) should get defaults."""
    f = _write(
        tmp_path / "velocity.yaml",
        "defaults:\n  info_velocity: high\n  confidence: low\nseries: {}\n",
    )
    reg = load_velocity_registry(f)
    tag = reg.lookup(None)
    assert tag.info_velocity == Velocity.HIGH
    assert tag.is_default is True


def test_defaults_block_missing_uses_high_low(tmp_path: Path) -> None:
    """No defaults block in YAML → still fail closed at HIGH / LOW."""
    f = _write(tmp_path / "velocity.yaml", "series:\n  KX-A:\n    info_velocity: low\n")
    reg = load_velocity_registry(f)
    assert reg.default_velocity == Velocity.HIGH
    assert reg.default_confidence == Confidence.LOW


def test_malformed_entry_treated_as_default(tmp_path: Path) -> None:
    """A series whose entry is a string instead of a dict should not crash."""
    f = _write(
        tmp_path / "velocity.yaml",
        "series:\n  KX-A: 'malformed should be dict'\n",
    )
    reg = load_velocity_registry(f)
    tag = reg.lookup("KX-A")
    assert tag.info_velocity == Velocity.HIGH
    assert "malformed" in tag.notes


def test_unknown_velocity_value_raises(tmp_path: Path) -> None:
    """Garbage values should fail loudly, not silently pass."""
    f = _write(
        tmp_path / "velocity.yaml",
        "series:\n  KX-A:\n    info_velocity: badvalue\n",
    )
    with pytest.raises(ValueError):
        load_velocity_registry(f)


def test_empty_yaml_returns_empty_registry(tmp_path: Path) -> None:
    f = _write(tmp_path / "velocity.yaml", "")
    reg = load_velocity_registry(f)
    assert reg.tagged_count == 0
    assert reg.default_velocity == Velocity.HIGH


def test_velocity_overrides_field_accepted_but_ignored(tmp_path: Path) -> None:
    """v0 schema is forward-compatible with overrides — must not crash."""
    f = _write(
        tmp_path / "velocity.yaml",
        """
series:
  KX-A:
    info_velocity: low
    confidence: medium
    velocity_overrides:
      - condition: month in [8, 9, 10]
        info_velocity: medium
""",
    )
    reg = load_velocity_registry(f)
    tag = reg.lookup("KX-A")
    # Base velocity is what we get; overrides are ignored in v0.
    assert tag.info_velocity == Velocity.LOW


def test_loads_committed_config_file() -> None:
    """The actual config/series_velocity.yaml in the repo must parse cleanly."""
    reg = load_velocity_registry()
    assert reg.tagged_count >= 10
    # Spot-check a few known entries
    ny = reg.lookup("KXHIGHNY")
    assert ny.info_velocity == Velocity.LOW
    assert ny.correlation_group == "nyc_weather"
    rt = reg.lookup("KXMOVIESCORE")
    assert rt.info_velocity == Velocity.HIGH
    assert isinstance(reg, VelocityRegistry)
