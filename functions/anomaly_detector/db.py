"""Database access for the anomaly-detection function.

The connection string is resolved in this order:
  1. SQL_CONNECTION_STRING env var (local development / CI).
  2. The Key Vault secret referenced by SQL_SECRET_URI, read with the
     function's managed identity (production).
"""

from __future__ import annotations

import logging
import os

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _resolve_connection_string() -> str:
    direct = os.getenv("SQL_CONNECTION_STRING")
    if direct:
        return direct

    secret_uri = os.getenv("SQL_SECRET_URI")
    if not secret_uri:
        raise RuntimeError(
            "Neither SQL_CONNECTION_STRING nor SQL_SECRET_URI is set."
        )

    # Lazy imports so local runs do not need the Azure identity packages.
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    # SQL_SECRET_URI looks like https://<vault>.vault.azure.net/secrets/<name>/<ver>
    vault_url = "/".join(secret_uri.split("/")[:3])
    secret_name = secret_uri.split("/secrets/")[1].split("/")[0]

    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    return client.get_secret(secret_name).value


def get_engine() -> Engine:
    conn = _resolve_connection_string()
    return create_engine("mssql+pyodbc:///?odbc_connect=" + conn)


def load_readings(engine: Engine) -> pd.DataFrame:
    """Load groundwater readings joined to well metadata."""
    sql = """
        SELECT  r.well_id,
                r.reading_date,
                r.water_level_mbgl,
                r.tds_mg_per_l,
                w.coastal_flag,
                w.management_area
        FROM    monitoring.water_level_readings r
        JOIN    monitoring.monitoring_wells w ON w.well_id = r.well_id
        WHERE   r.data_quality_flag <> 'suspect'
        ORDER BY r.well_id, r.reading_date
    """
    df = pd.read_sql(sql, engine, parse_dates=["reading_date"])
    return df


def load_rainfall(engine: Engine) -> pd.DataFrame:
    """Load daily rainfall by groundwater management area."""
    sql = """
        SELECT  management_area,
                obs_date,
                AVG(rainfall_mm) AS rainfall_mm
        FROM    monitoring.rainfall_observations
        WHERE   data_quality_flag <> 'suspect'
        GROUP BY management_area, obs_date
        ORDER BY management_area, obs_date
    """
    return pd.read_sql(sql, engine, parse_dates=["obs_date"])


def write_events(engine: Engine, events: pd.DataFrame) -> int:
    """Insert newly-detected anomaly events, skipping duplicates.

    A duplicate is the same (well_id, event_date, anomaly_type). This keeps the
    function idempotent so it can run daily without re-flagging old events.
    """
    if events.empty:
        return 0

    inserted = 0
    with engine.begin() as conn:
        for _, e in events.iterrows():
            exists = conn.execute(
                text(
                    """
                    SELECT 1 FROM monitoring.anomaly_events
                    WHERE well_id = :well_id
                      AND event_date = :event_date
                      AND anomaly_type = :anomaly_type
                    """
                ),
                {
                    "well_id": int(e["well_id"]),
                    "event_date": e["event_date"],
                    "anomaly_type": e["anomaly_type"],
                },
            ).first()
            if exists:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO monitoring.anomaly_events
                        (well_id, event_date, anomaly_type, severity,
                         threshold_value, actual_value, detail)
                    VALUES
                        (:well_id, :event_date, :anomaly_type, :severity,
                         :threshold_value, :actual_value, :detail)
                    """
                ),
                {
                    "well_id": int(e["well_id"]),
                    "event_date": e["event_date"],
                    "anomaly_type": e["anomaly_type"],
                    "severity": e["severity"],
                    "threshold_value": _none_if_nan(e.get("threshold_value")),
                    "actual_value": _none_if_nan(e.get("actual_value")),
                    "detail": e.get("detail"),
                },
            )
            inserted += 1
    logger.info("Inserted %d new anomaly events", inserted)
    return inserted


def _none_if_nan(value):
    if value is None:
        return None
    try:
        return None if pd.isna(value) else float(value)
    except (TypeError, ValueError):
        return None
