"""Option B — unsupervised multivariate anomaly detection (ML).

The rule-based detectors in functions/anomaly_detector encode known failure
modes with explicit, auditable thresholds — ideal for compliance. This module
complements them: an Isolation Forest learns the joint shape of "normal"
behaviour across water level, salinity, their rates of change and rainfall, and
flags points that don't fit — including combinations no single rule anticipates.

The two approaches are designed to sit side by side: transparent rules for the
faults you can name, plus learned detection for the ones you can't. As a
sanity check, this script reports how many of the deliberately-injected anomaly
scenarios the model independently rediscovers.

Outputs (written to ml/artifacts/):
  * anomaly_scores.csv      — every reading with its anomaly score and flag
  * anomaly_model.joblib    — the fitted Isolation Forest + feature list

Run:  python ml/anomaly_unsupervised.py
"""

from __future__ import annotations

import os

import pandas as pd
from sklearn.ensemble import IsolationForest

import _data

# Expected fraction of points that are anomalous. Kept small and explicit; this
# is the one knob an analyst would tune to trade sensitivity against noise.
CONTAMINATION = 0.01
ARTIFACTS = os.path.join(os.path.dirname(__file__), "artifacts")


def main() -> int:
    os.makedirs(ARTIFACTS, exist_ok=True)

    wells, readings, rainfall = _data.load_tables()
    frame, features = _data.build_anomaly_features(wells, readings, rainfall)

    model = IsolationForest(
        n_estimators=300,
        contamination=CONTAMINATION,
        random_state=0,
    )
    # decision_function: higher = more normal. We store the negated value so a
    # larger "anomaly_score" intuitively means more anomalous.
    model.fit(frame[features])
    frame["anomaly_score"] = -model.decision_function(frame[features])
    frame["is_anomaly"] = model.predict(frame[features]) == -1

    flagged = frame[frame["is_anomaly"]].sort_values(
        "anomaly_score", ascending=False
    )

    cols = [
        "well_id",
        "reading_date",
        "water_level_mbgl",
        "tds_mg_per_l",
        "level_z",
        "tds_z",
        "anomaly_score",
        "is_anomaly",
    ]
    frame[cols].to_csv(os.path.join(ARTIFACTS, "anomaly_scores.csv"), index=False)

    import joblib

    joblib.dump(
        {"model": model, "features": features},
        os.path.join(ARTIFACTS, "anomaly_model.joblib"),
    )

    _report(frame, flagged, cols)
    return 0


def _report(frame: pd.DataFrame, flagged: pd.DataFrame, cols: list[str]) -> None:
    print(f"Scored {len(frame)} readings | flagged {len(flagged)} as anomalous "
          f"(contamination={CONTAMINATION})")
    print("\nTop anomalies (learned, no thresholds):")
    print(flagged[cols].head(10).to_string(index=False))

    # Cross-check against the injected scenarios: well 1 has a planted rapid
    # level change and well 5 a planted coastal salinity trend. We expect the
    # model to surface points on those bores without being told where to look.
    rediscovered = sorted(set(flagged["well_id"]) & {1, 5})
    print(
        f"\nInjected-scenario bores rediscovered by the model: "
        f"{rediscovered or 'none'}"
    )
    print(f"Artefacts written to {os.path.normpath(ARTIFACTS)}")


if __name__ == "__main__":
    raise SystemExit(main())
