"""Client for the near-real-time surface-water data portal.

This is the multi-source enrichment layer: river / reservoir levels that give
the pipeline a third, independently-published source. It makes the ETL story
more realistic (joining heterogeneous schemas and cadences) without being
load-bearing for the core anomaly logic.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from .config import IngestionWindow, Settings
from .http_utils import get_with_retry

logger = logging.getLogger(__name__)

SURFACE_COLUMNS = [
    "site_id",
    "site_name",
    "reading_datetime",
    "metric_name",
    "metric_value",
    "data_quality_flag",
]


def fetch_site(
    settings: Settings,
    site_id: str,
    site_name: str,
    metric_name: str,
    window: IngestionWindow,
) -> pd.DataFrame:
    """Fetch one metric (e.g. water level) for one surface-water site."""
    params = {
        "DataSet": metric_name,
        "Location": site_id,
        "DateRange": "Custom",
        "StartTime": window.start.isoformat(),
        "EndTime": window.end.isoformat(),
        "Format": "csv",
    }
    resp = get_with_retry(settings.surface_water_base_url, params=params)

    raw = pd.read_csv(io.StringIO(resp.text))
    cols = {c.lower().strip(): c for c in raw.columns}
    ts_col = cols.get("timestamp") or cols.get("date") or list(raw.columns)[0]
    val_col = cols.get("value") or list(raw.columns)[1]

    df = pd.DataFrame()
    df["site_id"] = str(site_id)
    df["site_name"] = site_name
    df["reading_datetime"] = pd.to_datetime(raw[ts_col], errors="coerce")
    df["metric_name"] = metric_name
    df["metric_value"] = pd.to_numeric(raw[val_col], errors="coerce")
    df["data_quality_flag"] = "measured"

    return df[SURFACE_COLUMNS].dropna(subset=["reading_datetime"])


def collect_all(
    settings: Settings,
    window: IngestionWindow,
    sites: list[tuple[str, str, str]],
) -> pd.DataFrame:
    """Collect each (site_id, site_name, metric_name) tuple."""
    frames: list[pd.DataFrame] = []
    for site_id, site_name, metric in sites:
        try:
            frame = fetch_site(settings, site_id, site_name, metric, window)
            logger.info("Fetched %d surface rows for %s", len(frame), site_name)
            frames.append(frame)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to fetch surface water for site %s", site_id)

    return (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=SURFACE_COLUMNS)
    )
