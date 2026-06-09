"""Unit tests for the anomaly detectors.

These use small synthetic frames so the domain logic is verified without any
network or database dependency — the same way the function runs in CI.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Import the detectors module directly (without triggering the function
# package __init__, which needs the azure.functions runtime).
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "functions" / "anomaly_detector")
)

import detectors  # noqa: E402


@pytest.fixture(autouse=True)
def _deterministic_rng():
    """Seed the RNG so noise-based tests are reproducible, not flaky."""
    np.random.seed(0)


def _dates(n: int):
    start = date(2024, 1, 1)
    return [pd.Timestamp(start + timedelta(days=i)) for i in range(n)]


def _oscillate(n, centre=5.0, amp=0.01):
    """Deterministic small oscillation: non-zero variance, no randomness."""
    return [centre + amp * (1 if i % 2 == 0 else -1) for i in range(n)]


def test_rapid_level_change_flags_spike():
    d = _dates(20)
    # Stable around 5.0 mBGL, then a sharp drop (deeper) on the last day.
    levels = _oscillate(19) + [7.5]
    readings = pd.DataFrame(
        {
            "well_id": 1,
            "reading_date": d,
            "water_level_mbgl": levels,
            "tds_mg_per_l": 500.0,
            "coastal_flag": 0,
            "management_area": "Northern Adelaide Plains",
        }
    )
    events = detectors.detect_rapid_level_change(readings)
    assert len(events) == 1
    assert events.iloc[0]["anomaly_type"] == "RapidLevelChange"
    assert events.iloc[0]["severity"] == "CRITICAL"


def test_rapid_level_change_ignores_stable_series():
    d = _dates(20)
    levels = _oscillate(20)
    readings = pd.DataFrame(
        {
            "well_id": 1,
            "reading_date": d,
            "water_level_mbgl": levels,
            "tds_mg_per_l": 500.0,
            "coastal_flag": 0,
            "management_area": "Northern Adelaide Plains",
        }
    )
    assert detectors.detect_rapid_level_change(readings).empty


def test_low_recharge_response_flags_no_rebound():
    d = _dates(40)
    # Water table stays flat (no recharge) the whole time.
    readings = pd.DataFrame(
        {
            "well_id": 1,
            "reading_date": d,
            "water_level_mbgl": 6.0,
            "tds_mg_per_l": 500.0,
            "coastal_flag": 0,
            "management_area": "Barossa",
        }
    )
    # A clear rainfall event mid-series.
    rain = pd.DataFrame({"obs_date": d, "rainfall_mm": 0.0})
    rain.loc[10:14, "rainfall_mm"] = 10.0  # 7-day total well above 20mm
    events = detectors.detect_low_recharge_response(readings, rain)
    assert len(events) == 1
    assert events.iloc[0]["anomaly_type"] == "LowRechargeResponse"


def test_low_recharge_response_ignores_healthy_rebound():
    d = _dates(40)
    # Rain event early; table rises 1m well after the detected event date.
    levels = [6.0] * 22 + [5.0] * 18
    readings = pd.DataFrame(
        {
            "well_id": 1,
            "reading_date": d,
            "water_level_mbgl": levels,
            "tds_mg_per_l": 500.0,
            "coastal_flag": 0,
            "management_area": "Barossa",
        }
    )
    rain = pd.DataFrame({"obs_date": d, "rainfall_mm": 0.0})
    rain.loc[8:12, "rainfall_mm"] = 10.0
    assert detectors.detect_low_recharge_response(readings, rain).empty


def test_salinity_intrusion_flags_coastal_critical():
    d = _dates(30)
    tds = [500 + 5 * i for i in range(30)]  # steady rise of 5 mg/L per day
    readings = pd.DataFrame(
        {
            "well_id": 1,
            "reading_date": d,
            "water_level_mbgl": 6.0,
            "tds_mg_per_l": tds,
            "coastal_flag": 1,
            "management_area": "McLaren Vale",
        }
    )
    events = detectors.detect_salinity_intrusion(readings)
    assert len(events) == 1
    assert events.iloc[0]["anomaly_type"] == "SalinityIntrusionRisk"
    assert events.iloc[0]["severity"] == "CRITICAL"


def test_salinity_intrusion_ignores_flat_tds():
    d = _dates(30)
    readings = pd.DataFrame(
        {
            "well_id": 1,
            "reading_date": d,
            "water_level_mbgl": 6.0,
            "tds_mg_per_l": _oscillate(30, centre=500.0, amp=2.0),
            "coastal_flag": 1,
            "management_area": "McLaren Vale",
        }
    )
    assert detectors.detect_salinity_intrusion(readings).empty


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
