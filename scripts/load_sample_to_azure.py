"""Load the offline sample CSVs into the Azure SQL monitoring schema.

This gives Power BI live data to query (via DirectQuery) without having to run
the full public-API ingestion. Tables are loaded in foreign-key-safe order, and
identity / default columns are left for SQL Server to populate.

Run after `apply_schema.py`, with the AZURE_SQL_* env vars set:
    python scripts/load_sample_to_azure.py
"""

from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import text

from _db import make_engine

HERE = os.path.dirname(__file__)
SAMPLE = os.path.join(HERE, "..", "sample_data")

# (csv file, target table, date columns) in load order (parents first).
LOAD_PLAN = [
    ("monitoring_wells.csv", "monitoring_wells", []),
    ("water_licences.csv", "water_licences", ["licence_start_date", "licence_end_date"]),
    ("water_level_readings.csv", "water_level_readings", ["reading_date"]),
    ("rainfall_observations.csv", "rainfall_observations", ["obs_date"]),
    ("anomaly_events.csv", "anomaly_events", ["event_date"]),
    ("metered_extraction.csv", "metered_extraction", ["reading_month"]),
    ("water_security_risk.csv", "water_security_risk_snapshots", ["risk_snapshot_date"]),
]


def main() -> int:
    engine = make_engine()

    # Clear existing rows so re-running is idempotent (children before parents).
    with engine.begin() as conn:
        for table in [
            "water_security_risk_snapshots",
            "metered_extraction",
            "anomaly_events",
            "rainfall_observations",
            "water_level_readings",
            "water_licences",
            "monitoring_wells",
        ]:
            conn.execute(text(f"DELETE FROM monitoring.{table}"))

    for filename, table, date_cols in LOAD_PLAN:
        path = os.path.join(SAMPLE, filename)
        df = pd.read_csv(path, parse_dates=date_cols or None)
        df.to_sql(
            table,
            engine,
            schema="monitoring",
            if_exists="append",
            index=False,
            chunksize=500,
        )
        print(f"Loaded {len(df):>5} rows into monitoring.{table}")

    print("Sample data load complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
