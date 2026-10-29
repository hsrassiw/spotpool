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

variable "ondemand_instance_type" {
  description = "Máy gói Ổn định. 2 vCPU, 4 GB: đủ 2 suất 1,5 GB. Gói Free của tài khoản cho phép loại này."
  type        = string
  default     = "c7i-flex.large"
}

variable "spot_instance_types" {
  description = "Các loại máy Spot cho gói Tiết kiệm, đều 2 vCPU để mỗi máy chạy 2 suất."
  type        = list(string)
  default     = ["c7i-flex.large", "m7i-flex.large"]
}

# Quota của tài khoản: 16 vCPU On-Demand, 32 vCPU Spot. Mỗi máy 2 vCPU.
variable "ondemand_max_size" {
  type    = number
  default = 4
}

variable "spot_max_size" {
  type    = number
  default = 4
}

variable "ondemand_hours_start" {
  description = "Giờ bắt đầu giữ sẵn 1 máy On-Demand (giờ Việt Nam, 0–23)."
  type        = number
  default     = 8
}

variable "ondemand_hours_end" {
  description = "Giờ hết giữ sẵn máy On-Demand (giờ Việt Nam, 0–23), phải sau giờ bắt đầu."
  type        = number
  default     = 23

  validation {
    condition     = var.ondemand_hours_start >= 0 && var.ondemand_hours_start < var.ondemand_hours_end && var.ondemand_hours_end <= 23
    error_message = "Cần 0 <= ondemand_hours_start < ondemand_hours_end <= 23."
  }
}
