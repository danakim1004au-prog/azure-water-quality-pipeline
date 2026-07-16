"""Shared data loading and feature engineering for the ML modules.

Both the forecasting model (forecast_train.py) and the unsupervised anomaly
detector (anomaly_unsupervised.py) build on the same curated sample tables, so
the loading and feature logic lives here once.

A deliberate design point for time-series ML is *no look-ahead leakage*: every
feature available at time t is computed only from information known at or before
t, and the forecasting target is a future value. The split helper below splits
strictly in time (never randomly), with a purge gap so a training target can
never fall inside the test window.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
SAMPLE = os.path.join(HERE, "..", "sample_data")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load wells, daily readings and management-area rainfall."""
    wells = pd.read_csv(os.path.join(SAMPLE, "monitoring_wells.csv"))
    readings = pd.read_csv(
        os.path.join(SAMPLE, "water_level_readings.csv"),
        parse_dates=["reading_date"],
    )
    rainfall = pd.read_csv(
        os.path.join(SAMPLE, "rainfall_observations.csv"),
        parse_dates=["obs_date"],
    )

    # Keep only trustworthy readings: a suspect sensor value must never become
    # a training signal (the same integrity rule the detectors apply).
    if "data_quality_flag" in readings.columns:
        readings = readings[readings["data_quality_flag"] != "suspect"]

    # Keep rainfall local to each groundwater management area. Older generated
    # frames without the mapping remain supported as a single regional signal.
    rain_groups = ["obs_date"]
    if "management_area" in rainfall.columns:
        rain_groups.insert(0, "management_area")
    rainfall = (
        rainfall.groupby(rain_groups, as_index=False)["rainfall_mm"]
        .mean()
        .rename(columns={"obs_date": "reading_date", "rainfall_mm": "rainfall_mm"})
    )

    return wells, readings, rainfall


# ---------------------------------------------------------------------------
# Forecasting features (Option A)
# ---------------------------------------------------------------------------
# Lag/rolling windows (days). All are backward-looking from the feature date.
LAG_DAYS = [1, 7, 14, 30]
ROLL_DAYS = [7, 30]
RAIN_WINDOWS = [7, 14, 30]


def build_forecast_features(
    wells: pd.DataFrame,
    readings: pd.DataFrame,
    rainfall: pd.DataFrame,
    horizon: int,
) -> pd.DataFrame:
    """Build a leakage-safe feature table for forecasting water level.

    Target: the water level `horizon` days into the future for each bore.
    Features known at time t only: lagged/rolling water levels, trailing
    rainfall totals, salinity, and a smooth day-of-year seasonality encoding.
    """
    df = readings.merge(
        wells[["well_id", "coastal_flag", "management_area"]],
        on="well_id",
        how="left",
    )
    rain_keys = ["reading_date"]
    if "management_area" in rainfall.columns:
        rain_keys.append("management_area")
    df = df.merge(rainfall, on=rain_keys, how="left")
    df["rainfall_mm"] = df["rainfall_mm"].fillna(0.0)
    df = df.sort_values(["well_id", "reading_date"]).reset_index(drop=True)

    grp = df.groupby("well_id", group_keys=False)

    # Backward-looking water-level history.
    for lag in LAG_DAYS:
        df[f"level_lag_{lag}"] = grp["water_level_mbgl"].shift(lag)
    for win in ROLL_DAYS:
        df[f"level_roll_mean_{win}"] = grp["water_level_mbgl"].transform(
            lambda s, w=win: s.rolling(w, min_periods=max(2, w // 2)).mean()
        )
        df[f"level_roll_std_{win}"] = grp["water_level_mbgl"].transform(
            lambda s, w=win: s.rolling(w, min_periods=max(2, w // 2)).std()
        )

    # Trailing rainfall totals (recharge driver).
    for win in RAIN_WINDOWS:
        df[f"rain_sum_{win}"] = grp["rainfall_mm"].transform(
            lambda s, w=win: s.rolling(w, min_periods=1).sum()
        )

    # Salinity context (current value is known at t).
    df["tds_mg_per_l"] = grp["tds_mg_per_l"].transform(
        lambda s: s.ffill()
    )

    # Smooth seasonality: winter-dominant recharge in a Mediterranean climate.
    doy = df["reading_date"].dt.dayofyear
    df["season_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["season_cos"] = np.cos(2 * np.pi * doy / 365.25)

    # One-hot the management area (small, fixed cardinality).
    df = pd.get_dummies(df, columns=["management_area"], prefix="area")

    # The current level is a legitimate feature (known at t); it also gives us
    # the persistence baseline later.
    df["level_now"] = df["water_level_mbgl"]

    # Target and its calendar date (used for the purged time split).
    df["target"] = grp["water_level_mbgl"].shift(-horizon)
    df["target_date"] = df["reading_date"] + pd.to_timedelta(horizon, unit="D")

    return df.dropna(subset=["target", f"level_lag_{max(LAG_DAYS)}"]).reset_index(
        drop=True
    )


def feature_columns(df: pd.DataFrame) -> list[str]:
    """Select the model input columns (everything engineered, no identifiers)."""
    drop = {
        "well_id",
        "reading_date",
        "target",
        "target_date",
        "water_level_mbgl",
        "data_quality_flag",
        "pumping_event",
    }
    return [c for c in df.columns if c not in drop]


def purged_time_split(
    df: pd.DataFrame, horizon: int, test_fraction: float = 0.2
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split strictly in time, purging the gap so no train target leaks.

    Train rows have a target date *before* the cutoff; test rows have a feature
    date *at or after* the cutoff. The horizon-wide gap between the two windows
    means a training label can never sit inside the test period.
    """
    dates = np.sort(df["reading_date"].unique())
    cutoff = pd.Timestamp(dates[int(len(dates) * (1 - test_fraction))])

    train = df[df["target_date"] < cutoff]
    test = df[df["reading_date"] >= cutoff]
    return train.reset_index(drop=True), test.reset_index(drop=True), cutoff


# ---------------------------------------------------------------------------
# Anomaly features (Option B)
# ---------------------------------------------------------------------------
def build_anomaly_features(
    wells: pd.DataFrame,
    readings: pd.DataFrame,
    rainfall: pd.DataFrame,
) -> pd.DataFrame:
    """Build well-agnostic features for unsupervised multivariate detection.

    To compare bores with very different baselines on one scale, the level and
    salinity are expressed as rolling z-scores (deviation from each bore's own
    recent history), alongside day-over-day rates of change and trailing
    rainfall. IsolationForest then learns "normal" jointly across all features.
    """
    df = readings.merge(
        wells[["well_id", "coastal_flag", "management_area"]],
        on="well_id",
        how="left",
    )
    rain_keys = ["reading_date"]
    if "management_area" in rainfall.columns:
        rain_keys.append("management_area")
    df = df.merge(rainfall, on=rain_keys, how="left")
    df["rainfall_mm"] = df["rainfall_mm"].fillna(0.0)
    df = df.sort_values(["well_id", "reading_date"]).reset_index(drop=True)

    grp = df.groupby("well_id", group_keys=False)

    def _zscore(s: pd.Series) -> pd.Series:
        # Exclude the current point from its own baseline (shift by 1).
        prior = s.shift(1)
        mean = prior.rolling(30, min_periods=5).mean()
        std = prior.rolling(30, min_periods=5).std()
        return (s - mean) / std.replace(0, np.nan)

    df["level_z"] = grp["water_level_mbgl"].transform(_zscore)
    df["tds_z"] = grp["tds_mg_per_l"].transform(_zscore)
    df["level_diff"] = grp["water_level_mbgl"].transform(lambda s: s.diff())
    df["tds_diff"] = grp["tds_mg_per_l"].transform(lambda s: s.diff())
    df["rain_sum_7"] = grp["rainfall_mm"].transform(
        lambda s: s.rolling(7, min_periods=1).sum()
    )

    feat = ["level_z", "tds_z", "level_diff", "tds_diff", "rain_sum_7"]
    return df.dropna(subset=feat).reset_index(drop=True), feat
