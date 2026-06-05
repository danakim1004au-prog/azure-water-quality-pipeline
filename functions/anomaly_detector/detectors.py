"""Domain-specific anomaly detectors for groundwater monitoring.

Three detectors, each encoding a piece of hydrogeological domain knowledge.
They are deliberately statistical and transparent (no opaque model) so every
flag can be explained to a decision-maker and tied back to a threshold with a
documented rationale. See docs/anomaly_methodology.md for the full write-up.

Each detector takes tidy pandas frames and returns a frame of events with the
columns expected by monitoring.anomaly_events:

    well_id, event_date, anomaly_type, severity,
    threshold_value, actual_value, detail
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EVENT_COLUMNS = [
    "well_id",
    "event_date",
    "anomaly_type",
    "severity",
    "threshold_value",
    "actual_value",
    "detail",
]

# --- Tunable parameters (documented in the methodology) ---------------------
ROLLING_WINDOW_DAYS = 7        # short-term baseline for level changes
WARNING_SIGMA = 2.0            # ±2σ deviation -> WARNING
CRITICAL_SIGMA = 3.0           # ±3σ deviation -> CRITICAL
MIN_OBS_FOR_STATS = 10         # need enough history before flagging

RAIN_EVENT_WINDOW_DAYS = 7     # window to accumulate a rainfall "event"
RAIN_EVENT_THRESHOLD_MM = 20.0 # 7-day total above this counts as an event
RECHARGE_RESPONSE_DAYS = 14    # expect a level response within this window
RECHARGE_MIN_RISE_M = 0.10     # a "response" is at least this much shallower

TDS_WINDOW_DAYS = 30           # sliding window for salinity trend
TDS_TREND_MIN_SLOPE = 1.0      # mg/L per day sustained rise to flag
TDS_MIN_R2 = 0.5               # trend must be reasonably linear to count


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(columns=EVENT_COLUMNS)


# ---------------------------------------------------------------------------
# Detector 1: rapid water-level change vs a rolling baseline
# ---------------------------------------------------------------------------
def detect_rapid_level_change(readings: pd.DataFrame) -> pd.DataFrame:
    """Flag readings that deviate sharply from a 7-day rolling baseline.

    A current level more than WARNING_SIGMA (or CRITICAL_SIGMA) standard
    deviations from the trailing 7-day mean indicates an abrupt change:
    over-extraction, an adjacent development's drawdown, or a stalled recharge
    during drought. We evaluate only the most recent reading per well so the
    daily run flags *new* conditions rather than re-deriving history.
    """
    events: list[dict] = []

    for well_id, grp in readings.groupby("well_id"):
        grp = grp.sort_values("reading_date")
        level = grp["water_level_mbgl"].astype(float)
        if level.notna().sum() < MIN_OBS_FOR_STATS:
            continue

        # Baseline excludes the current reading (shift by 1) so a single sharp
        # move is measured against its history, not blended into it.
        prior = level.shift(1)
        roll_mean = prior.rolling(ROLLING_WINDOW_DAYS, min_periods=3).mean()
        roll_std = prior.rolling(ROLLING_WINDOW_DAYS, min_periods=3).std()

        last = grp.iloc[-1]
        mean_v = roll_mean.iloc[-1]
        std_v = roll_std.iloc[-1]
        actual = level.iloc[-1]

        if pd.isna(mean_v) or pd.isna(std_v) or std_v == 0 or pd.isna(actual):
            continue

        z = abs(actual - mean_v) / std_v
        if z < WARNING_SIGMA:
            continue

        severity = "CRITICAL" if z >= CRITICAL_SIGMA else "WARNING"
        direction = "deeper" if actual > mean_v else "shallower"
        events.append(
            {
                "well_id": int(well_id),
                "event_date": last["reading_date"].date(),
                "anomaly_type": "RapidLevelChange",
                "severity": severity,
                "threshold_value": round(mean_v + np.sign(actual - mean_v)
                                         * WARNING_SIGMA * std_v, 3),
                "actual_value": round(float(actual), 3),
                "detail": (
                    f"Water level {actual:.2f} mBGL is {z:.1f}σ {direction} "
                    f"than the 7-day baseline ({mean_v:.2f} mBGL)."
                ),
            }
        )

    return pd.DataFrame(events, columns=EVENT_COLUMNS) if events else _empty_events()


# ---------------------------------------------------------------------------
# Detector 2: low recharge response after a rainfall event
# ---------------------------------------------------------------------------
def detect_low_recharge_response(
    readings: pd.DataFrame, rainfall: pd.DataFrame
) -> pd.DataFrame:
    """Flag wells that fail to recharge after a meaningful rainfall event.

    Logic:
      1. Find the most recent rainfall event (7-day rolling total >
         RAIN_EVENT_THRESHOLD_MM) that is old enough to have produced a
         response (i.e. ended at least RECHARGE_RESPONSE_DAYS ago is not
         required; we look for a response *within* the window after it).
      2. For each well, compare the water level just before the event to the
         shallowest level in the RECHARGE_RESPONSE_DAYS that follow.
      3. If the table did not rise by at least RECHARGE_MIN_RISE_M, raise a
         "LowRechargeResponse" WARNING — a leading indicator of declining
         aquifer health that matters more than any single reading.
    """
    if rainfall.empty:
        return _empty_events()

    rain = rainfall.sort_values("obs_date").copy()
    rain["roll_7d"] = (
        rain["rainfall_mm"].rolling(RAIN_EVENT_WINDOW_DAYS, min_periods=1).sum()
    )
    rain_events = rain[rain["roll_7d"] > RAIN_EVENT_THRESHOLD_MM]
    if rain_events.empty:
        return _empty_events()

    # Use the most recent qualifying rainfall event.
    event_date = rain_events["obs_date"].max()
    window_end = event_date + pd.Timedelta(days=RECHARGE_RESPONSE_DAYS)

    events: list[dict] = []
    for well_id, grp in readings.groupby("well_id"):
        grp = grp.sort_values("reading_date")

        before = grp[grp["reading_date"] <= event_date]
        after = grp[
            (grp["reading_date"] > event_date)
            & (grp["reading_date"] <= window_end)
        ]
        if before.empty or after.empty:
            continue

        level_before = before["water_level_mbgl"].iloc[-1]
        # Recharge makes the water table shallower => smaller mBGL value.
        shallowest_after = after["water_level_mbgl"].min()
        rise = float(level_before - shallowest_after)

        if rise >= RECHARGE_MIN_RISE_M:
            continue  # healthy recharge response, no flag

        events.append(
            {
                "well_id": int(well_id),
                "event_date": event_date.date(),
                "anomaly_type": "LowRechargeResponse",
                "severity": "WARNING",
                "threshold_value": RECHARGE_MIN_RISE_M,
                "actual_value": round(rise, 3),
                "detail": (
                    f"After a rainfall event (7-day total > "
                    f"{RAIN_EVENT_THRESHOLD_MM:.0f} mm) the water table rose "
                    f"only {rise:.2f} m within {RECHARGE_RESPONSE_DAYS} days "
                    f"(expected >= {RECHARGE_MIN_RISE_M:.2f} m)."
                ),
            }
        )

    return pd.DataFrame(events, columns=EVENT_COLUMNS) if events else _empty_events()


# ---------------------------------------------------------------------------
# Detector 3: sustained salinity (TDS) increase — intrusion risk
# ---------------------------------------------------------------------------
def detect_salinity_intrusion(readings: pd.DataFrame) -> pd.DataFrame:
    """Flag a sustained upward TDS trend over a 30-day sliding window.

    A persistent linear rise in total dissolved solids in a coastal aquifer is
    an early signature of saline intrusion. We fit a least-squares line to the
    last TDS_WINDOW_DAYS of readings; if the slope exceeds TDS_TREND_MIN_SLOPE
    and the fit is sufficiently linear (R² >= TDS_MIN_R2) we raise a
    "SalinityIntrusionRisk" flag. Coastal wells escalate to CRITICAL.
    """
    events: list[dict] = []

    for well_id, grp in readings.groupby("well_id"):
        grp = grp.sort_values("reading_date")
        if grp["tds_mg_per_l"].notna().sum() < MIN_OBS_FOR_STATS:
            continue

        cutoff = grp["reading_date"].max() - pd.Timedelta(days=TDS_WINDOW_DAYS)
        window = grp[grp["reading_date"] >= cutoff].dropna(subset=["tds_mg_per_l"])
        if len(window) < 3:
            continue

        # x = days since window start, y = TDS
        x = (window["reading_date"] - window["reading_date"].min()).dt.days.to_numpy()
        y = window["tds_mg_per_l"].astype(float).to_numpy()
        if x.max() == 0:
            continue

        slope, intercept = np.polyfit(x, y, 1)
        fitted = slope * x + intercept
        ss_res = float(np.sum((y - fitted) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        if slope < TDS_TREND_MIN_SLOPE or r2 < TDS_MIN_R2:
            continue

        coastal = bool(grp["coastal_flag"].iloc[-1])
        severity = "CRITICAL" if coastal else "WARNING"
        events.append(
            {
                "well_id": int(well_id),
                "event_date": window["reading_date"].max().date(),
                "anomaly_type": "SalinityIntrusionRisk",
                "severity": severity,
                "threshold_value": TDS_TREND_MIN_SLOPE,
                "actual_value": round(float(slope), 3),
                "detail": (
                    f"TDS rising {slope:.1f} mg/L per day over "
                    f"{TDS_WINDOW_DAYS} days (R²={r2:.2f})"
                    + (" in a coastal aquifer." if coastal else ".")
                ),
            }
        )

    return pd.DataFrame(events, columns=EVENT_COLUMNS) if events else _empty_events()


def run_all(readings: pd.DataFrame, rainfall: pd.DataFrame) -> pd.DataFrame:
    """Run every detector and concatenate their events."""
    if readings.empty:
        return _empty_events()

    frames = [
        detect_rapid_level_change(readings),
        detect_low_recharge_response(readings, rainfall),
        detect_salinity_intrusion(readings),
    ]
    combined = pd.concat(frames, ignore_index=True)
    return combined
