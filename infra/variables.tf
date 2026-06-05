# Input variables for the water-quality monitoring platform.

variable "project_name" {
  description = "Short name used as a prefix for all resources."
  type        = string
  default     = "waterqlty"

  validation {
    condition     = can(regex("^[a-z0-9]{3,12}$", var.project_name))
    error_message = "project_name must be 3-12 lowercase alphanumeric characters."
  }
}

variable "environment" {
  description = "Deployment environment (dev, test, prod)."
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region. Australia East keeps data resident close to the source jurisdiction."
  type        = string
  default     = "australiaeast"
}

variable "sql_admin_login" {
  description = "Administrator login for the Azure SQL logical server."
  type        = string
  default     = "wqadmin"
}

variable "sql_admin_password" {
  description = "Administrator password for Azure SQL. Supply via TF_VAR_sql_admin_password, never commit it."
  type        = string
  sensitive   = true
}

variable "sql_database_sku" {
  description = "SKU for the Azure SQL database. Basic (5 DTU) keeps the monthly cost near AUD 5."
  type        = string
  default     = "Basic"
}

variable "allowed_client_ip" {
  description = "Public IP allowed through the SQL firewall for Power BI / local development. CIDR not required."
  type        = string
  default     = "0.0.0.0"
}

variable "tags" {
  description = "Tags applied to every resource for cost tracking and governance."
  type        = map(string)
  default = {
    project    = "water-quality-pipeline"
    managed_by = "terraform"
    domain     = "environmental-monitoring"
  }
}
