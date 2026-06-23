# Azure Data Factory artifacts

JSON definitions for the ETL orchestration, expressed in the ARM resource shape
the Data Factory REST API / Git integration uses.

| File | What it is |
|------|------------|
| `linkedservices/ls_keyvault.json` | Key Vault link used to resolve secrets at runtime |
| `linkedservices/ls_azuresql.json` | Azure SQL connection (string pulled from Key Vault) |
| `linkedservices/ls_batch.json` | Azure Batch pool that runs the `RunIngestion` Custom activity |
| `linkedservices/ls_staging.json` | Blob Storage that stages the Custom-activity resource files |
| `pipelines/pl_water_ingestion.json` | Run ingestion → audit on failure |
| `triggers/trg_daily_0500.json` | Daily 05:00 **UTC** schedule trigger |

## Importing

The cleanest path is to enable **Git integration** on the Data Factory and
commit these files under the configured collaboration branch — ADF Studio then
shows them as authored resources. Alternatively, recreate each object in ADF
Studio using these files as the source of truth, or deploy them via the
`Microsoft.DataFactory/factories/*` ARM resource types.

Linked services are parameterised (`vaultName`, `batchAccountName`, `region`,
`poolName`) so the same definitions work across environments; supply the values
from the Terraform outputs when wiring them up.

## What you must supply before it runs

The artifacts are structurally complete, but two external resources are **not**
provisioned by the Terraform in `infra/` and must be supplied:

- **An Azure Batch account + pool** for the `RunIngestion` Custom activity
  (referenced via `ls_batch`, staging through `ls_staging`). If you would rather
  not run Batch, swap `RunIngestion` for a **Container Apps Job** or a **Web
  Activity** that calls a containerised ingestion endpoint.
- **The Key Vault secrets** the linked services resolve: `sql-connection-string`,
  `batch-account-key`, `storage-connection-string`.

**Anomaly detection is not invoked from this pipeline.** The Function is
**timer-triggered** (daily **06:00 UTC**), so it has no HTTP endpoint for ADF to
call; it runs one hour after the **05:00 UTC** ingestion trigger. (UTC
throughout: Linux Consumption Functions schedule in UTC and ignore
`WEBSITE_TIME_ZONE`.) An explicit ADF→Function hand-off would need an
HTTP-triggered Function.

## Flow

```
trg_daily_0500 (05:00 UTC daily)
        │
        ▼
pl_water_ingestion
   ├─ RunIngestion ......... python -m ingestion.run_ingestion  (on Azure Batch via ls_batch)
   └─ LogIngestionFailure .. (on failure) writes monitoring.pipeline_audit

Function App (separate, daily 06:00 UTC timer) ── rule-based detection → monitoring.anomaly_events
```
