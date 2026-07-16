# Machine learning

Two learned models sit alongside the transparent rule-based detectors in
[`functions/anomaly_detector`](../functions/anomaly_detector). The rules answer
*"has a named failure mode occurred?"*; these models answer *"what is coming?"*
and *"what doesn't fit, even if no rule describes it?"*.

| Script | Type | Question it answers |
|--------|------|---------------------|
| [`forecast_train.py`](forecast_train.py) | Supervised regression (gradient-boosted trees) | What will each bore's water level be ~30 days from now? |
| [`anomaly_unsupervised.py`](anomaly_unsupervised.py) | Unsupervised (Isolation Forest) | Which readings are jointly anomalous across level, salinity and rainfall? |

Shared loading and leakage-safe feature engineering live in
[`_data.py`](_data.py).

## A — Groundwater-level forecasting

A gradient-boosted regressor predicts each bore's water level a fixed horizon
ahead from lagged/rolling levels, trailing rainfall totals, salinity and a
smooth day-of-year seasonality encoding.

Two correctness choices make the reported skill trustworthy:

- **No look-ahead leakage.** Every feature uses only information available at
  the prediction time; the target is a future value.
- **Purged chronological split.** Train and test are split strictly in time
  (never shuffled), with a horizon-wide purge gap so a training label can never
  land inside the test window. Skill is then benchmarked against a naive
  **persistence** baseline ("next month looks like today") — beating it is what
  proves the model adds value.

The horizon sweep ([`artifacts/forecast_horizon_sweep.csv`](artifacts/forecast_horizon_sweep.csv))
makes the trade-off explicit: at short range the series is so autocorrelated
that persistence is hard to beat, but the model's edge grows steadily with the
horizon as it learns the seasonal recharge cycle persistence cannot see.

| Horizon | Model MAE | Persistence MAE | Improvement |
|--------:|----------:|----------------:|------------:|
| 7 days  | 0.138 m   | 0.124 m         | −11.5 %     |
| 14 days | 0.167 m   | 0.151 m         | −10.6 %     |
| 30 days | 0.203 m   | 0.208 m         | **+2.2 %**  |
| 45 days | 0.217 m   | 0.250 m         | **+13.3 %** |
| 60 days | 0.234 m   | 0.304 m         | **+22.8 %** |

![Actual vs predicted water level](artifacts/forecast_example.png)

## B — Unsupervised anomaly detection

An Isolation Forest learns the joint distribution of well-agnostic features
(rolling z-scores of level and salinity, day-over-day rates of change, and
management-area rainfall) and flags readings that don't fit. It uses no labels
or manually defined hydrogeological threshold; the operating point is set with
a 1% contamination parameter.

As a validation check, the script reports how many of the deliberately-injected
anomaly scenarios it independently rediscovers — it surfaces the planted rapid
level change (well 1) and the coastal salinity-intrusion trend (well 5) without
being told where to look.

## Running

```bash
pip install -r requirements.txt
python ml/forecast_train.py         # trains, evaluates, writes artifacts/
python ml/anomaly_unsupervised.py   # scores every reading, writes artifacts/
pytest tests/test_ml.py -v          # leakage, split and detection guards
```

Regenerable outputs (model binaries, full scored CSVs) are git-ignored; the
small result summaries and the example chart are committed.

## Deployment note

Both models are plain scikit-learn artefacts. The trained `forecast_model.joblib`
can be loaded by the existing Azure Function for scheduled scoring, or hosted via
Azure ML — no architectural change is required, only deploying the model artefact.
