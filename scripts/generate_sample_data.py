"""Generate a realistic synthetic dataset for offline demos.

The live pipeline pulls from public APIs, but reviewers (and Power BI Desktop)
often need data immediately and offline. This script fabricates hydrologically
plausible series — seasonal rainfall, recharge-driven water-level response,
and a couple of injected anomalies — and writes CSVs to sample_data/.

It deliberately plants:
  * one rapid-level-change event,
  * one low-recharge-response well,
  * one coastal salinity-intrusion trend,
so the dashboard and detectors have something to show.

Run:  python scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import math
import os
import random
from datetime import date, timedelta

random.seed(42)

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_data")
os.makedirs(OUT_DIR, exist_ok=True)

START = date.today() - timedelta(days=365 * 3)
END = date.today()
DAYS = (END - START).days

# Wells: (well_id, name, lat, lon, aquifer, area, coastal)
WELLS = [
    (1, "Adelaide Plains AP-01", -34.78, 138.62, "T1 Sand", "Central Adelaide", 0),
    (2, "Adelaide Plains AP-02", -34.83, 138.58, "T2 Sand", "Central Adelaide", 0),
    (3, "Barossa BV-01", -34.53, 138.95, "Fractured Rock", "Barossa", 0),
    (4, "Barossa BV-02", -34.49, 138.99, "Fractured Rock", "Barossa", 0),
    (5, "McLaren Vale MV-01", -35.21, 138.54, "Maslin Sand", "McLaren Vale", 1),
    (6, "McLaren Vale MV-02", -35.24, 138.51, "Port Willunga Fm", "McLaren Vale", 1),
]

STATIONS = [("23090", "Kent Town"), ("18201", "Port Augusta"), ("18012", "Ceduna")]


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
        w = csv.writer(f)
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
        w = csv.writer(f)
        w.writerow(["station_id", "station_name", "obs_date", "rainfall_mm",
                    "data_quality_flag"])
        for sid, sname in STATIONS:
            for d in daterange():
                # Stations vary around the regional signal.
                val = max(0.0, rain_by_date[d] * random.uniform(0.6, 1.3))
                w.writerow([sid, sname, d.isoformat(), round(val, 1), "measured"])
    return path


def write_readings(rain_by_date):
    path = os.path.join(OUT_DIR, "water_level_readings.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
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


def main():
    rain_by_date = {d: seasonal_rain(d) for d in daterange()}
    paths = [
        write_wells(),
        write_rainfall(rain_by_date),
        write_readings(rain_by_date),
    ]
    print("Wrote sample data:")
    for p in paths:
        print("  -", os.path.normpath(p))


if __name__ == "__main__":
    main()
