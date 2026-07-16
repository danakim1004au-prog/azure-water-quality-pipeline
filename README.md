# AquaSentry — Multi-Aquifer Groundwater Monitoring & Forecasting

A portfolio project for monitoring groundwater quantity and salinity across
multiple management areas. It combines Azure infrastructure, Python data
processing, Power BI reporting, statistical detection, machine-learning
experiments and a local OPC-UA HMI demonstration.

[![CI](https://github.com/danakim1004au-prog/azure-water-quality-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/danakim1004au-prog/azure-water-quality-pipeline/actions/workflows/ci.yml)
![Azure](https://img.shields.io/badge/Azure-SQL%20%C2%B7%20Data%20Factory%20%C2%B7%20Functions-0078D4?logo=microsoftazure&logoColor=white)
![Terraform](https://img.shields.io/badge/IaC-Terraform-7B42BC?logo=terraform&logoColor=white)
![Power BI](https://img.shields.io/badge/Power%20BI-DirectQuery-F2C811?logo=powerbi&logoColor=black)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![scikit-learn](https://img.shields.io/badge/scikit--learn-forecast%20%2B%20anomaly-F7931E?logo=scikitlearn&logoColor=white)
![SCADA HMI](https://img.shields.io/badge/SCADA%20HMI-OPC--UA%20%C2%B7%20FastAPI%20%C2%B7%20WebSocket-009688?logo=fastapi&logoColor=white)
![Tests](https://img.shields.io/badge/tests-18%20passing-2E8B57)

---

## Overview

AquaSentry brings groundwater levels, salinity and rainfall into a common data
model for surveillance, trend analysis and reporting. Three rule-based
detectors identify rapid level changes, weak recharge responses and sustained
salinity increases. Separate machine-learning experiments assess future water
levels and unusual combinations of readings.

The Power BI report uses DirectQuery against Azure SQL. The current database is
populated with a generated demonstration dataset containing six bores and
three known anomaly scenarios. Public-source ingestion clients are included,
but live API validation and reconciliation remain future work.

## Project scope

| Area | Current scope |
|---|---|
| **Monitoring dataset** | 6 bores · 3 management areas · 4 aquifers · approximately 3 years of daily demonstration data |
| **Forecast evaluation** | 30-day MAE of 0.203 m against a 0.208 m persistence baseline; stronger gains at longer horizons |
| **Anomaly detection** | 3 documented statistical detectors and an Isolation Forest evaluated against injected scenarios |
| **Decision support** | Bore-level risk status combining forecast range, licence use, anomaly context, data completeness and a recommended action |
| **Azure and reporting** | Terraform-provisioned Azure resources · demonstration data in Azure SQL · Power BI DirectQuery report |
| **Quality checks** | 18 automated tests covering detector behaviour, regional rainfall, time-series splitting, ML preparation, risk scoring and HMI startup |

## Project status

This is a portfolio implementation rather than a standing production system.

| Component | Status |
|-----------|--------|
| Rule-based anomaly detection | Implemented, tested and packaged as a 06:00 UTC timer-triggered Azure Function |
| ML forecasting and Isolation Forest | Implemented and tested offline; cloud inference is not deployed |
| Phase 2 decision support | Licence compliance, 60-day projection and bore-level risk snapshot implemented |
| Power BI dashboard | Built with DirectQuery against the demonstration Azure SQL database |
| SCADA HMI | Runs locally over OPC-UA and WebSocket with simulated process values |
| Core Azure infrastructure | Provisioned through Terraform |
| SQL, ADF and Logic App artefacts | Supplied separately from Terraform; runtime configuration remains required |
| Live public-source ingestion | Clients implemented; live endpoint validation remains outstanding |

---

## Dashboard

The four-page Power BI report supports operational review across management
areas. It uses DirectQuery so visuals query the Azure SQL database rather than
an imported Power BI dataset.

**Operational overview** — current status by bore and management area, with KPI
cards and a location map.

<img width="1274" height="655" alt="02_overview" src="https://github.com/user-attachments/assets/83662c44-b6af-4e48-944d-29a83e6e41f2" />


**Data model** — `monitoring_wells` and a `Date` dimension linked to the
water-level, rainfall and anomaly fact tables.

![Power BI data model](powerbi/screenshots/01_data_model.png)

**Groundwater trend and rainfall** — water level and its 7-day moving average,
shown with daily and 7-day cumulative rainfall. Higher mBGL values indicate a
deeper water table and are plotted lower on the chart.

![Groundwater trend and rainfall](powerbi/screenshots/03_groundwater_trend.png)

**Risk review** — rainfall versus water level and a per-bore table showing the
latest Normal, Watch or Critical classification.

![Advanced analytics](powerbi/screenshots/04_advanced_analytics.png)

---

## How it works

The following diagram shows the intended cloud workflow. Azure infrastructure,
Azure SQL loading and Power BI DirectQuery have been implemented. Publishing
the ADF artefacts, deploying cloud ML scoring and connecting the Logic App
notifications are planned extensions.

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
                       (DirectQuery to Azure SQL)              (workflow defined; connectors TBC)
```

| Layer | Technology | Repository status |
|-------|-----------|-------------------|
| Infrastructure | **Terraform** | Provisions the resource group, storage, Azure SQL, Data Factory, Function App and Key Vault |
| Ingestion | **Python** (requests · pandas · SQLAlchemy) | Source clients and idempotent SQL loaders implemented; live API validation pending |
| Orchestration | **Azure Data Factory** | 05:00 UTC trigger and deployable artefacts included; Azure Batch and staging configuration are required |
| Storage | **Azure SQL Database** | Schema, reporting views, licence allocation, extraction and risk snapshot tables implemented |
| Analytics | **Azure Functions · scikit-learn · pandas** | 06:00 UTC rule-based Function, offline ML experiments and Phase 2 decision-support scoring implemented |
| Secrets | **Azure Key Vault** | SQL connection string accessed by the Function through managed identity |
| Alerting | **Azure Logic Apps** | Workflow definition included; email and Teams connector configuration pending |
| Visualisation | **Power BI** (DirectQuery) | Four-page report connected to the demonstration database |
| CI/CD | **GitHub Actions** | Python linting, unit tests, Terraform formatting and validation |

---

## Analytics

### Rule-based detection

The rule-based layer provides a recorded threshold and plain-language reason
for each event. The thresholds support this demonstration and would need to be
calibrated against operational requirements before production use. Methodology:
[`docs/anomaly_methodology.md`](docs/anomaly_methodology.md).

| Detector | Signal | Method |
|----------|--------|--------|
| **Rapid Level Change** | Sudden water-level movement | Z-score against a trailing 7-day baseline (±2σ → WARNING, ±3σ → CRITICAL) |
| **Low Recharge Response** | Limited rebound after rain | After a 7-day rainfall total above 20 mm, check for a rise of at least 0.10 m within 14 days |
| **Salinity Intrusion Risk** | Sustained increase in TDS | OLS trend over 30 days (slope above 1 mg/L/day, R² ≥ 0.5); coastal bores are classified as CRITICAL |

### Forecasting and machine learning

The ML modules are evaluated offline on the committed demonstration data.
Implementation details: [`ml/README.md`](ml/README.md).

**Groundwater-level forecast.** A gradient-boosted regressor predicts the
future water level from lagged levels, trailing rainfall, salinity and seasonal
features. Training and testing are separated chronologically, with a purge gap
between them. Results are compared with a persistence baseline.

| Horizon | Model MAE | Persistence MAE | Improvement |
|--------:|----------:|----------------:|------------:|
| 7 days  | 0.138 m   | 0.124 m         | −11.5 %     |
| 30 days | 0.203 m   | 0.208 m         | **+2.2 %**  |
| 60 days | 0.234 m   | 0.304 m         | **+22.8 %** |

At seven days, persistence performs better. Performance is similar at 30 days,
while the model performs better at the longer test horizon on this generated
dataset.

![Actual vs predicted water level](ml/artifacts/forecast_example.png)

**Unsupervised anomaly detection.** An Isolation Forest scores level, salinity,
rates of change and rainfall features using a 1% contamination setting. It does
not use manually defined hydrogeological thresholds. The injected scenarios
provide a repeatable check of the scoring pipeline rather than evidence of
performance on operational data.

---

## Phase 2 — Regional water-security decision support

*Added 12 July 2026.*

Phase 2 adds a bore-level risk snapshot designed for regional surveillance,
licence compliance review and management reporting. It combines:

- the latest groundwater level and a 60-day trend projection;
- an 80% projection interval to show forecast uncertainty;
- annual licence allocation, extraction to date and projected year-end use;
- the latest rule-based anomaly and 30-day data completeness;
- a 0–100 risk score, Normal / Watch / Critical status, recorded risk drivers
  and a recommended action.

Rainfall is now mapped to the relevant groundwater management area rather than
averaged across all three stations. The committed data generator uses a fixed
reference date, so the dataset and reported results can be reproduced.

The implementation writes
[`sample_data/water_security_risk.csv`](sample_data/water_security_risk.csv)
and loads it into `monitoring.water_security_risk_snapshots`. Power BI can query
the latest result through `monitoring.vw_water_security_risk`, while
`monitoring.vw_licence_compliance` provides the allocation summary. Reusable
measures are supplied in
[`powerbi/phase2_water_security_measures.dax`](powerbi/phase2_water_security_measures.dax).
The licence and extraction values are demonstration scenarios, not regulatory
records.

```bash
python scripts/generate_sample_data.py
python scripts/run_detectors_offline.py
python analytics/water_security_risk.py
```

---

## Azure implementation

Terraform was used to provision the Azure resource group, Azure SQL Database,
Data Factory, Function App, storage account and Key Vault. The demonstration
dataset was loaded into Azure SQL and queried by Power BI through DirectQuery.
The images below are from the implemented Azure environment.

![Azure resource group](docs/screenshots/azure_resource_group.png)
![Azure SQL query editor](docs/screenshots/azure_sql_query.png)

---

## SCADA HMI demonstration

The repository also includes a local web-based HMI that places ML outputs and
rule-based alerts beside process tags. It is intended to demonstrate how the
batch analytics could be presented in an operational interface.

The OPC-UA server and client use an OPC-UA subscription with data-change
notifications. The process values are simulated from the demonstration data;
no field sensors or production control system are connected.

```
OPC-UA server (PLC/RTU stand-in) ──opc.tcp──► client subscription ──► FastAPI HMI ──WebSocket──► browser
 6 bores × {level, TDS, pump, valve}          data-change cache          ML and alert context
```

- **Monitor** — the server exposes one object per bore and the client subscribes
  to level, TDS, pump and valve tags.
- **Analyse** — the HMI shows the 30-day forecast and Isolation Forest score
  beside the current OPC-UA values.
- **Control pathway** — pump and valve nodes are writable in the simulator. No
  operator command workflow has been implemented.

The full design and production considerations are documented in
[`scada_hmi/SCADA_HMI_REPORT.md`](scada_hmi/SCADA_HMI_REPORT.md).

The generated ML CSVs remain outside version control. Create them locally
before starting the HMI:

```bash
pip install -r requirements.txt
pip install -r scada_hmi/requirements.txt
python ml/forecast_train.py
python ml/anomaly_unsupervised.py
python scada_hmi/opcua_server.py      # terminal 1
python scada_hmi/hmi_server.py        # terminal 2; open http://localhost:8080
```

---

<details>
<summary><b>Run the analytics offline</b></summary>

```bash
pip install -r requirements.txt
python scripts/generate_sample_data.py      # generate the demonstration dataset
python scripts/run_detectors_offline.py     # rule-based detectors
python ml/forecast_train.py                 # train and evaluate the level forecast
python ml/anomaly_unsupervised.py           # multivariate anomaly scoring
python analytics/water_security_risk.py     # risk and licence-compliance snapshot
pytest tests/ -v                            # 18 tests
```

For the cloud setup, see [`infra/README.md`](infra/README.md) and the
[deployment section](docs/PROJECT_REPORT.md#azure-deployment). The estimated
cost of the demonstrated configuration is approximately AUD 5–10 per month.
</details>

<details>
<summary><b>Data sources and provenance</b></summary>

| Designed source | Data | Licence |
|-----------------|------|---------|
| State groundwater portal (WaterConnect) | Drillhole water levels (mBGL) and salinity (TDS) | Creative Commons Attribution |
| National climate service (SILO) | Daily rainfall by station | Open / Creative Commons |
| Surface-water portal | River and reservoir levels | Open |

The ingestion layer is designed around these public sources. The current
dashboard and Azure database use a schema-compatible generated dataset from
[`scripts/generate_sample_data.py`](scripts/generate_sample_data.py). Its bore
identifiers and readings are representative demonstration values, not observed
measurements. Three scenarios are added to exercise the detector logic: a rapid
level change, limited recharge response and a coastal salinity trend.
Phase 2 also includes representative licence allocation and metered-extraction
scenarios for decision-support testing.

Live use requires validation of endpoint response fields, drillhole and station
identifiers, attribution requirements and reconciliation against the source
systems. Further detail is available in
[`docs/PROJECT_REPORT.md`](docs/PROJECT_REPORT.md#data-provenance).
</details>

<details>
<summary><b>Repository structure</b></summary>

```
azure-water-quality-pipeline/
├── infra/            # Terraform infrastructure
├── ingestion/        # Public-source clients and Azure SQL loaders
├── functions/        # Azure Function for rule-based anomaly detection
├── ml/               # Forecasting and Isolation Forest experiments
├── analytics/        # Water-security risk and licence-compliance scoring
├── adf/              # Data Factory linked services, pipeline and trigger definitions
├── logic_apps/       # Critical-event alert workflow definition
├── sql/              # Schema, views, audit table and stored procedures
├── powerbi/          # Dashboard screenshots and Phase 2 DAX measures
├── scada_hmi/        # OPC-UA server/client and FastAPI HMI demonstration
├── scripts/          # Sample-data generation and deployment helpers
├── tests/            # Detector, ML, risk and HMI tests
├── docs/             # Project report and anomaly methodology
└── .github/          # CI and manual deployment workflows
```
</details>

---

[Project report](docs/PROJECT_REPORT.md) ·
[Detection methodology](docs/anomaly_methodology.md) ·
[Machine learning notes](ml/README.md)
