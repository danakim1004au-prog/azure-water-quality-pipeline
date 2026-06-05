# Azure Data Factory artifacts

JSON definitions for the ETL orchestration, expressed in the ARM resource shape
the Data Factory REST API / Git integration uses.

| File | What it is |
|------|------------|
| `linkedservices/ls_keyvault.json` | Key Vault link used to resolve secrets at runtime |
| `linkedservices/ls_azuresql.json` | Azure SQL connection (string pulled from Key Vault) |
| `linkedservices/ls_function.json` | Connection to the anomaly-detection Function App |
| `pipelines/pl_water_ingestion.json` | Run ingestion → trigger detection → audit on failure |
| `triggers/trg_daily_0500.json` | Daily 05:00 schedule trigger |

## Importing

The cleanest path is to enable **Git integration** on the Data Factory and
commit these files under the configured collaboration branch — ADF Studio then
shows them as authored resources. Alternatively, recreate each object in ADF
Studio using these files as the source of truth, or deploy them via the
`Microsoft.DataFactory/factories/*` ARM resource types.

Linked services are parameterised (`vaultName`, `functionAppName`) so the same
definitions work across environments; supply the values from the Terraform
outputs when wiring them up.

## Flow

```
trg_daily_0500 (05:00 daily)
        │
        ▼
pl_water_ingestion
   ├─ RunIngestion ............. python -m ingestion.run_ingestion
   ├─ TriggerAnomalyDetection .. (on success) calls the Function
   └─ LogIngestionFailure ...... (on failure) writes monitoring.pipeline_audit
```
