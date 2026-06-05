# Regional Water Quality & Groundwater Monitoring Pipeline

> South Australia's water security depends on monitoring dozens of regional
> groundwater systems — tracking how aquifer levels respond to rainfall,
> watching for salinity intrusion along the coast, and turning a constant
> stream of sensor readings into decisions a human can act on.

This project is an end-to-end, cloud-native implementation of that workflow. It
ingests **real, publicly-licensed** South Australian groundwater, rainfall and
surface-water data, lands it in **Azure SQL** via an **Azure Data Factory**
pipeline, scores it for anomalies with an **Azure Function**, raises alerts
through **Logic Apps**, and presents everything in a four-page **Power BI**
decision dashboard. The whole stack is provisioned with **Terraform**.

---

## What it does

```
[ Public data sources ]
 Groundwater REST API (levels + salinity) ──┐
 Climate API (daily rainfall)               ├─► Azure Data Factory ─► Azure SQL Database
 Surface-water portal (river/reservoir)     ┘     (daily ETL)              │
                                                                           │
                                              Azure Function (anomaly scorer, daily)
                                                          │
                                              Azure SQL · anomaly_events
                                                          │
                                   ┌──────────────────────┴───────────────────────┐
                                   ▼                                               ▼
                          Power BI dashboard                       Logic Apps → Email / Teams
                       (4 pages, DirectQuery)                      (CRITICAL alerts)
```

1. **Ingest** – Python clients pull groundwater levels & salinity (TDS),
   daily rainfall, and near-real-time surface-water levels from three
   independent public APIs and upsert them idempotently into Azure SQL.
2. **Detect** – a daily Azure Function runs three domain-specific, statistical
   anomaly detectors and writes events to `anomaly_events`.
3. **Alert** – a Logic App polls for new `CRITICAL` events and dispatches
   email / Teams notifications.
4. **Decide** – a Power BI report turns it all into a regional map, trend
   analysis, rainfall-recharge correlation, and an anomaly log.

---

## Anomaly detection (the technical core)

Three transparent, explainable detectors — each encoding a piece of
hydrogeological domain knowledge. Full rationale and threshold justification in
[`docs/anomaly_methodology.md`](docs/anomaly_methodology.md); logic in
[`functions/anomaly_detector/detectors.py`](functions/anomaly_detector/detectors.py);
verified by [`tests/test_detectors.py`](tests/test_detectors.py).

| Detector | Signal | Method | Why it matters |
|----------|--------|--------|----------------|
| **Rapid Level Change** | Sudden water-level move | Z-score vs a trailing 7-day baseline (±2σ → WARNING, ±3σ → CRITICAL) | Over-extraction, adjacent drawdown, drought-stalled recharge |
| **Low Recharge Response** | No rebound after rain | After a 7-day rainfall total > 20 mm, expect a ≥ 0.10 m rise within 14 days | Leading indicator of declining aquifer health |
| **Salinity Intrusion Risk** | Sustained rising TDS | OLS trend over 30 days (slope > 1 mg/L/day, R² ≥ 0.5); coastal wells escalate to CRITICAL | Early signature of seawater intrusion |

The detectors are deliberately statistical rather than black-box: in a water
security context, every alert must be explainable back to a threshold and a
reason — which is exactly what each event's `detail` field records.

---

## Power BI dashboard

Four pages, designed to feel like an operational monitoring view. Full build
instructions (connection, data model, DAX, visual-by-visual layout, colour
scheme) are in [`powerbi/DASHBOARD_GUIDE.md`](powerbi/DASHBOARD_GUIDE.md).

1. **Regional Overview** – a map of every monitoring well coloured by status
   (Normal / Watch / Critical), with KPI cards.
2. **Groundwater Trend Analysis** – per-well level time series, 7-day moving
   average, seasonality matrix, and a forecast/trend line.
3. **Rainfall–Recharge Correlation** – rainfall bars overlaid on the water-level
   line, with recharge-lag comparison by region.
4. **Anomaly Event Log** – filterable event table, severity colours, and a
   month × type frequency heatmap.

<!-- Add screenshots once built:
![Regional Overview](powerbi/screenshots/01_overview.png)
![Trend Analysis](powerbi/screenshots/02_trend.png)
![Rainfall–Recharge](powerbi/screenshots/03_recharge.png)
![Anomaly Log](powerbi/screenshots/04_anomaly_log.png)
-->

---

## Try it without Azure (offline demo)

The full pipeline runs in the cloud, but you can exercise the detection logic
and produce Power-BI-ready data locally in under a minute:

```bash
pip install -r requirements.txt
python scripts/generate_sample_data.py      # hydrologically plausible synthetic data
python scripts/run_detectors_offline.py     # runs the real detectors over it
pytest tests/ -v                            # verify the detection logic
```

This writes `sample_data/*.csv` (including `anomaly_events.csv`) that Power BI
Desktop can load directly via the CSV path in the dashboard guide. The
synthetic generator deliberately plants one rapid-level-change and one coastal
salinity-intrusion event so the dashboard and alerts have something to show.

---

## Deploy to Azure

See [`infra/README.md`](infra/README.md) for the full walkthrough.

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
export TF_VAR_sql_admin_password='<a-strong-password>'
terraform init && terraform apply
```

Then apply the SQL scripts (`sql/01…03`), deploy the Function (`functions/`),
import the ADF artifacts (`adf/`), and point Power BI at the `sql_server_fqdn`
output.

**Cost:** ~AUD 5–10/month (Azure SQL Basic + consumption Functions/ADF); a
two-week run is a few dollars. `terraform destroy` when finished.

---

## Data sources & licensing

| Source | Data | Access | Licence |
|--------|------|--------|---------|
| State groundwater portal | Drillhole water levels (mBGL) & salinity (TDS) | REST API / `sa_gwdata` helper | Creative Commons Attribution |
| National climate service | Daily rainfall by station | HTTP CSV (email attribution) | Open / CC |
| Surface-water portal | Near-real-time river & reservoir levels | Bulk export API | Open |

All sources are public and openly licensed, which is what makes this an
appropriate open portfolio project. Source endpoints are configurable via
environment variables in [`ingestion/config.py`](ingestion/config.py).

### Data integrity & quality handling

- Every fact table carries a `data_quality_flag` (`measured` / `interpolated`
  / `suspect`) and an `ingested_at` timestamp.
- Non-physical readings (negative rainfall, impossible water levels) are
  flagged `suspect` at ingestion and **excluded from anomaly detection**, so a
  bad sensor value can't trigger a false alert.
- Loads are **idempotent** (MERGE on natural keys), so re-running a window
  updates rather than duplicates.
- Pipeline failures are recorded in `monitoring.pipeline_audit`, so data gaps
  are explainable during reporting.
- Natural keys from each source system are preserved for reconciliation.

---

## Repository structure

```
azure-water-quality-pipeline/
├── infra/                 # Terraform IaC (resource group, SQL, ADF, Function, Key Vault)
├── ingestion/             # Python source clients + Azure SQL loader
├── functions/             # Azure Function: anomaly detection
│   └── anomaly_detector/  #   detectors.py · db.py · __init__.py
├── adf/                   # Data Factory linked services, pipeline, trigger (JSON)
├── logic_apps/            # CRITICAL-event email/Teams alert workflow
├── sql/                   # Schema, views, audit table, stored procedures
├── powerbi/               # Dashboard build guide (+ screenshots)
├── scripts/               # Offline sample-data generator & detector runner
├── tests/                 # Unit tests for the detectors
├── docs/                  # Anomaly-detection methodology
└── .github/workflows/     # CI (lint + test + tf validate) and Deploy
```

---

## Engineering notes

- **Security**: no secrets in the repo. The SQL password is supplied via an
  environment variable to Terraform; the connection string lives in Key Vault
  and is read at runtime by the Function's managed identity.
- **Testing/CI**: detector logic is covered by unit tests that run on synthetic
  frames (no cloud needed); CI also runs `terraform validate` and a linter.
- **Idempotency**: both ingestion and detection de-duplicate, so the daily
  schedule is safe to re-run.

---

### A note on design lineage

This platform shares its skeleton with an earlier real-time EV-charging
analytics project — *sensor data → Azure pipeline → anomaly detection →
visualisation + alerting* — applied here to environmental monitoring. The EV
system handled high-frequency IoT streams with an ML detector and operator
alerts; this one handles batch environmental readings with transparent
statistical detection and a decision-maker dashboard. Same structural problem,
two domains.
