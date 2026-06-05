"""CLI entry point for the end-to-end ingestion run.

This is the script the Data Factory pipeline invokes (as a custom activity /
container step). It can also be run locally:

    export SQL_CONNECTION_STRING='Driver={ODBC Driver 18 for SQL Server};...'
    python -m ingestion.run_ingestion --years 5

Steps:
    1. Collect groundwater wells + readings.
    2. Collect rainfall.
    3. Collect surface water (enrichment).
    4. Upsert everything into Azure SQL.

Each source is independent: a failure in one is logged and does not abort the
others, but the process exits non-zero if any source failed so the pipeline
surfaces the problem.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

from . import groundwater_client, load_to_sql, rainfall_client, surface_water_client
from .config import (
    MANAGEMENT_AREAS,
    RAINFALL_STATIONS,
    SETTINGS,
    IngestionWindow,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
logger = logging.getLogger("run_ingestion")

# Surface-water sites to enrich with (site_id, name, metric).
SURFACE_SITES = [
    ("A5040517", "Torrens at Holbrooks Road", "level_m"),
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the water-data ingestion.")
    parser.add_argument(
        "--years", type=int, default=5, help="History length to collect."
    )
    parser.add_argument(
        "--skip-surface",
        action="store_true",
        help="Skip the surface-water enrichment source.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    window = IngestionWindow(
        start=date.today() - timedelta(days=365 * args.years),
        end=date.today(),
    )
    logger.info("Ingestion window: %s to %s", window.start, window.end)

    engine = load_to_sql.make_engine(SETTINGS.sql_connection_string)
    failures: list[str] = []

    # 1 + 2: groundwater
    try:
        wells, readings = groundwater_client.collect_all(
            SETTINGS, window, MANAGEMENT_AREAS
        )
        well_id_map = load_to_sql.load_wells(engine, wells)
        load_to_sql.load_readings(engine, readings, well_id_map)
    except Exception:  # noqa: BLE001
        logger.exception("Groundwater ingestion failed")
        failures.append("groundwater")

    # 3: rainfall
    try:
        rainfall = rainfall_client.collect_all(SETTINGS, window, RAINFALL_STATIONS)
        load_to_sql.load_rainfall(engine, rainfall)
    except Exception:  # noqa: BLE001
        logger.exception("Rainfall ingestion failed")
        failures.append("rainfall")

    # 4: surface water (optional)
    if not args.skip_surface:
        try:
            surface = surface_water_client.collect_all(SETTINGS, window, SURFACE_SITES)
            load_to_sql.load_surface_water(engine, surface)
        except Exception:  # noqa: BLE001
            logger.exception("Surface-water ingestion failed")
            failures.append("surface-water")

    if failures:
        logger.error("Ingestion completed with failures: %s", ", ".join(failures))
        return 1

    logger.info("Ingestion completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
