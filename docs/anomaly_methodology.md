# Anomaly Detection Methodology

This document explains *what* each detector flags, *why* the threshold was
chosen, and *how* it maps to groundwater monitoring practice. The detectors
are deliberately statistical and transparent rather than black-box: in a water
security context every alert must be explainable to an engineer, a regulator,
or a decision-maker.

Implementation: [`functions/anomaly_detector/detectors.py`](../functions/anomaly_detector/detectors.py).
Verified by unit tests in [`tests/test_detectors.py`](../tests/test_detectors.py).

---

## 1. Rapid Water-Level Change

**What it flags.** A single daily water-level reading that deviates sharply
from its own recent history.

**Method.** For each well we maintain a trailing 7-day rolling mean and
standard deviation of `water_level_mBGL`, computed from the readings *before*
the current day (the current reading is excluded from its own baseline so a
genuine spike is not averaged into the very baseline it is measured against).
The current reading's z-score is:

```
z = |current_level - rolling_mean(7d)| / rolling_std(7d)
```

| z-score | Severity |
|---------|----------|
| ≥ 2σ    | WARNING  |
| ≥ 3σ    | CRITICAL |

**Why these thresholds.** Under an approximately normal short-term baseline,
~5% of points fall beyond 2σ and ~0.3% beyond 3σ. A 7-day window is short
enough to react within a week yet long enough to smooth daily sensor noise. A
minimum of 10 prior observations is required before any flag is raised, so new
or sparsely-monitored wells do not generate spurious alerts.

**Hydrogeological meaning.** An abrupt deepening can indicate over-extraction
(a new or intensified pumping regime), drawdown interference from an adjacent
development, or a stalled recharge during drought. An abrupt shallowing can
indicate a sudden recharge event or, occasionally, a sensor fault — which is
why these are surfaced for review rather than acted on blindly.

---

## 2. Low Recharge Response

**What it flags.** A well that *fails to recharge* after a meaningful rainfall
event — a leading indicator of declining aquifer health that no single reading
would reveal.

**Method.**

1. Build a regional 7-day rolling rainfall total from the rainfall stations.
2. Identify the most recent **rainfall event**: a day where the 7-day total
   exceeds **20 mm**.
3. For each well, compare the water level immediately before the event to the
   shallowest level observed in the **14 days** after it.
4. If the water table rose by less than **0.10 m**, raise a
   `LowRechargeResponse` WARNING.

**Why these thresholds.** A 7-day accumulation of 20 mm is a commonly-used
lower bound for rainfall capable of producing measurable recharge in
temperate aquifers; smaller totals are typically lost to evapotranspiration and
soil moisture deficit. A 14-day response window reflects the typical lag
between rainfall and a water-table response in shallow-to-moderate aquifers.
The 0.10 m rise threshold filters out sensor noise while still catching wells
that are effectively unresponsive.

**Hydrogeological meaning.** A healthy unconfined aquifer rebounds after
significant rain. Persistent non-response suggests the aquifer is no longer
being effectively replenished — because of sustained over-extraction, reduced
catchment permeability, or a structural decline in storage. This is the kind
of trend that matters more than any one day's number.

---

## 3. Salinity Intrusion Risk

**What it flags.** A sustained upward trend in salinity (total dissolved
solids) — the early signature of saline intrusion, which is especially
consequential in coastal aquifers.

**Method.** Over a 30-day sliding window we fit an ordinary least-squares line
to `tds_mg_per_l` against time. We flag the well when **both**:

- the slope exceeds **1.0 mg/L per day** (a sustained rise, not noise), and
- the fit is reasonably linear, **R² ≥ 0.5** (a genuine trend, not scatter).

| Aquifer        | Severity |
|----------------|----------|
| Inland         | WARNING  |
| Coastal        | CRITICAL |

**Why these thresholds.** Requiring both a minimum slope *and* a minimum R²
distinguishes a true directional trend from random fluctuation around a stable
mean — a slope alone can be produced by a couple of noisy readings. Coastal
wells (flagged via `coastal_flag` derived from the aquifer/area metadata)
escalate to CRITICAL because for them a rising-TDS trend is the canonical
precursor to seawater intrusion.

**Hydrogeological meaning.** When extraction lowers the freshwater head near a
coast, the freshwater–saltwater interface advances inland and salinity climbs.
Catching the *trend* early — before any single reading breaches an absolute
limit — is what makes the signal actionable.

---

## Design notes

- **Idempotent.** Events are de-duplicated on `(well_id, event_date,
  anomaly_type)`, so the daily run only ever inserts genuinely new events.
- **Quality-aware.** Readings flagged `suspect` at ingestion are excluded from
  detection, so a known-bad sensor value cannot trigger a false alert.
- **Tunable.** Every threshold is a named constant at the top of
  `detectors.py`, so they can be calibrated per region as more is learned about
  each aquifer's behaviour.
- **Extensible.** The three detectors share one interface (`frame in → events
  frame out`), so adding a fourth — e.g. a seasonal-decomposition residual
  detector — is a localised change.

*The threshold choices above are informed by general groundwater monitoring
best practice (rolling-baseline deviation, rainfall-recharge response lag, and
salinity-trend surveillance). They are starting points intended to be
calibrated against an organisation's own historical data.*
