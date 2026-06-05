"""Client for the public daily-rainfall (gridded climate) API.

The climate data service returns long daily series for any station given its
coordinates / station id and a contact email for attribution. We request the
daily rainfall variable only and normalise it to one row per station per day.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from .config import IngestionWindow, Settings
from .http_utils import get_with_retry

logger = logging.getLogger(__name__)

RAINFALL_COLUMNS = [
    "station_id",
    "station_name",
    "obs_date",
    "rainfall_mm",
    "data_quality_flag",
]


def fetch_station(
    settings: Settings,
    station_id: str,
    station_name: str,
    window: IngestionWindow,
) -> pd.DataFrame:
    """Fetch a single station's daily rainfall series as a tidy frame."""
    params = {
        "station": station_id,
        "start": window.start.strftime("%Y%m%d"),
        "finish": window.end.strftime("%Y%m%d"),
        "format": "csv",
        "comment": "R",          # request the rainfall variable
        "username": settings.rainfall_api_email,
        "password": "apirequest",
    }
    resp = get_with_retry(settings.rainfall_base_url, params=params)

    raw = pd.read_csv(io.StringIO(resp.text))
    # The service uses different header casings across endpoints; normalise.
    cols = {c.lower().strip(): c for c in raw.columns}
    date_col = cols.get("date") or cols.get("date2") or list(raw.columns)[0]
    rain_col = cols.get("rain") or cols.get("daily_rain") or cols.get("rainfall")

    df = pd.DataFrame()
    df["station_id"] = str(station_id)
    df["station_name"] = station_name
    df["obs_date"] = pd.to_datetime(raw[date_col], errors="coerce").dt.date
    df["rainfall_mm"] = pd.to_numeric(raw[rain_col], errors="coerce")
    # Negative rainfall is impossible: flag as suspect rather than dropping so
    # the gap is visible in reporting.
    df["data_quality_flag"] = "measured"
    df.loc[df["rainfall_mm"] < 0, "data_quality_flag"] = "suspect"

    return df[RAINFALL_COLUMNS].dropna(subset=["obs_date"])


def collect_all(
    settings: Settings, window: IngestionWindow, stations: list[tuple[str, str]]
) -> pd.DataFrame:
    """Collect rainfall for every configured station."""
    frames: list[pd.DataFrame] = []
    for station_id, station_name in stations:
        try:
            frame = fetch_station(settings, station_id, station_name, window)
            logger.info("Fetched %d rainfall rows for %s", len(frame), station_name)
            frames.append(frame)
        except Exception:  # noqa: BLE001 - one bad station shouldn't fail the run
            logger.exception("Failed to fetch rainfall for station %s", station_id)

    return (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=RAINFALL_COLUMNS)
    )
