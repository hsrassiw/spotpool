terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Khai báo bucket khi init, xem infra/README.md
  backend "s3" {
    key          = "spotpool/base.tfstate"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "spotpool" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # Tên S3 bucket là duy nhất toàn AWS, mặc định dùng account id làm hậu tố
  suffix = coalesce(var.suffix, local.account_id)
  azs    = slice(data.aws_availability_zones.available.names, 0, 3)
}
