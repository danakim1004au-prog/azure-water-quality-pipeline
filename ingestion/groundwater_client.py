"""Client for the public groundwater monitoring REST API.

The API exposes drillhole metadata and time-series of water level (metres
below ground level) and salinity (TDS, mg/L). The data is published under a
Creative Commons Attribution licence, making it suitable for an open
portfolio project.

Two access paths exist:

  1. The typed `sa_gwdata` helper library (preferred when installed).
  2. Direct REST calls to the documented endpoint (used as the fallback so the
     pipeline has no hard dependency on the helper library).

Both return the same tidy `pandas.DataFrame` shape, so the rest of the
pipeline does not care which path produced the data.
"""

from __future__ import annotations

import logging

import pandas as pd

from .config import IngestionWindow, Settings
from .http_utils import get_with_retry

logger = logging.getLogger(__name__)

# Columns every well-reading frame is normalised to before loading.
READING_COLUMNS = [
    "source_dh_no",
    "reading_date",
    "water_level_mbgl",
    "tds_mg_per_l",
    "pumping_event",
    "data_quality_flag",
]

WELL_COLUMNS = [
    "source_dh_no",
    "location_name",
    "latitude",
    "longitude",
    "aquifer_name",
    "management_area",
    "coastal_flag",
]


def discover_wells(settings: Settings, management_area: str) -> pd.DataFrame:
    """Return the drillholes that belong to a management area.

    Uses the typed helper library when available; otherwise queries the REST
    endpoint directly. Coastal classification is derived from the aquifer
    name / area so salinity-intrusion logic downstream can target the wells
    where it matters.
    """
    try:
        import sa_gwdata  # type: ignore

        wells = sa_gwdata.find_wells(management_area)
        df = wells.df()
        out = pd.DataFrame(
            {
                "source_dh_no": df["dh_no"].astype(str),
                "location_name": df.get("dh_name"),
                "latitude": df.get("lat"),
                "longitude": df.get("lon"),
                "aquifer_name": df.get("aquifer"),
                "management_area": management_area,
            }
        )
    except Exception:  # noqa: BLE001 - fall back to the raw REST endpoint
        logger.info("sa_gwdata unavailable; using REST fallback for %s", management_area)
        resp = get_with_retry(
            settings.groundwater_base_url,
            params={"cmd": "getobswellnetworkbyarea", "area": management_area},
        )
        records = resp.json() if resp.content else []
        out = pd.DataFrame.from_records(records)
        out = out.rename(
            columns={
                "DHNO": "source_dh_no",
                "NAME": "location_name",
                "LAT": "latitude",
                "LON": "longitude",
                "AQUIFER": "aquifer_name",
            }
        )
        out["management_area"] = management_area
        keep = [c for c in WELL_COLUMNS if c in out.columns]
        out = out[keep]

    out["coastal_flag"] = (
        out["aquifer_name"].fillna("").str.contains("coast", case=False)
        | out["management_area"].str.contains("McLaren|Willunga", case=False)
    ).astype(int)

    # Ensure all expected columns exist even if the source omitted some.
    for col in WELL_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out[WELL_COLUMNS].drop_duplicates("source_dh_no").reset_index(drop=True)


def fetch_readings(
    settings: Settings, dh_no: str, window: IngestionWindow
) -> pd.DataFrame:
    """Fetch the water-level + salinity time-series for a single drillhole."""
    resp = get_with_retry(
        settings.groundwater_base_url,
        params={
            "cmd": "getwaterleveldetails",
            "dhno": dh_no,
            "from": window.start.isoformat(),
            "to": window.end.isoformat(),
        },
    )
    records = resp.json() if resp.content else []
    if not records:
        return pd.DataFrame(columns=READING_COLUMNS)

    raw = pd.DataFrame.from_records(records)
    df = pd.DataFrame()
    df["source_dh_no"] = str(dh_no)
    df["reading_date"] = pd.to_datetime(
        raw.get("OBS_DATE", raw.get("obs_date")), errors="coerce"
    ).dt.date
    df["water_level_mbgl"] = pd.to_numeric(
        raw.get("DTW", raw.get("water_level")), errors="coerce"
    )
    df["tds_mg_per_l"] = pd.to_numeric(
        raw.get("TDS", raw.get("tds")), errors="coerce"
    )
    # Pumping events are flagged in the source where known.
    df["pumping_event"] = (
        raw.get("PUMPING", pd.Series([0] * len(raw))).fillna(0).astype(int)
        if "PUMPING" in raw.columns
        else 0
    )
    return _flag_quality(df).dropna(subset=["reading_date"])


def _flag_quality(df: pd.DataFrame) -> pd.DataFrame:
    """Apply a simple data-quality flag.

    A reading is 'suspect' when its water level is non-physical (negative or
    implausibly deep) so the compliance-grade views can exclude it. Everything
    else is treated as 'measured'.
    """
    suspect = (df["water_level_mbgl"] < 0) | (df["water_level_mbgl"] > 300)
    df["data_quality_flag"] = "measured"
    df.loc[suspect.fillna(False), "data_quality_flag"] = "suspect"
    return df[READING_COLUMNS]


def collect_all(settings: Settings, window: IngestionWindow, areas: list[str]):
    """Collect wells + readings for every configured management area.

    Returns a tuple of (wells_df, readings_df) ready for loading.
    """
    all_wells: list[pd.DataFrame] = []
    all_readings: list[pd.DataFrame] = []

    for area in areas:
        wells = discover_wells(settings, area)
        logger.info("Discovered %d wells in %s", len(wells), area)
        all_wells.append(wells)

        for dh_no in wells["source_dh_no"].dropna().unique():
            readings = fetch_readings(settings, str(dh_no), window)
            if not readings.empty:
                all_readings.append(readings)

    wells_df = (
        pd.concat(all_wells, ignore_index=True)
        if all_wells
        else pd.DataFrame(columns=WELL_COLUMNS)
    )
    readings_df = (
        pd.concat(all_readings, ignore_index=True)
        if all_readings
        else pd.DataFrame(columns=READING_COLUMNS)
    )
    logger.info(
        "Groundwater collection complete: %d wells, %d readings",
        len(wells_df),
        len(readings_df),
    )
    return wells_df, readings_df
