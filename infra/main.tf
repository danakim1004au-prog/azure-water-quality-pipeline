# Core infrastructure for the regional water-quality monitoring platform.
#
# Resources provisioned:
#   - Resource Group              : logical container for the whole stack
#   - Storage Account + container : landing zone for raw ingested files
#   - Azure SQL (logical server + Basic database) : curated analytical store
#   - Data Factory                : orchestrates the ETL pipeline
#   - Function App (consumption)  : runs the anomaly-detection logic
#   - Key Vault                   : holds the SQL connection string + secrets
#
# Everything is sized for the free / lowest-cost tier so the running cost is
# roughly AUD 5-10 per month.

locals {
  # Resource name prefix, e.g. "waterqlty-dev"
  prefix = "${var.project_name}-${var.environment}"

  # Storage accounts and Key Vaults need globally-unique, alphanumeric names.
  suffix = random_string.suffix.result
}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

data "azurerm_client_config" "current" {}

# ---------------------------------------------------------------------------
# Resource Group
# ---------------------------------------------------------------------------
resource "azurerm_resource_group" "main" {
  name     = "rg-${local.prefix}"
  location = var.location
  tags     = var.tags
}

# ---------------------------------------------------------------------------
# Storage Account - raw landing zone for ingested CSV/JSON files
# ---------------------------------------------------------------------------
resource "azurerm_storage_account" "landing" {
  name                            = "st${var.project_name}${local.suffix}"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = var.tags
}

resource "azurerm_storage_container" "raw" {
  name                  = "raw"
  storage_account_name  = azurerm_storage_account.landing.name
  container_access_type = "private"
}

# ---------------------------------------------------------------------------
# Azure SQL - curated analytical store consumed by Power BI
# ---------------------------------------------------------------------------
resource "azurerm_mssql_server" "main" {
  name                         = "sql-${local.prefix}-${local.suffix}"
  resource_group_name          = azurerm_resource_group.main.name
  location                     = azurerm_resource_group.main.location
  version                      = "12.0"
  administrator_login          = var.sql_admin_login
  administrator_login_password = var.sql_admin_password
  minimum_tls_version          = "1.2"
  tags                         = var.tags
}

resource "azurerm_mssql_database" "main" {
  name      = "sqldb-water-monitoring"
  server_id = azurerm_mssql_server.main.id
  sku_name  = var.sql_database_sku
  collation = "SQL_Latin1_General_CP1_CI_AS"
  tags      = var.tags
}

# Allow other Azure services (Data Factory, Functions) to reach the server.
resource "azurerm_mssql_firewall_rule" "azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_mssql_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# Allow a single developer / Power BI client IP through the firewall.
resource "azurerm_mssql_firewall_rule" "client" {
  name             = "AllowClient"
  server_id        = azurerm_mssql_server.main.id
  start_ip_address = var.allowed_client_ip
  end_ip_address   = var.allowed_client_ip
}

# ---------------------------------------------------------------------------
# Data Factory - ETL orchestration
# ---------------------------------------------------------------------------
resource "azurerm_data_factory" "main" {
  name                = "adf-${local.prefix}-${local.suffix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags

  identity {
    type = "SystemAssigned"
  }
}

# ---------------------------------------------------------------------------
# Function App (Linux consumption plan) - anomaly detection
# ---------------------------------------------------------------------------
resource "azurerm_service_plan" "functions" {
  name                = "asp-${local.prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "Y1" # consumption plan - pay per execution
  tags                = var.tags
}

resource "azurerm_linux_function_app" "anomaly" {
  name                       = "func-${local.prefix}-${local.suffix}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  service_plan_id            = azurerm_service_plan.functions.id
  storage_account_name       = azurerm_storage_account.landing.name
  storage_account_access_key = azurerm_storage_account.landing.primary_access_key
  tags                       = var.tags

  identity {
    type = "SystemAssigned"
  }

  site_config {
    application_stack {
      python_version = "3.11"
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME" = "python"
    # The function reads the SQL connection string from Key Vault at runtime
    # using its managed identity, so no secret is stored here.
    "SQL_SECRET_URI" = azurerm_key_vault_secret.sql_connection.id
  }
}

# ---------------------------------------------------------------------------
# Key Vault - secret store for the SQL connection string
# ---------------------------------------------------------------------------
resource "azurerm_key_vault" "main" {
  name                       = "kv-${var.project_name}-${local.suffix}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 7
  tags                       = var.tags
}

# Grant the deploying principal permission to write secrets.
resource "azurerm_key_vault_access_policy" "deployer" {
  key_vault_id = azurerm_key_vault.main.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = data.azurerm_client_config.current.object_id

  secret_permissions = ["Get", "List", "Set", "Delete", "Purge"]
}

# Grant the Function App's managed identity read access to secrets.
resource "azurerm_key_vault_access_policy" "function" {
  key_vault_id = azurerm_key_vault.main.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_linux_function_app.anomaly.identity[0].principal_id

  secret_permissions = ["Get", "List"]
}

resource "azurerm_key_vault_secret" "sql_connection" {
  name  = "sql-connection-string"
  value = "Driver={ODBC Driver 18 for SQL Server};Server=tcp:${azurerm_mssql_server.main.fully_qualified_domain_name},1433;Database=${azurerm_mssql_database.main.name};Uid=${var.sql_admin_login};Pwd=${var.sql_admin_password};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"

  key_vault_id = azurerm_key_vault.main.id

  depends_on = [azurerm_key_vault_access_policy.deployer]
}
