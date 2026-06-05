"""Upsert collected data frames into Azure SQL.

Loading strategy
----------------
Each frame is staged into a temporary table and then merged into the target
so the pipeline is idempotent: re-running the same window updates existing
rows instead of creating duplicates. Wells are upserted on their natural key
(source_dh_no); readings on (well_id, reading_date); rainfall on
(station_id, obs_date).
"""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def make_engine(connection_string: str) -> Engine:
    """Build a SQLAlchemy engine from an ODBC connection string."""
    if not connection_string:
        raise ValueError(
            "SQL_CONNECTION_STRING is empty. In Azure it is read from Key "
            "Vault; locally export it before running."
        )
    return create_engine(
        "mssql+pyodbc:///?odbc_connect=" + connection_string,
        fast_executemany=True,
    )


def _stage_and_merge(
    engine: Engine,
    df: pd.DataFrame,
    target_table: str,
    key_columns: list[str],
) -> int:
    """Stage a frame and MERGE it into the target table.

    Returns the number of source rows processed.
    """
    if df.empty:
        logger.info("Nothing to load into %s", target_table)
        return 0

    staging = f"#stg_{target_table.split('.')[-1]}"
    with engine.begin() as conn:
        df.to_sql(
            staging.lstrip("#"),
            conn,
            schema=None,
            if_exists="replace",
            index=False,
        )

        update_cols = [c for c in df.columns if c not in key_columns]
        set_clause = ", ".join(f"t.{c} = s.{c}" for c in update_cols)
        on_clause = " AND ".join(f"t.{k} = s.{k}" for k in key_columns)
        insert_cols = ", ".join(df.columns)
        insert_vals = ", ".join(f"s.{c}" for c in df.columns)

        merge_sql = f"""
            MERGE {target_table} AS t
            USING {staging.lstrip('#')} AS s
              ON {on_clause}
            WHEN MATCHED THEN UPDATE SET {set_clause}
            WHEN NOT MATCHED THEN
              INSERT ({insert_cols}) VALUES ({insert_vals});
        """
        conn.execute(text(merge_sql))
        conn.execute(text(f"DROP TABLE {staging.lstrip('#')}"))

    logger.info("Merged %d rows into %s", len(df), target_table)
    return len(df)


def load_wells(engine: Engine, wells: pd.DataFrame) -> dict[str, int]:
    """Upsert wells and return a map of source_dh_no -> well_id."""
    if not wells.empty:
        # Assign deterministic surrogate keys based on the natural key order so
        # the same drillhole always maps to the same well_id across runs.
        with engine.begin() as conn:
            existing = pd.read_sql(
                "SELECT well_id, source_dh_no FROM monitoring.monitoring_wells",
                conn,
            )
        next_id = (existing["well_id"].max() + 1) if not existing.empty else 1
        known = dict(zip(existing["source_dh_no"], existing["well_id"]))

        new_rows = wells[~wells["source_dh_no"].isin(known)].copy()
        for dh_no in new_rows["source_dh_no"]:
            known[dh_no] = next_id
            next_id += 1

        wells = wells.copy()
        wells.insert(0, "well_id", wells["source_dh_no"].map(known))
        _stage_and_merge(
            engine, wells, "monitoring.monitoring_wells", ["well_id"]
        )
        return known
    return {}


def load_readings(
    engine: Engine, readings: pd.DataFrame, well_id_map: dict[str, int]
) -> int:
    """Upsert water-level readings, resolving well_id from the natural key."""
    if readings.empty:
        return 0
    readings = readings.copy()
    readings["well_id"] = readings["source_dh_no"].map(well_id_map)
    readings = readings.drop(columns=["source_dh_no"]).dropna(subset=["well_id"])
    readings["well_id"] = readings["well_id"].astype(int)
    return _stage_and_merge(
        engine,
        readings,
        "monitoring.water_level_readings",
        ["well_id", "reading_date"],
    )


def load_rainfall(engine: Engine, rainfall: pd.DataFrame) -> int:
    return _stage_and_merge(
        engine, rainfall, "monitoring.rainfall_observations", ["station_id", "obs_date"]
    )


def load_surface_water(engine: Engine, surface: pd.DataFrame) -> int:
    return _stage_and_merge(
        engine,
        surface,
        "monitoring.surface_water_readings",
        ["site_id", "reading_datetime", "metric_name"],
    )
