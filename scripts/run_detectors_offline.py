"""Run the anomaly detectors against the local sample CSVs (no Azure needed).

Useful for demos and for regenerating an anomaly_events.csv that Power BI can
load alongside the other sample tables.

Run:  python scripts/run_detectors_offline.py
"""

from __future__ import annotations

import os
import sys

import pandas as pd

HERE = os.path.dirname(__file__)
SAMPLE = os.path.join(HERE, "..", "sample_data")
sys.path.insert(0, os.path.join(HERE, "..", "functions", "anomaly_detector"))

import detectors  # noqa: E402


def main() -> int:
    wells = pd.read_csv(os.path.join(SAMPLE, "monitoring_wells.csv"))
    readings = pd.read_csv(
        os.path.join(SAMPLE, "water_level_readings.csv"),
        parse_dates=["reading_date"],
    )
    rainfall = pd.read_csv(
        os.path.join(SAMPLE, "rainfall_observations.csv"),
        parse_dates=["obs_date"],
    )

    # Join well attributes the detectors rely on.
    readings = readings.merge(
        wells[["well_id", "coastal_flag", "management_area"]],
        on="well_id",
        how="left",
    )
    # Regional rainfall signal (average across stations).
    rainfall = (
        rainfall.groupby("obs_date", as_index=False)["rainfall_mm"].mean()
    )

    events = detectors.run_all(readings, rainfall)

    out_path = os.path.join(SAMPLE, "anomaly_events.csv")
    events.to_csv(out_path, index=False)

    print(f"Detected {len(events)} events. Written to {os.path.normpath(out_path)}")
    if not events.empty:
        print(events.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
