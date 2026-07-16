# Project Report — AquaSentry: Multi-Aquifer Groundwater Monitoring & Forecasting

A detailed technical report covering the problem context, architecture, data
provenance, analytical methodology, the deployed Azure environment, and how to
reproduce the work.

---

## 1. Executive summary

Water security depends on continuously monitoring dozens of
groundwater systems: understanding how each aquifer responds to rainfall,
detecting when a bore is being drawn down faster than it can recharge, and
catching the early signature of saline intrusion before it becomes
irreversible. Doing this well means turning a noisy, multi-source stream of
environmental readings into a small number of trustworthy, explainable signals
that a decision-maker can act on.

This project implements that workflow end to end on Azure. It is built around
three independent public data sources, a state-driven infrastructure-as-code
deployment, a two-layer analytics engine — transparent statistical detectors
*plus* machine-learning models for forecasting and open-ended anomaly
detection — and a Power BI decision dashboard connected live to the cloud
warehouse. The emphasis throughout is on **explainability and data integrity**:
every figure on the dashboard can be traced back to a source reading, every
rule-based alert back to a documented threshold, and every learned model back to
a leakage-safe evaluation against a held-out future.

---

## 2. Problem context

Groundwater is a slow-moving, easily-degraded resource. By the time a problem
is obvious in a single reading, the underlying trend is usually well
established. The analytical questions that matter are therefore mostly about
*change over time* and *relationships between variables*:

- Is a bore's water level moving abnormally fast relative to its own recent
  behaviour?
- After meaningful rainfall, is the aquifer actually recharging — or has the
  link between rainfall and recovery broken down?
- Is salinity creeping upwards in a way that signals seawater intrusion in a
  coastal aquifer?

These questions map directly onto the three detectors described in Section 6.

---

## 3. Architecture

```
[ Public data sources ]
 Groundwater REST API (levels + salinity) ──┐
 Climate API (daily rainfall)               ├─► Azure Data Factory ─► Azure SQL Database
 Surface-water portal (river/reservoir)     ┘     (daily ETL)              │
                                                                           │
                              Azure Function (daily 06:00 UTC timer) ──────┤
                              · rule-based detectors → anomaly_events      │
                              · ML forecast + Isolation Forest (offline;   │
                                cloud inference planned)                   │
                                                          │
                                              Azure SQL · anomaly_events
                                                          │
                                   ┌──────────────────────┴───────────────────────┐
                                   ▼                                               ▼
                          Power BI dashboard                       Logic Apps → Email / Teams
                       (DirectQuery to Azure SQL)                  (CRITICAL alerts)
```

| Layer | Technology | Responsibility |
|-------|-----------|----------------|
| Infrastructure | Terraform | Provisions every Azure resource as code |
| Ingestion | Python (requests, pandas, SQLAlchemy) | Pulls each source, normalises it, upserts to Azure SQL |
| Orchestration | Azure Data Factory | Schedules the daily ETL (05:00 UTC trigger) |
| Storage | Azure SQL Database (Basic) | Curated, query-optimised analytical store |
| Analytics (rules) | Azure Functions (Python) | Runs the three anomaly detectors daily |
| Analytics (ML) | scikit-learn (gradient boosting, Isolation Forest) | Month-ahead level forecast and unsupervised anomaly detection |
| Secrets | Azure Key Vault | Holds the SQL connection string; read via managed identity |
| Alerting | Azure Logic Apps | Emails / posts Teams messages on CRITICAL events |
| Visualisation | Power BI (DirectQuery) | Four-view decision dashboard |
| CI/CD | GitHub Actions | Lints, unit-tests and validates infrastructure |

Terraform was chosen deliberately as a counterpart to a Bicep-based deployment
elsewhere, demonstrating the same Azure resources expressed in a
cloud-agnostic, state-driven workflow.

---

## 4. Data provenance

### 4.1 Designed data sources

The ingestion layer is built to collect from three independent, openly licensed
public sources. Endpoints are configurable via environment variables in
[`ingestion/config.py`](../ingestion/config.py).

| Source | What it provides | Access method | Licence |
|--------|------------------|---------------|---------|
| **WaterConnect** (state groundwater portal) | Drillhole metadata, water level (metres below ground level) and salinity (total dissolved solids, mg/L) | REST endpoint, or the typed `sa_gwdata` helper library | Creative Commons Attribution |
| **SILO** (national gridded climate service) | Daily rainfall by station — Edinburgh (23083), Nuriootpa (23321), Willunga (23753), one near each management area | HTTP CSV request with contact email for attribution | Open / Creative Commons |
| **water.data.sa.gov.au** (surface-water portal) | Near-real-time river and reservoir levels | Bulk export API | Open |

The study area covers the Northern Adelaide Plains, Barossa and McLaren Vale.
The demonstration coordinates, aquifer names, station identifiers and
drillhole identifiers are representative values. They must be checked against
the source systems before the ingestion clients are used with observed data.

### 4.2 Validating the pipeline: a schema-identical demonstration dataset

> The dashboard and the Azure SQL database demonstrated in this report are
> populated through a schema-identical, hydrologically-modelled dataset rather
> than a live pull from the APIs above — see the rationale and honesty note
> below.

Building a monitoring system around live government APIs introduces
credentials, rate limits and upstream availability as dependencies. The
demonstration warehouse is therefore populated by
[`scripts/generate_sample_data.py`](../scripts/generate_sample_data.py): a
generator with a fixed random seed and reference date, built on a light hydrological
model — a winter-dominant (Mediterranean-climate) rainfall pattern, a
rainfall-driven recharge response on each bore, gentle long-term drawdown, and
mean-reverting salinity. Rainfall stations are mapped to the relevant
groundwater management area.

On top of that realistic baseline, **three anomaly scenarios are deliberately
injected** — a rapid water-level change, a stalled rainfall-recharge response
and a coastal salinity-intrusion trend — which gives the detectors and the
dashboard known-answer cases to be validated against, rather than hoping a real
anomaly happens to occur in a short observation window. This is the same
technique used to test monitoring and alerting systems generally: known faults
are injected so that detection, thresholds and reporting can be proven to work
*before* being pointed at production data.

This is a **demonstration and validation dataset**, not a record of observed
readings. The ingestion clients are included but have not yet been reconciled
against live API responses.

---

## 5. Data model and schema

The warehouse schema lives in [`sql/01_schema.sql`](../sql/01_schema.sql) under
the `monitoring` schema. It is intentionally narrow and normalised; Power BI
builds its star model on top via relationships.

| Table | Grain | Notes |
|-------|-------|-------|
| `monitoring_wells` | one row per bore | natural key `source_dh_no`; `coastal_flag` drives salinity escalation |
| `water_level_readings` | one row per bore per day | water level (mBGL) and TDS (mg/L) |
| `rainfall_observations` | one row per station per day | daily rainfall (mm) |
| `surface_water_readings` | one row per site/metric/time | enrichment source |
| `anomaly_events` | one row per detected event | type, severity, threshold vs actual, explanation |
| `water_licences` | one row per licence period and management area | allocation and compliance limit |
| `metered_extraction` | one row per bore per reporting month | extraction volume and coverage days |
| `water_security_risk_snapshots` | one row per bore per snapshot date | forecast, allocation use, data completeness, risk and recommendation |

Four reporting views ([`sql/02_indexes_and_views.sql`](../sql/02_indexes_and_views.sql))
pre-shape the data so DirectQuery stays responsive:

- `vw_well_status` — classifies each bore as Normal / Watch / Critical from its
  most recent anomalies (drives the overview map).
- `vw_rainfall_recharge` — aligns daily rainfall with water level by management
  area (drives the recharge analysis).
- `vw_water_security_risk` — returns the latest bore-level Phase 2 risk snapshot.
- `vw_licence_compliance` — summarises extraction against allocation by licence period.

Every fact table carries a `data_quality_flag` and an `ingested_at` timestamp;
the design rationale is in Section 9.

---

## 6. Detection & forecasting methodology

Analytics are deliberately built in two layers. Transparent statistical rules
handle the failure modes you can name — auditable and explainable, which a
water-security/compliance context demands. Machine-learning models then add what
rules cannot: a forward-looking forecast, and open-ended detection of anomalies
no rule anticipates. The two are complementary, not competing.

### 6.1 Rule-based detectors (explainable)

The detectors are statistical and transparent rather than black-box, because
every alert must be explainable to an engineer, a regulator or a decision-maker.
Each event records a plain-language `detail` string describing exactly why it
fired.

| Detector | What it flags | Method | Thresholds |
|----------|---------------|--------|------------|
| Rapid Level Change | An abrupt water-level move | Z-score of the latest reading against a trailing 7-day mean/standard deviation (current point excluded from its own baseline) | ≥ 2σ → WARNING, ≥ 3σ → CRITICAL |
| Low Recharge Response | A bore that fails to recharge after rain | After a 7-day rainfall total above 20 mm, the water table should rise ≥ 0.10 m within 14 days | otherwise → WARNING |
| Salinity Intrusion Risk | A sustained upward salinity trend | Ordinary-least-squares fit of TDS over a 30-day window | slope > 1 mg/L/day and R² ≥ 0.5; coastal → CRITICAL |

The reasoning behind each threshold is documented in full in
[`docs/anomaly_methodology.md`](anomaly_methodology.md). The logic is verified
by seven unit tests ([`tests/test_detectors.py`](../tests/test_detectors.py))
that run on synthetic frames with no cloud or database dependency, so they
execute in CI on every push.

### 6.2 Groundwater-level forecasting (supervised ML)

To anticipate supply pressure rather than only react to it,
[`ml/forecast_train.py`](../ml/forecast_train.py) trains a gradient-boosted
regression model (`GradientBoostingRegressor`) to predict each bore's water
level a month ahead. Features known at prediction time only: lagged and rolling
water levels (1/7/14/30-day), trailing rainfall totals (7/14/30-day), salinity,
and a smooth day-of-year seasonality encoding.

Two choices make the reported skill trustworthy in a time-series setting:

- **No look-ahead leakage.** Every feature uses only information available at
  the prediction time; the target is a strictly future value. A unit test
  asserts the feature matrix never contains the raw target.
- **Purged chronological split.** Train and test are split strictly in time —
  never randomly shuffled — with a horizon-wide purge gap, so a training label
  can never fall inside the test window. Skill is benchmarked against a naive
  **persistence** baseline ("next month looks like today"); beating it is what
  proves the model adds value.

The model's advantage grows with the horizon as it learns the seasonal recharge
cycle that persistence cannot represent
([`ml/artifacts/forecast_horizon_sweep.csv`](../ml/artifacts/forecast_horizon_sweep.csv)):

| Horizon | Model MAE | Persistence MAE | Improvement |
|--------:|----------:|----------------:|------------:|
| 7 days  | 0.138 m   | 0.124 m         | −11.5 %     |
| 14 days | 0.167 m   | 0.151 m         | −10.6 %     |
| 30 days | 0.203 m   | 0.208 m         | +2.2 %      |
| 45 days | 0.217 m   | 0.250 m         | +13.3 %     |
| 60 days | 0.234 m   | 0.304 m         | +22.8 %     |

The negative values at short horizons are reported deliberately and honestly:
at close range the series is so autocorrelated that persistence is genuinely
hard to beat, so the model is positioned at the month-ahead operational horizon
where it adds real foresight.

### 6.3 Unsupervised anomaly detection (ML)

[`ml/anomaly_unsupervised.py`](../ml/anomaly_unsupervised.py) fits an
`IsolationForest` over well-agnostic features — rolling z-scores of level and
salinity, day-over-day rates of change, and trailing rainfall — to learn the
joint shape of "normal" across the fleet and flag points that don't fit. It
uses no labelled outcomes or manually defined hydrogeological threshold; the
operating point is set with a 1% contamination parameter. The injected rapid
level and salinity cases provide a repeatable pipeline check.

Both models are verified by [`tests/test_ml.py`](../tests/test_ml.py), which
guards the leakage-safe split, the absence of target leakage, the model's win
over persistence at range, and the rediscovery of the injected anomalies — all
on the committed sample data, with no cloud dependency.

### 6.4 Regional water-security decision support

[`analytics/water_security_risk.py`](../analytics/water_security_risk.py)
combines a 60-day trend projection and 80% interval with licence allocation,
metered extraction, anomaly context and 30-day data completeness. It produces
a bore-level risk score, status, recorded drivers and a recommended action.
The allocation and extraction values are demonstration scenarios rather than
regulatory records.

---

## 7. Dashboard walk-through

The Power BI report connects to Azure SQL with **DirectQuery**, so each visual
reflects the live warehouse rather than an imported snapshot.

### 7.1 Data model
![Power BI data model](../powerbi/screenshots/01_data_model.png)

A star-style model: `monitoring_wells` and a `Date` dimension relate to the
water-level, rainfall and anomaly fact tables. The anomaly-to-date relationship
is kept inactive so it can be invoked explicitly where needed without
distorting the default filter context.

### 7.2 Operational overview
![Operational overview across management areas](../powerbi/screenshots/02_overview.png)

The at-a-glance operational view: KPI cards (bores monitored, critical and
warning counts, average water level), a map of every bore coloured by status,
and a status breakdown by management area. This is the page designed to be
recognised immediately as a real monitoring view.

### 7.3 Groundwater trend & rainfall–recharge
![Groundwater trend and rainfall](../powerbi/screenshots/03_groundwater_trend.png)

Water level with its 7-day moving average (the Y-axis is inverted so a deeper
water table reads lower on the chart), above daily rainfall. The seasonal
winter rainfall peaks align with the subsequent water-table recovery, making
the recharge response visible — exactly the rainfall-to-level relationship the
Low Recharge Response detector formalises.

### 7.4 Advanced analytics
![Advanced analytics](../powerbi/screenshots/04_advanced_analytics.png)

A rainfall-versus-water-level scatter and a per-bore table that rolls up to the
current Normal / Watch / Critical classification, surfacing the bores carrying
an active critical signal.

---

## 8. Azure deployment

The platform was deployed to Azure with Terraform and the curated dataset
loaded into Azure SQL, so the Power BI report queries it live.

![Azure resource group](screenshots/azure_resource_group.png)

The curated data is queryable live in Azure SQL — here the `monitoring` schema
and its tables, views and stored procedures in the portal query editor:

![Azure SQL query editor](screenshots/azure_sql_query.png)

| Resource | Name | Tier / notes |
|----------|------|--------------|
| Resource group | `rg-wq4dt002-dev` | region: Korea Central |
| Azure SQL Database | `sqldb-water-monitoring` | Basic (5 DTU) |
| Data Factory | `adf-wq4dt002-dev-…` | ETL orchestration |
| Function App | `func-wq4dt002-dev-…` | Linux consumption plan |
| Storage account | `stwq4dt002…` | raw landing zone |
| Key Vault | `kv-wq4dt002-…` | holds the SQL connection string |

The schema was applied with
[`scripts/apply_schema.py`](../scripts/apply_schema.py) and the curated data
loaded with
[`scripts/load_sample_to_azure.py`](../scripts/load_sample_to_azure.py). A
shared connection helper ([`scripts/_db.py`](../scripts/_db.py)) supports both
the ODBC and pymssql drivers so the load runs from any operating system.

**Running cost** is approximately AUD 5–10 per month — dominated by the Basic
SQL database; Data Factory, Functions and Logic Apps are effectively free at
this volume. The environment is intended to be torn down with
`terraform destroy` once screenshots are captured.

---

## 9. Data quality and integrity

Several controls support traceable reporting and decision review:

- **Quality flags.** Every fact table carries a `data_quality_flag`
  (`measured` / `interpolated` / `suspect`). Non-physical readings — negative
  rainfall, impossible water levels — are flagged `suspect` at ingestion and
  **excluded from anomaly detection**, so a faulty sensor value can never
  trigger a false alert.
- **Idempotent loads.** Data is upserted with `MERGE` on natural keys, so
  re-running any window updates rows rather than duplicating them.
- **Auditability.** Pipeline failures are recorded in
  `monitoring.pipeline_audit`, so any gap in the data is explainable rather
  than silent.
- **Reconciliation.** Source natural keys (drillhole numbers, station IDs) are
  preserved end to end so the warehouse can be reconciled against the source of
  truth at any time.
- **Secret hygiene.** No secrets in the repository: the SQL password is
  supplied to Terraform via an environment variable, and the connection string
  is stored in Key Vault and read at runtime via the Function's managed
  identity.

---

## 10. Reproducing the project

### Offline (no Azure)

```bash
pip install -r requirements.txt
python scripts/generate_sample_data.py      # demonstration dataset
python scripts/run_detectors_offline.py     # run the rule-based detectors
python ml/forecast_train.py                 # train + evaluate the level forecast
python ml/anomaly_unsupervised.py           # learned multivariate anomaly detection
python analytics/water_security_risk.py     # risk and licence-compliance snapshot
pytest tests/ -v                            # 18 automated tests
```

### On Azure

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars      # set allowed_client_ip
export TF_VAR_sql_admin_password='<a-strong-password>'
terraform init && terraform apply

# then, with the AZURE_SQL_* environment variables set:
python ../scripts/apply_schema.py
python ../scripts/load_sample_to_azure.py
```

Point Power BI at the `sql_server_fqdn` Terraform output, then
`terraform destroy` when finished.

---

## 11. Limitations and future work

- **Live ingestion is implemented but not yet validated against the real
  APIs.** The clients normalise the documented response shapes and include
  fallbacks, but the exact field names and drillhole identifiers should be
  confirmed against a live call before relying on real data.
- **Terraform provisions the core infrastructure, not the application
  artifacts.** It stands up the resource group, Azure SQL, Data Factory,
  Function App, Storage and Key Vault. The SQL schema/views/procedures, the ADF
  pipeline/linked services/trigger, the Logic App workflow, and the Function
  code are applied/deployed separately (via the scripts and the Deploy
  workflow) — so "Core Azure infrastructure as code", not "the entire stack as
  one `terraform apply`".
- **Data Factory and the Function App are provisioned but not populated.** The
  ADF `Custom` activity references an **Azure Batch pool** (`ls_batch`, staging
  through `ls_staging`), but the Batch account/pool itself and the Key Vault
  secrets it resolves are **not** provisioned by the Terraform and must be
  supplied — or swap `RunIngestion` for a **Container Apps Job / Web Activity**.
  Importing the pipeline definitions in [`adf/`](../adf/) and deploying the
  function code is the next step.
- **Anomaly detection runs on the Function's own daily 06:00 UTC timer**, one
  hour after the 05:00 UTC ingestion trigger, rather than being invoked by ADF —
  the Function is timer-triggered, so there is no HTTP endpoint for ADF to call.
  UTC is used on both sides because Linux Consumption Functions schedule in UTC
  and do not honour `WEBSITE_TIME_ZONE`; an explicit ADF→Function hand-off would
  instead need an HTTP-triggered Function.
- **The ML models run offline; cloud scoring is the next step.** Training and
  evaluation are complete and tested, and the saved `forecast_model.joblib` is a
  plain scikit-learn artefact that the Function code (or Azure ML) can load for
  scheduled inference — a deployment task, not a modelling one. There is no
  `forecasts` table in the schema yet; cloud inference would add one.
- **The Logic App workflow is defined but not connected** to live email/Teams
  connectors.
- **The dashboard currently runs on the schema-identical demonstration dataset**
  described in Section 4.2, not live API readings; pointing the existing
  ingestion clients at the live sources and reconciling the result is the
  natural next step.

The Phase 2 licence, extraction and risk records are representative scenarios.
They do not constitute a regulatory compliance assessment.

---

## 12. Attribution and licence

All designed data sources are public and openly licensed (Creative Commons
Attribution or equivalent). When real data is ingested, attribution to the
originating agencies is required and is recorded against each source in
[`ingestion/config.py`](../ingestion/config.py). The synthetic demonstration
data, licence allocations and extraction scenarios are generated by this
project and carry no third-party licence.
