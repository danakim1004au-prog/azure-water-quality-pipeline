# Infrastructure (Terraform)

Provisions the whole platform on Azure. Terraform is used here intentionally as
a counterpart to a Bicep-based deployment elsewhere — the same Azure resources
expressed in a state-driven, cloud-agnostic workflow.

## Resources created

| Resource                | Purpose                                   | Cost tier            |
|-------------------------|-------------------------------------------|----------------------|
| Resource Group          | Container for the stack                   | free                 |
| Storage Account + blob  | Raw landing zone for ingested files       | a few cents/month    |
| Azure SQL (server + DB) | Curated analytical store for Power BI      | Basic, ~AUD 5/month  |
| Data Factory            | ETL orchestration                         | ~free at this volume |
| Function App (Y1)       | Runs anomaly detection                    | free grant           |
| Key Vault               | Stores the SQL connection string          | negligible           |

Estimated total: **~AUD 5–10 / month**, so a two-week run costs only a few
dollars. Remember to `terraform destroy` when finished.

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.6
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli),
  logged in: `az login`

## Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars      # edit allowed_client_ip
export TF_VAR_sql_admin_password='<a-strong-password>'   # never commit this

terraform init
terraform plan
terraform apply
```

Key outputs (also consumed by the deploy workflow and the Power BI guide):

```bash
terraform output sql_server_fqdn      # Power BI data source
terraform output function_app_name    # Function deploy target
terraform output key_vault_name
```

## After apply

1. Run the SQL scripts in order against the new database:
   `sql/01_schema.sql`, `02_indexes_and_views.sql`, `03_audit_and_procs.sql`.
2. Deploy the Function (`functions/`) — manually or via the `Deploy` workflow.
3. Import the ADF artifacts in `adf/` (linked services → pipeline → trigger).
4. Point Power BI at `sql_server_fqdn` (see `powerbi/DASHBOARD_GUIDE.md`).

## Tear down

```bash
terraform destroy
```

## Notes

- State is local by default for low friction. The remote `azurerm` backend is
  scaffolded (commented) in `providers.tf` for a team setup.
- Secrets never live in the repo: the SQL password comes from an environment
  variable, and the connection string is stored in Key Vault and read by the
  Function's managed identity at runtime.
