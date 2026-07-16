"""Build a bore-level water-security risk and licence-compliance snapshot.

The snapshot combines groundwater condition, a 60-day trend projection,
licence allocation use, recent anomaly events and data completeness. It is a
decision-support demonstration, not a regulatory determination.

Run from the repository root:

    python analytics/water_security_risk.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "sample_data"
OUTPUT_PATH = SAMPLE_DIR / "water_security_risk.csv"

FORECAST_HORIZON_DAYS = 60
FORECAST_WINDOW_DAYS = 90
INTERVAL_Z_80 = 1.281552


def load_inputs(sample_dir: Path = SAMPLE_DIR) -> dict[str, pd.DataFrame]:
    """Load the committed demonstration tables used by the risk model."""
    return {
        "wells": pd.read_csv(sample_dir / "monitoring_wells.csv"),
        "readings": pd.read_csv(
            sample_dir / "water_level_readings.csv",
            parse_dates=["reading_date"],
        ),
        "events": pd.read_csv(
            sample_dir / "anomaly_events.csv",
            parse_dates=["event_date"],
        ),
        "licences": pd.read_csv(
            sample_dir / "water_licences.csv",
            parse_dates=["licence_start_date", "licence_end_date"],
        ),
        "extraction": pd.read_csv(
            sample_dir / "metered_extraction.csv",
            parse_dates=["reading_month"],
        ),
    }


def calculate_licence_compliance(
    licences: pd.DataFrame,
    extraction: pd.DataFrame,
    wells: pd.DataFrame,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """Calculate current and projected allocation use by management area."""
    active = licences[
        (licences["licence_start_date"] <= as_of)
        & (licences["licence_end_date"] >= as_of)
    ].copy()

    trusted = extraction.copy()
    if "data_quality_flag" in trusted.columns:
        trusted = trusted[trusted["data_quality_flag"] != "suspect"]
    trusted = trusted.merge(
        wells[["well_id", "management_area"]], on="well_id", how="left"
    )

    rows: list[dict] = []
    for _, licence in active.iterrows():
        area_rows = trusted[
            (trusted["management_area"] == licence["management_area"])
            & (trusted["reading_month"] >= licence["licence_start_date"])
            & (trusted["reading_month"] <= as_of)
        ]
        extracted = float(area_rows["extraction_ml"].sum())
        elapsed_days = int((as_of - licence["licence_start_date"]).days) + 1
        licence_days = int(
            (licence["licence_end_date"] - licence["licence_start_date"]).days
        ) + 1
        elapsed_fraction = min(max(elapsed_days / licence_days, 0.0), 1.0)
        projected = extracted / elapsed_fraction if elapsed_fraction else np.nan
        allocation = float(licence["annual_allocation_ml"])

        rows.append(
            {
                "licence_id": licence["licence_id"],
                "management_area": licence["management_area"],
                "annual_allocation_ml": allocation,
                "extraction_ytd_ml": round(extracted, 2),
                "allocation_used_pct": round(100 * extracted / allocation, 1),
                "projected_year_end_extraction_ml": round(projected, 2),
                "projected_allocation_pct": round(100 * projected / allocation, 1),
            }
        )
    return pd.DataFrame(rows)


def forecast_levels(
    readings: pd.DataFrame,
    horizon_days: int = FORECAST_HORIZON_DAYS,
    window_days: int = FORECAST_WINDOW_DAYS,
) -> pd.DataFrame:
    """Project each bore with a recent linear trend and an 80% interval."""
    rows: list[dict] = []
    trusted = readings.copy()
    if "data_quality_flag" in trusted.columns:
        trusted = trusted[trusted["data_quality_flag"] != "suspect"]

    for well_id, group in trusted.groupby("well_id"):
        group = group.sort_values("reading_date")
        latest_date = group["reading_date"].max()
        recent = group[
            group["reading_date"] >= latest_date - pd.Timedelta(days=window_days - 1)
        ].dropna(subset=["water_level_mbgl"])
        if len(recent) < 10:
            continue

        x = (recent["reading_date"] - recent["reading_date"].min()).dt.days.to_numpy()
        y = recent["water_level_mbgl"].astype(float).to_numpy()
        slope, intercept = np.polyfit(x, y, 1)
        x_future = float(x[-1] + horizon_days)
        prediction = float(slope * x_future + intercept)
        fitted = slope * x + intercept
        residual_std = float(np.std(y - fitted, ddof=2)) if len(y) > 2 else 0.0
        sxx = float(np.sum((x - x.mean()) ** 2))
        leverage = 1.0 + 1.0 / len(x)
        if sxx > 0:
            leverage += (x_future - x.mean()) ** 2 / sxx
        half_width = INTERVAL_Z_80 * residual_std * np.sqrt(leverage)
        latest_level = float(y[-1])

        rows.append(
            {
                "well_id": int(well_id),
                "latest_level_mbgl": round(latest_level, 3),
                "forecast_60d_mbgl": round(prediction, 3),
                "forecast_lower_80_mbgl": round(prediction - half_width, 3),
                "forecast_upper_80_mbgl": round(prediction + half_width, 3),
                "forecast_change_mbgl": round(prediction - latest_level, 3),
            }
        )
    return pd.DataFrame(rows)


def data_completeness(readings: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Calculate trusted daily coverage over the latest 30-day reporting window."""
    start = as_of - pd.Timedelta(days=29)
    recent = readings[
        (readings["reading_date"] >= start) & (readings["reading_date"] <= as_of)
    ].copy()
    if "data_quality_flag" in recent.columns:
        recent = recent[recent["data_quality_flag"] != "suspect"]
    counts = recent.groupby("well_id")["reading_date"].nunique()
    return pd.DataFrame(
        {
            "well_id": counts.index.astype(int),
            "data_completeness_30d_pct": (counts.values / 30 * 100).clip(max=100).round(1),
        }
    )


def latest_events(events: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Return the most severe event for each bore in the latest 30 days."""
    recent = events[events["event_date"] >= as_of - pd.Timedelta(days=29)].copy()
    if recent.empty:
        return pd.DataFrame(
            columns=["well_id", "latest_anomaly_type", "latest_anomaly_severity"]
        )
    rank = {"INFO": 1, "WARNING": 2, "CRITICAL": 3}
    recent["severity_rank"] = recent["severity"].map(rank).fillna(0)
    recent = recent.sort_values(
        ["well_id", "severity_rank", "event_date"], ascending=[True, False, False]
    ).drop_duplicates("well_id")
    return recent[["well_id", "anomaly_type", "severity"]].rename(
        columns={
            "anomaly_type": "latest_anomaly_type",
            "severity": "latest_anomaly_severity",
        }
    )


def _classify(row: pd.Series) -> tuple[int, str, str, str]:
    score = 0
    drivers: list[str] = []

    if row.get("latest_anomaly_severity") == "CRITICAL":
        score += 60
        drivers.append(f"critical {row['latest_anomaly_type']} event")
    elif row.get("latest_anomaly_severity") == "WARNING":
        score += 25
        drivers.append(f"warning {row['latest_anomaly_type']} event")

    projected_use = float(row.get("projected_allocation_pct", 0) or 0)
    if projected_use > 100:
        score += 50
        drivers.append("projected extraction above allocation")
    elif projected_use >= 85:
        score += 25
        drivers.append("projected extraction at or above 85% of allocation")

    forecast_change = float(row.get("forecast_change_mbgl", 0) or 0)
    if forecast_change >= 0.30:
        score += 20
        drivers.append("60-day projection indicates further drawdown")
    elif forecast_change >= 0.15:
        score += 10
        drivers.append("60-day projection indicates moderate drawdown")

    completeness = float(row.get("data_completeness_30d_pct", 0) or 0)
    if completeness < 95:
        score += 20
        drivers.append("recent data completeness below 95%")

    score = min(score, 100)
    status = "Critical" if score >= 60 else "Watch" if score >= 25 else "Normal"

    anomaly_type = row.get("latest_anomaly_type")
    if anomaly_type == "SalinityIntrusionRisk":
        action = "Confirm the salinity result and review coastal bore monitoring."
    elif anomaly_type == "RapidLevelChange":
        action = "Confirm the latest level reading and review recent pumping conditions."
    elif anomaly_type == "LowRechargeResponse":
        action = "Review the recent rainfall response and check for continued low recharge."
    elif projected_use > 100:
        action = "Review extraction against the licence allocation and prepare a compliance response."
    elif projected_use >= 85:
        action = "Review the seasonal pumping plan and monitor allocation use monthly."
    elif forecast_change >= 0.30:
        action = "Review the 60-day drawdown outlook and increase monitoring frequency if required."
    elif completeness < 95:
        action = "Resolve recent data gaps before the next water-security review."
    else:
        action = "Continue routine monitoring."

    return score, status, "; ".join(drivers) or "no current risk trigger", action


def build_risk_snapshot(inputs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build the Power BI-ready bore-level risk snapshot."""
    readings = inputs["readings"]
    as_of = pd.Timestamp(readings["reading_date"].max()).normalize()
    wells = inputs["wells"].copy()

    compliance = calculate_licence_compliance(
        inputs["licences"], inputs["extraction"], wells, as_of
    )
    snapshot = wells.merge(compliance, on="management_area", how="left")
    snapshot = snapshot.merge(forecast_levels(readings), on="well_id", how="left")
    snapshot = snapshot.merge(data_completeness(readings, as_of), on="well_id", how="left")
    snapshot = snapshot.merge(latest_events(inputs["events"], as_of), on="well_id", how="left")

    classified = snapshot.apply(_classify, axis=1, result_type="expand")
    classified.columns = [
        "risk_score", "risk_status", "risk_drivers", "recommended_action"
    ]
    snapshot = pd.concat([snapshot, classified], axis=1)
    snapshot.insert(0, "risk_snapshot_date", as_of.date().isoformat())

    columns = [
        "risk_snapshot_date", "well_id", "source_dh_no", "location_name",
        "management_area", "aquifer_name", "licence_id", "annual_allocation_ml",
        "extraction_ytd_ml", "allocation_used_pct",
        "projected_year_end_extraction_ml", "projected_allocation_pct",
        "latest_level_mbgl", "forecast_60d_mbgl", "forecast_lower_80_mbgl",
        "forecast_upper_80_mbgl", "forecast_change_mbgl",
        "data_completeness_30d_pct", "latest_anomaly_type",
        "latest_anomaly_severity", "risk_score", "risk_status", "risk_drivers",
        "recommended_action",
    ]
    return snapshot[columns].sort_values(
        ["risk_score", "management_area", "well_id"], ascending=[False, True, True]
    ).reset_index(drop=True)


def main() -> int:
    snapshot = build_risk_snapshot(load_inputs())
    snapshot.to_csv(OUTPUT_PATH, index=False, lineterminator="\n")
    print(snapshot[[
        "well_id", "management_area", "risk_status", "risk_score",
        "projected_allocation_pct", "recommended_action",
    ]].to_string(index=False))
    print(f"\nWrote {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
