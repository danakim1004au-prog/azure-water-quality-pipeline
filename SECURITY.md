# Security policy

This is a portfolio project, not a production service, but it is built to
production-minded security habits.

## Reporting a vulnerability

If you spot a security issue, please open a private report via GitHub Security
Advisories, or email the maintainer rather than filing a public issue. Please
allow a reasonable window to respond before any public disclosure.

## Secret handling in this repository

- **No secrets are committed.** The Azure SQL admin password is supplied to
  Terraform via the `TF_VAR_sql_admin_password` environment variable, and the
  connection string is stored in **Azure Key Vault** and read at runtime via the
  Function's **managed identity**.
- `infra/terraform.tfvars` (which would hold the real password and the allowed
  client IP) is **git-ignored** and must never be committed. Use
  `infra/terraform.tfvars.example` as the template.
- Application data shown in the repo is a synthetic, schema-identical
  demonstration dataset — it contains no real personal or sensitive records.

## Known hardening follow-ups (roadmap)

These are deliberately scoped out of the portfolio build and tracked as future
work (see the project report, §11):

- Replace SQL admin authentication with **Microsoft Entra ID / managed-identity**
  authentication end to end.
- Narrow the Azure SQL firewall rule that allows Azure services
  (`AllowAllWindowsAzureIps` / `0.0.0.0`) to the specific resources that need it.
- Move all application secrets (SILO contact email, connector credentials) into
  Key Vault references rather than environment variables where applicable.
