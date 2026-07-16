"""Option A — short-term groundwater-level forecasting (supervised ML).

Trains a gradient-boosted regression model to predict each bore's water level a
fixed horizon ahead, so operators can anticipate supply constraints rather than
only react to them. This is genuine machine learning: the model *learns* its
parameters from historical data and is judged on data it has never seen.

Key correctness choices for time-series ML:
  * Features use only information available at the prediction time (no leakage).
  * The train/test split is strictly chronological with a purge gap, never a
    random shuffle, so reported error reflects true forward-looking skill.
  * The learned model is benchmarked against a naive persistence baseline
    ("tomorrow looks like today"); beating it is what proves the model adds value.

Outputs (written to ml/artifacts/):
  * forecast_model.joblib  — the trained pipeline
  * forecast_metrics.json  — MAE / RMSE for the model and the baseline
  * forecast_predictions.csv — test-set actual vs predicted (for Power BI)
  * forecast_example.png   — actual vs predicted for one bore

Run:  python ml/forecast_train.py
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

import _data

# Forecast a month ahead: far enough to be operationally useful for supply
# planning, and far enough that naive persistence ("next month looks like
# today") starts to break down and the learned seasonal/recharge signal pays
# off. The horizon sweep below documents exactly where that crossover happens.
HORIZON_DAYS = 30
SWEEP_HORIZONS = [7, 14, 30, 45, 60]
ARTIFACTS = os.path.join(os.path.dirname(__file__), "artifacts")


def _rmse(y_true, y_pred) -> float:
    return float(np.sqrt(mean_squared_error(y_true, y_pred)))


def main() -> int:
    os.makedirs(ARTIFACTS, exist_ok=True)

    wells, readings, rainfall = _data.load_tables()
    frame = _data.build_forecast_features(wells, readings, rainfall, HORIZON_DAYS)
    train, test, cutoff = _data.purged_time_split(frame, HORIZON_DAYS)

    features = _data.feature_columns(frame)
    x_train, y_train = train[features], train["target"]
    x_test, y_test = test[features], test["target"]

    print(
        f"Horizon: {HORIZON_DAYS} days | "
        f"train rows: {len(train)} | test rows: {len(test)} | "
        f"time cutoff: {cutoff.date()}"
    )

    # Gradient-boosted trees: strong on small tabular data, and exposes feature
    # importances so the forecast stays explainable.
    model = GradientBoostingRegressor(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=3,
        subsample=0.8,
        random_state=0,
    )
    model.fit(x_train, y_train)
    pred = model.predict(x_test)

    # Naive baseline: predict the future level equals the current level.
    baseline = test["level_now"].to_numpy()

    model_mae = float(mean_absolute_error(y_test, pred))
    baseline_mae = float(mean_absolute_error(y_test, baseline))
    metrics = {
        "horizon_days": HORIZON_DAYS,
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "model_mae_m": round(model_mae, 4),
        "model_rmse_m": round(_rmse(y_test, pred), 4),
        "baseline_mae_m": round(baseline_mae, 4),
        "baseline_rmse_m": round(_rmse(y_test, baseline), 4),
    }
    metrics["mae_improvement_pct"] = round(
        100 * (baseline_mae - model_mae) / baseline_mae,
        1,
    )

    # Persist artefacts.
    import joblib

    joblib.dump({"model": model, "features": features}, os.path.join(ARTIFACTS, "forecast_model.joblib"))
    with open(os.path.join(ARTIFACTS, "forecast_metrics.json"), "w") as fh:
        json.dump(metrics, fh, indent=2)

    out = test[["well_id", "reading_date", "target_date", "level_now"]].copy()
    out["actual"] = y_test.to_numpy()
    out["predicted"] = pred
    out.to_csv(os.path.join(ARTIFACTS, "forecast_predictions.csv"), index=False)

    _plot_example(out)
    _report(metrics, model, features)
    _horizon_sweep(wells, readings, rainfall)
    return 0


def _fit_eval(frame: pd.DataFrame, horizon: int) -> tuple[float, float]:
    """Fit at a given horizon and return (model MAE, baseline MAE) on held-out future."""
    train, test, _ = _data.purged_time_split(frame, horizon)
    features = _data.feature_columns(frame)
    model = GradientBoostingRegressor(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=3,
        subsample=0.8,
        random_state=0,
    )
    model.fit(train[features], train["target"])
    pred = model.predict(test[features])
    model_mae = float(mean_absolute_error(test["target"], pred))
    baseline_mae = float(mean_absolute_error(test["target"], test["level_now"]))
    return model_mae, baseline_mae


def _horizon_sweep(wells, readings, rainfall) -> None:
    """Quantify how the learned model's edge over persistence grows with horizon."""
    rows = []
    for horizon in SWEEP_HORIZONS:
        frame = _data.build_forecast_features(wells, readings, rainfall, horizon)
        model_mae, baseline_mae = _fit_eval(frame, horizon)
        rows.append(
            {
                "horizon_days": horizon,
                "model_mae_m": round(model_mae, 4),
                "baseline_mae_m": round(baseline_mae, 4),
                "improvement_pct": round(
                    100 * (baseline_mae - model_mae) / baseline_mae, 1
                ),
            }
        )

    sweep = pd.DataFrame(rows)
    sweep.to_csv(os.path.join(ARTIFACTS, "forecast_horizon_sweep.csv"), index=False)
    print("\n=== Horizon sweep (model vs persistence) ===")
    print(sweep.to_string(index=False))


def _plot_example(out: pd.DataFrame) -> None:
    """Save an actual-vs-predicted chart for the first bore in the test set."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    well = out["well_id"].iloc[0]
    sub = out[out["well_id"] == well].sort_values("target_date")

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(sub["target_date"], sub["actual"], label="Actual", linewidth=1.6)
    ax.plot(
        sub["target_date"],
        sub["predicted"],
        label="Predicted",
        linewidth=1.6,
        linestyle="--",
    )
    ax.invert_yaxis()  # deeper water table reads lower
    ax.set_title(f"{HORIZON_DAYS}-day water-level forecast — well {well} (test set)")
    ax.set_ylabel("Water level (mBGL)")
    ax.set_xlabel("Date")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(ARTIFACTS, "forecast_example.png"), dpi=130)
    plt.close(fig)


def _report(metrics: dict, model: GradientBoostingRegressor, features: list[str]) -> None:
    print("\n=== Forecast performance (held-out future) ===")
    print(
        f"Model    MAE {metrics['model_mae_m']:.3f} m | "
        f"RMSE {metrics['model_rmse_m']:.3f} m"
    )
    print(
        f"Baseline MAE {metrics['baseline_mae_m']:.3f} m | "
        f"RMSE {metrics['baseline_rmse_m']:.3f} m  (persistence)"
    )
    print(f"MAE improvement over baseline: {metrics['mae_improvement_pct']:.1f}%")

    importances = sorted(
        zip(features, model.feature_importances_), key=lambda t: t[1], reverse=True
    )
    print("\nTop features:")
    for name, imp in importances[:6]:
        print(f"  {name:<22} {imp:.3f}")
    print(f"\nArtefacts written to {os.path.normpath(ARTIFACTS)}")


if __name__ == "__main__":
    raise SystemExit(main())
