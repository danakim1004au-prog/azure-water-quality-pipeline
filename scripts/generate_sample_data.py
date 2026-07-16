"""Generate a realistic synthetic dataset for offline demos.

The live pipeline pulls from public APIs, but reviewers (and Power BI Desktop)
often need data immediately and offline. This script generates seasonal
rainfall, recharge-driven water levels and three injected anomaly scenarios,
then writes the results to sample_data/.

It deliberately plants:
  * one rapid-level-change event,
  * one low-recharge-response well,
  * one coastal salinity-intrusion trend,
so the dashboard and detectors have something to show.

Run:  python scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import calendar
import math
import os
import random
from datetime import date, timedelta

random.seed(42)

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_data")
os.makedirs(OUT_DIR, exist_ok=True)

# A fixed reference date keeps the committed dataset and model metrics stable
# across machines and reruns. END is exclusive, so the latest reading is
# 7 June 2026.
REFERENCE_END_DATE = date(2026, 6, 8)
START = REFERENCE_END_DATE - timedelta(days=365 * 3)
END = REFERENCE_END_DATE
DAYS = (END - START).days

# Wells: (well_id, name, lat, lon, aquifer, area, coastal)
# Coordinates use localities within each management area so the map and area
# label agree. Aquifer labels are representative of the settings used in the
# demonstration.
WELLS = [
    (1, "Virginia NAP-01", -34.667, 138.553, "T1 Sand", "Northern Adelaide Plains", 0),
    (2, "Angle Vale NAP-02", -34.648, 138.643, "T2 Sand", "Northern Adelaide Plains", 0),
    (3, "Nuriootpa BAR-01", -34.476, 138.996, "Fractured Rock", "Barossa", 0),
    (4, "Tanunda BAR-02", -34.523, 138.960, "Fractured Rock", "Barossa", 0),
    (5, "Maslin Beach MLV-01", -35.225, 138.470, "Maslin Sand", "McLaren Vale", 1),
    (6, "Port Willunga MLV-02", -35.280, 138.462, "Port Willunga Fm", "McLaren Vale", 1),
]

# Daily-rainfall stations, one near each area (so the rainfall signal is local
# to the bores it explains, not from the far north/west of the state).
STATIONS = [
    ("23083", "Edinburgh", "Northern Adelaide Plains"),
    ("23321", "Nuriootpa", "Barossa"),
    ("23753", "Willunga", "McLaren Vale"),
]

# Demonstration groundwater licences for the 2025–26 water year. Values are
# representative and are not records from an operational licensing system.
LICENCES = [
    ("LIC-NAP-2025", "Northern Adelaide Plains", 2400.0),
    ("LIC-BAR-2025", "Barossa", 1400.0),
    ("LIC-MLV-2025", "McLaren Vale", 1100.0),
]

# Target full-year extraction as a share of allocation. The Northern Adelaide
# Plains scenario is slightly above allocation so the compliance logic has a
# known critical case; McLaren Vale sits in the watch range.
EXTRACTION_TARGET = {
    "Northern Adelaide Plains": 1.03,
    "Barossa": 0.76,
    "McLaren Vale": 0.92,
}


def daterange():
    for i in range(DAYS):
        yield START + timedelta(days=i)


def seasonal_rain(d: date) -> float:
    """Winter-dominant rainfall typical of a Mediterranean climate."""
    day_of_year = d.timetuple().tm_yday
    # Peak around July (day ~200 in southern hemisphere winter).
    seasonal = max(0.0, math.sin((day_of_year - 100) / 365 * 2 * math.pi))
    base = seasonal * 6.0
    # Most days are dry; occasional fronts.
    if random.random() < 0.25 + 0.3 * seasonal:
        return round(base + random.expovariate(1 / (4 + 8 * seasonal)), 1)
    return 0.0


def write_wells():
    path = os.path.join(OUT_DIR, "monitoring_wells.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(
            ["well_id", "source_dh_no", "location_name", "latitude",
             "longitude", "aquifer_name", "management_area", "coastal_flag"]
        )
        for wid, name, lat, lon, aq, area, coastal in WELLS:
            w.writerow([wid, f"DH{6620 + wid}", name, lat, lon, aq, area, coastal])
    return path


def write_rainfall(rain_by_date):
    path = os.path.join(OUT_DIR, "rainfall_observations.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["station_id", "station_name", "management_area", "obs_date",
                    "rainfall_mm", "data_quality_flag"])
        for sid, sname, area in STATIONS:
            for d in daterange():
                # Stations vary around the regional signal.
                val = max(0.0, rain_by_date[d] * random.uniform(0.6, 1.3))
                w.writerow([sid, sname, area, d.isoformat(), round(val, 1), "measured"])
    return path


def write_readings(rain_by_date):
    path = os.path.join(OUT_DIR, "water_level_readings.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["well_id", "reading_date", "water_level_mbgl",
                    "tds_mg_per_l", "pumping_event", "data_quality_flag"])

        for wid, name, lat, lon, aq, area, coastal in WELLS:
            base_level = 5.0 + wid * 0.5      # static baseline depth (mBGL)
            tds = 450 + coastal * 200
            rolling_rain = 0.0                 # recent-rainfall index

            for idx, d in enumerate(daterange()):
                # Decaying memory of recent rain: high after wet spells.
                rolling_rain = rolling_rain * 0.92 + rain_by_date[d]

                # Recharge makes the table shallower (smaller mBGL). The level
                # mean-reverts to base_level, modulated by recent rain, plus a
                # very slow long-term deepening trend.
                recharge_term = 0.02 * rolling_rain
                # Well 4 is the planted "low recharge" well: barely responds.
                if wid == 4:
                    recharge_term *= 0.05
                long_term_drawdown = 0.0004 * idx
                level = (
                    base_level - recharge_term + long_term_drawdown
                    + random.normalvariate(0, 0.03)
                )

                # Salinity baseline wobble.
                tds += random.normalvariate(0, 3)
                # Mild mean reversion so TDS does not random-walk away.
                tds += (450 + coastal * 200 - tds) * 0.02
                # Well 5 is the planted coastal intrusion: steady TDS climb in
                # the final 30 days.
                if wid == 5 and idx > DAYS - 30:
                    tds += 6.0

                # Well 1 gets a planted rapid drop (deepening) on the last day.
                lvl = level
                if wid == 1 and idx == DAYS - 1:
                    lvl = level + 2.5

                w.writerow([
                    wid, d.isoformat(), round(max(lvl, 0.1), 3),
                    round(max(tds, 100), 1), 0, "measured",
                ])
    return path


def write_licences():
    path = os.path.join(OUT_DIR, "water_licences.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow([
            "licence_id", "management_area", "licence_start_date",
            "licence_end_date", "annual_allocation_ml", "compliance_limit_pct",
        ])
        for licence_id, area, allocation in LICENCES:
            w.writerow([
                licence_id, area, "2025-07-01", "2026-06-30", allocation, 100.0,
            ])
    return path


def _month_starts(start: date, end: date):
    current = start.replace(day=1)
    while current < end:
        yield current
        current = (
            current.replace(year=current.year + 1, month=1)
            if current.month == 12
            else current.replace(month=current.month + 1)
        )


def write_metered_extraction():
    """Write monthly extraction for the active demonstration licence year."""
    path = os.path.join(OUT_DIR, "metered_extraction.csv")
    rng = random.Random(20260712)
    allocation_by_area = {area: value for _, area, value in LICENCES}
    wells_by_area = {
        area: [wid for wid, _, _, _, _, well_area, _ in WELLS if well_area == area]
        for area in allocation_by_area
    }

    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow([
            "well_id", "reading_month", "coverage_days", "extraction_ml",
            "data_quality_flag",
        ])
        for month in _month_starts(date(2025, 7, 1), END):
            days_in_month = calendar.monthrange(month.year, month.month)[1]
            next_month = (
                month.replace(year=month.year + 1, month=1)
                if month.month == 12
                else month.replace(month=month.month + 1)
            )
            coverage_days = min((END - month).days, (next_month - month).days)

            for area, well_ids in wells_by_area.items():
                area_monthly = (
                    allocation_by_area[area]
                    * EXTRACTION_TARGET[area]
                    / 12
                    * coverage_days
                    / days_in_month
                )
                for share, well_id in zip((0.52, 0.48), well_ids):
                    value = area_monthly * share * rng.uniform(0.98, 1.02)
                    w.writerow([
                        well_id, month.isoformat(), coverage_days,
                        round(value, 2), "measured",
                    ])
    return path


def main():
    rain_by_date = {d: seasonal_rain(d) for d in daterange()}
    paths = [
        write_wells(),
        write_rainfall(rain_by_date),
        write_readings(rain_by_date),
        write_licences(),
        write_metered_extraction(),
    ]
    print("Wrote sample data:")
    for p in paths:
        print("  -", os.path.normpath(p))


if __name__ == "__main__":
    main()
