terraform {
  required_providers {
    random = {
      source  = "hashicorp/random"
      version = "3.6.3"
    }
    null = {
      source  = "hashicorp/null"
      version = "3.2.4"
    }
  }
}

variable "prefix" {
  type = string
}

resource "random_pet" "name" {
  prefix = var.prefix
}

resource "random_password" "token" {
  length  = 24
  special = false
}

resource "null_resource" "marker" {
  triggers = {
    name = random_pet.name.id
  }
}

output "name" {
  value = random_pet.name.id
}

output "token" {
  value     = random_password.token.result
  sensitive = true
}
