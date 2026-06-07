"""Unit tests for the ML modules (forecasting + unsupervised anomaly detection).

These run on the committed sample data with fixed random seeds, so they are
deterministic and need no network, database or cloud dependency — the same way
they would run in CI. They guard the two properties that actually matter:

  * the forecasting features and split are free of look-ahead leakage, and the
    learned model genuinely beats a naive persistence baseline at range; and
  * the unsupervised detector independently rediscovers the deliberately
    injected anomaly scenarios.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, IsolationForest
from sklearn.metrics import mean_absolute_error

# Import the ml package's data helper directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml"))

import _data  # noqa: E402


def _load():
    return _data.load_tables()


def test_forecast_target_is_true_future_value():
    """The target must equal the actual water level `horizon` days later."""
    wells, readings, rainfall = _load()
    horizon = 30
    frame = _data.build_forecast_features(wells, readings, rainfall, horizon)

    # target_date is exactly horizon days after the feature date.
    assert (frame["target_date"] - frame["reading_date"]).dt.days.eq(horizon).all()

    # Spot-check one bore: the target equals the future reading in the raw data.
    raw = readings[readings["well_id"] == 1].set_index("reading_date")[
        "water_level_mbgl"
    ]
    row = frame[frame["well_id"] == 1].iloc[0]
    expected = raw.loc[row["target_date"]]
    assert abs(row["target"] - expected) < 1e-9


def test_purged_split_has_no_time_overlap():
    """No training target may fall at/after the cutoff that opens the test set."""
    wells, readings, rainfall = _load()
    horizon = 30
    frame = _data.build_forecast_features(wells, readings, rainfall, horizon)
    train, test, cutoff = _data.purged_time_split(frame, horizon)

    assert train["target_date"].max() < cutoff
    assert test["reading_date"].min() >= cutoff
    assert len(train) > 0 and len(test) > 0


def test_no_feature_is_the_raw_future_target():
    """Guard against accidentally leaking the target into the feature matrix."""
    wells, readings, rainfall = _load()
    frame = _data.build_forecast_features(wells, readings, rainfall, 30)
    features = _data.feature_columns(frame)
    assert "target" not in features
    assert "target_date" not in features
    assert "water_level_mbgl" not in features  # raw current value is excluded


def test_model_beats_persistence_at_long_horizon():
    """At a 60-day horizon the learned model must beat naive persistence."""
    wells, readings, rainfall = _load()
    horizon = 60
    frame = _data.build_forecast_features(wells, readings, rainfall, horizon)
    train, test, _ = _data.purged_time_split(frame, horizon)
    features = _data.feature_columns(frame)

    model = GradientBoostingRegressor(
        n_estimators=200, learning_rate=0.05, max_depth=3, random_state=0
    )
    model.fit(train[features], train["target"])
    pred = model.predict(test[features])

    model_mae = mean_absolute_error(test["target"], pred)
    baseline_mae = mean_absolute_error(test["target"], test["level_now"])
    assert model_mae < baseline_mae


def test_anomaly_features_are_clean():
    """The anomaly feature matrix must be finite and non-empty."""
    wells, readings, rainfall = _load()
    frame, feats = _data.build_anomaly_features(wells, readings, rainfall)
    assert len(frame) > 0
    assert np.isfinite(frame[feats].to_numpy()).all()


def test_isolation_forest_rediscovers_injected_anomalies():
    """The unsupervised model should flag the planted scenarios on wells 1 and 5."""
    wells, readings, rainfall = _load()
    frame, feats = _data.build_anomaly_features(wells, readings, rainfall)

    model = IsolationForest(n_estimators=300, contamination=0.01, random_state=0)
    flagged_mask = model.fit_predict(frame[feats]) == -1
    flagged_wells = set(frame.loc[flagged_mask, "well_id"])

    assert {1, 5}.issubset(flagged_wells)


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
