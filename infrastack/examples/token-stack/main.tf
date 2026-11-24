# A module whose variable token is sensitive: the contract that funcdctl infra push makes leaves it out, so no run's
# input holds it, and OpenTofu reads it from TF_VAR_token, a key of a Secret the stack's Job binds (funcd ADR-0355).
terraform {
  required_providers {
    random = {
      source  = "hashicorp/random"
      version = "3.6.3"
    }
  }
}

variable "prefix" {
  type = string
}

variable "token" {
  type      = string
  sensitive = true
}

resource "random_pet" "name" {
  prefix = var.prefix
  keepers = {
    token = sha256(var.token)
  }
}

output "name" {
  value = random_pet.name.id
}
