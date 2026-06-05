# Outputs used by downstream steps (SQL deployment, Power BI connection,
# function configuration) and surfaced by the CI/CD workflow.

output "resource_group_name" {
  description = "Name of the resource group containing the whole stack."
  value       = azurerm_resource_group.main.name
}

output "sql_server_fqdn" {
  description = "Fully qualified domain name of the Azure SQL server (Power BI data source)."
  value       = azurerm_mssql_server.main.fully_qualified_domain_name
}

output "sql_database_name" {
  description = "Name of the analytical database."
  value       = azurerm_mssql_database.main.name
}

output "data_factory_name" {
  description = "Name of the Data Factory instance."
  value       = azurerm_data_factory.main.name
}

output "function_app_name" {
  description = "Name of the Function App that runs anomaly detection."
  value       = azurerm_linux_function_app.anomaly.name
}

output "storage_account_name" {
  description = "Raw landing-zone storage account."
  value       = azurerm_storage_account.landing.name
}

output "key_vault_name" {
  description = "Key Vault holding the SQL connection string."
  value       = azurerm_key_vault.main.name
}
