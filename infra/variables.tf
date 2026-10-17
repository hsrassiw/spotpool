variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "name_prefix" {
  type    = string
  default = "spotpool"
}

variable "suffix" {
  description = "Hậu tố cho tên S3 bucket. Để trống thì dùng account id."
  type        = string
  default     = null
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}
