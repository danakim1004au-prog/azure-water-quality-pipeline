"""Tests for the regional water-security risk and compliance snapshot."""

from __future__ import annotations

import pandas as pd

from analytics.water_security_risk import (
    build_risk_snapshot,
    calculate_licence_compliance,
    load_inputs,
)


def test_committed_dataset_has_fixed_reference_date():
    inputs = load_inputs()
    assert inputs["readings"]["reading_date"].max() == pd.Timestamp("2026-06-07")


def test_rainfall_is_mapped_to_each_management_area():
    rainfall = pd.read_csv("sample_data/rainfall_observations.csv")
    mapping = rainfall[["station_id", "management_area"]].drop_duplicates()
    assert len(mapping) == 3
    assert mapping["station_id"].nunique() == 3
    assert mapping["management_area"].nunique() == 3


def test_licence_projection_identifies_known_scenarios():
    inputs = load_inputs()
    as_of = inputs["readings"]["reading_date"].max()
    compliance = calculate_licence_compliance(
        inputs["licences"], inputs["extraction"], inputs["wells"], as_of
    ).set_index("management_area")

    assert compliance.loc[
        "Northern Adelaide Plains", "projected_allocation_pct"
    ] > 100
    assert 85 <= compliance.loc["McLaren Vale", "projected_allocation_pct"] < 100
    assert compliance.loc["Barossa", "projected_allocation_pct"] < 85


def test_risk_snapshot_contains_status_reasons_and_actions():
    snapshot = build_risk_snapshot(load_inputs())
    required = {
        "forecast_lower_80_mbgl",
        "forecast_upper_80_mbgl",
        "projected_allocation_pct",
        "data_completeness_30d_pct",
        "risk_status",
        "risk_drivers",
        "recommended_action",
    }
    assert required.issubset(snapshot.columns)
    assert set(snapshot["risk_status"]) == {"Normal", "Watch", "Critical"}
    assert snapshot["recommended_action"].str.endswith(".").all()
    assert snapshot["risk_drivers"].str.len().gt(0).all()
