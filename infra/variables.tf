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

variable "enable_worker" {
  description = "Tạo ASG worker. Chỉ bật khi đã có bảng DynamoDB và image trên ECR."
  type        = bool
  default     = false
}

variable "worker_instance_type" {
  description = "2 vCPU, 4 GB: đủ 2 suất 1,5 GB. Gói Free của tài khoản cho phép loại này."
  type        = string
  default     = "c7i-flex.large"
}

variable "worker_queue" {
  description = "Hàng đợi worker nhận task: ondemand hoặc spot."
  type        = string
  default     = "spot"
}
