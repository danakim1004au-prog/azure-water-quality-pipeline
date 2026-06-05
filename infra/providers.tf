# Terraform and provider configuration.
# This project uses Terraform for infrastructure-as-code to demonstrate a
# tool-set complementary to Bicep: the same Azure resources expressed in a
# cloud-agnostic, state-driven workflow.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.110"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # For a portfolio project the state lives locally. In a team setting this
  # block would point at a remote backend (Azure Storage) so state is shared
  # and locked. Left commented to keep `terraform init` friction-free.
  #
  # backend "azurerm" {
  #   resource_group_name  = "rg-tfstate"
  #   storage_account_name = "sttfstatewaterqlty"
  #   container_name       = "tfstate"
  #   key                  = "water-quality-pipeline.tfstate"
  # }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = true
    }
  }
}
