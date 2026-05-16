"""Series velocity tagging — bootstrap classification feeding EV Phase B.

Reads `config/series_velocity.yaml` and exposes a `lookup` for the scanner.
Untagged series fall through to the file's `defaults` block (HIGH/LOW per
the spec — fail closed).

v0 scope (per INFO_VELOCITY_TAGGING.md §11 + scoping decisions):
- Loader + lookup
- Static correlation_group strings (no template resolution)
- velocity_overrides field accepted in schema, NOT resolved (v1 work)
- `confidence` is preserved on the result so the CSV/dashboard can show it,
  but it does NOT affect EV math (Phase C dependency)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


class Velocity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class SeriesTag:
    """Per-series tag (or the default fallback). All fields populated."""

    series_ticker: str  # "" when this is the default fallback
    info_velocity: Velocity
    confidence: Confidence
    correlation_group: str | None
    notes: str
    is_default: bool  # True when the lookup fell through to defaults


@dataclass(frozen=True)
class VelocityRegistry:
    """Loaded velocity tags. Construct via `load_velocity_registry`."""

    default_velocity: Velocity
    default_confidence: Confidence
    _series: dict[str, SeriesTag]

    def lookup(self, series_ticker: str | None) -> SeriesTag:
        """Return the tag for a series, or a default fallback if untagged."""
        if series_ticker and series_ticker in self._series:
            return self._series[series_ticker]
        return SeriesTag(
            series_ticker=series_ticker or "",
            info_velocity=self.default_velocity,
            confidence=self.default_confidence,
            correlation_group=None,
            notes="",
            is_default=True,
        )

    @property
    def tagged_count(self) -> int:
        return len(self._series)


DEFAULT_CONFIG_PATH = Path("config/series_velocity.yaml")


def load_velocity_registry(path: Path = DEFAULT_CONFIG_PATH) -> VelocityRegistry:
    """Load the YAML at `path`. Missing file → empty registry, default HIGH/LOW.

    Empty/missing file is non-fatal: we still produce a registry, every
    series falls through to defaults (HIGH), and the scanner can proceed.
    The dashboard's "0 tagged series" indicator should make this visible.
    """
    if not path.exists():
        return VelocityRegistry(
            default_velocity=Velocity.HIGH,
            default_confidence=Confidence.LOW,
            _series={},
        )

    with path.open() as f:
        raw = yaml.safe_load(f) or {}

    defaults_block = raw.get("defaults") or {}
    default_velocity = Velocity(defaults_block.get("info_velocity", "high"))
    default_confidence = Confidence(defaults_block.get("confidence", "low"))

    series: dict[str, SeriesTag] = {}
    for ticker, entry in (raw.get("series") or {}).items():
        series[ticker] = _parse_entry(ticker, entry)

    return VelocityRegistry(
        default_velocity=default_velocity,
        default_confidence=default_confidence,
        _series=series,
    )


def _parse_entry(ticker: str, entry: Any) -> SeriesTag:
    """Tolerant parse: unknown fields ignored, missing fields default sensibly."""
    if not isinstance(entry, dict):
        # Malformed entry → treat as default
        return SeriesTag(
            series_ticker=ticker,
            info_velocity=Velocity.HIGH,
            confidence=Confidence.LOW,
            correlation_group=None,
            notes="(malformed entry; treated as default)",
            is_default=False,
        )
    return SeriesTag(
        series_ticker=ticker,
        info_velocity=Velocity(entry.get("info_velocity", "high")),
        confidence=Confidence(entry.get("confidence", "low")),
        correlation_group=entry.get("correlation_group"),
        notes=entry.get("notes", "") or "",
        is_default=False,
    )


@lru_cache(maxsize=1)
def get_default_registry() -> VelocityRegistry:
    """Cached load for hot paths. Tests should call load_velocity_registry directly."""
    return load_velocity_registry()
