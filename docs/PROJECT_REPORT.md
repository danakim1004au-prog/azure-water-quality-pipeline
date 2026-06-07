# Project Report — Regional Water Quality & Groundwater Monitoring Pipeline

A detailed technical report covering the problem context, architecture, data
provenance, analytical methodology, the deployed Azure environment, and how to
reproduce the work.

---

## 1. Executive summary

Regional water security depends on continuously monitoring dozens of
groundwater systems: understanding how each aquifer responds to rainfall,
detecting when a bore is being drawn down faster than it can recharge, and
catching the early signature of saline intrusion before it becomes
irreversible. Doing this well means turning a noisy, multi-source stream of
environmental readings into a small number of trustworthy, explainable signals
that a decision-maker can act on.

This project implements that workflow end to end on Azure. It is built around
three independent public data sources, a state-driven infrastructure-as-code
deployment, a transparent statistical anomaly-detection engine, and a Power BI
decision dashboard connected live to the cloud warehouse. The emphasis
throughout is on **explainability and data integrity** — every figure on the
dashboard can be traced back to a source reading, and every alert back to a
documented threshold and rationale.

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
                                              Azure Function (anomaly scorer, daily)
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
| Orchestration | Azure Data Factory | Schedules the daily ETL and triggers detection |
| Storage | Azure SQL Database (Basic) | Curated, query-optimised analytical store |
| Analytics | Azure Functions (Python) | Runs the three anomaly detectors daily |
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

The ingestion layer is built to collect from three independent, openly-licensed
public sources. Endpoints are configurable via environment variables in
[`ingestion/config.py`](../ingestion/config.py).

| Source | What it provides | Access method | Licence |
|--------|------------------|---------------|---------|
| **WaterConnect** (state groundwater portal) | Drillhole metadata, water level (metres below ground level) and salinity (total dissolved solids, mg/L) | REST endpoint, or the typed `sa_gwdata` helper library | Creative Commons Attribution |
| **SILO** (national gridded climate service) | Daily rainfall by station — Kent Town (23090), Port Augusta (18201), Ceduna (18012) | HTTP CSV request with contact email for attribution | Open / Creative Commons |
| **water.data.sa.gov.au** (surface-water portal) | Near-real-time river and reservoir levels | Bulk export API | Open |

Target study area: the Central Adelaide, Barossa and McLaren Vale groundwater
management areas — all real, publicly-defined prescribed water areas. The
station identifiers and management-area names used in the project are genuine.

### 4.2 Validating the pipeline: a schema-identical demonstration dataset

> The dashboard and the Azure SQL database demonstrated in this report are
> populated through a schema-identical, hydrologically-modelled dataset rather
> than a live pull from the APIs above — see the rationale and honesty note
> below.

Building a monitoring system around live government APIs introduces
credentials, rate limits and upstream availability as dependencies of the demo
itself. To keep the project fully reproducible, explainable and verifiable
end-to-end, the demonstration warehouse is populated by
[`scripts/generate_sample_data.py`](../scripts/generate_sample_data.py): a
deterministic generator (fixed random seed) built on a light hydrological
model — a winter-dominant (Mediterranean-climate) rainfall pattern, a
rainfall-driven recharge response on each bore, gentle long-term drawdown, and
mean-reverting salinity, all using the genuine drillhole numbers, station IDs
and management areas described in Section 4.1.

On top of that realistic baseline, **three anomaly scenarios are deliberately
injected** — a rapid water-level change, a stalled rainfall-recharge response
and a coastal salinity-intrusion trend — which gives the detectors and the
dashboard known-answer cases to be validated against, rather than hoping a real
anomaly happens to occur in a short observation window. This is the same
technique used to test monitoring and alerting systems generally: known faults
are injected so that detection, thresholds and reporting can be proven to work
*before* being pointed at production data.

To be scrupulously clear: this is a **demonstration and validation dataset**,
not a record of live readings. The ingestion clients for the real sources are
implemented and included; pointing them at the live APIs and reconciling the
result is listed as the natural next step in Section 11.

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

Two reporting views ([`sql/02_indexes_and_views.sql`](../sql/02_indexes_and_views.sql))
pre-shape the data so DirectQuery stays responsive:

- `vw_well_status` — classifies each bore as Normal / Watch / Critical from its
  most recent anomalies (drives the overview map).
- `vw_rainfall_recharge` — aligns daily rainfall with water level by management
  area (drives the recharge analysis).

Every fact table carries a `data_quality_flag` and an `ingested_at` timestamp;
the design rationale is in Section 9.

---

## 6. Anomaly-detection methodology

The detectors are statistical and transparent rather than black-box, because in
a water-security context every alert must be explainable to an engineer, a
regulator or a decision-maker. Each event records a plain-language `detail`
string describing exactly why it fired.

| Detector | What it flags | Method | Thresholds |
|----------|---------------|--------|------------|
| Rapid Level Change | An abrupt water-level move | Z-score of the latest reading against a trailing 7-day mean/standard deviation (current point excluded from its own baseline) | ≥ 2σ → WARNING, ≥ 3σ → CRITICAL |
| Low Recharge Response | A bore that fails to recharge after rain | After a 7-day rainfall total above 20 mm, the water table should rise ≥ 0.10 m within 14 days | otherwise → WARNING |
| Salinity Intrusion Risk | A sustained upward salinity trend | Ordinary-least-squares fit of TDS over a 30-day window | slope > 1 mg/L/day and R² ≥ 0.5; coastal → CRITICAL |

The reasoning behind each threshold is documented in full in
[`docs/anomaly_methodology.md`](anomaly_methodology.md). The logic is verified
by six unit tests ([`tests/test_detectors.py`](../tests/test_detectors.py))
that run on synthetic frames with no cloud or database dependency, so they
execute in CI on every push.

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

### 7.2 Regional overview
![Regional overview](../powerbi/screenshots/02_regional_overview.png)

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

The full build instructions (data model, DAX measures, visual-by-visual layout
and colour scheme) are in
[`powerbi/DASHBOARD_GUIDE.md`](../powerbi/DASHBOARD_GUIDE.md).

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

Supporting trustworthy, compliance-grade reporting was an explicit goal, so
several integrity controls are built in:

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
python scripts/generate_sample_data.py      # synthetic dataset
python scripts/run_detectors_offline.py     # run the detectors, write anomaly_events.csv
pytest tests/ -v                            # 6 detector unit tests
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

Point Power BI at the `sql_server_fqdn` Terraform output (see
[`powerbi/DASHBOARD_GUIDE.md`](../powerbi/DASHBOARD_GUIDE.md)), then
`terraform destroy` when finished.

---

## 11. Limitations and future work

In the interest of an honest account:

- **Live ingestion is implemented but not yet validated against the real
  APIs.** The clients normalise the documented response shapes and include
  fallbacks, but the exact field names and drillhole identifiers should be
  confirmed against a live call before relying on real data.
- **Data Factory and the Function App are provisioned but not populated.** The
  Terraform deploys both resources; importing the pipeline definitions in
  [`adf/`](../adf/) and deploying the function code is the next step to make the
  orchestration and scheduled detection run in the cloud (the detection logic
  itself is complete and tested).
- **The Logic App workflow is defined but not connected** to live email/Teams
  connectors.
- **The dashboard currently runs on the schema-identical demonstration dataset**
  described in Section 4.2, not live API readings; pointing the existing
  ingestion clients at the live sources and reconciling the result is the
  natural next step.

These are scoping decisions for a portfolio build rather than gaps in the
design — each remaining step is a configuration/credentials task on top of
code that already exists.

---

## 12. Attribution and licence

All designed data sources are public and openly licensed (Creative Commons
Attribution or equivalent). When real data is ingested, attribution to the
originating agencies is required and is recorded against each source in
[`ingestion/config.py`](../ingestion/config.py). The synthetic demonstration
data is generated by this project and carries no third-party licence.
