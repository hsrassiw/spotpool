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

variable "spot_interrupt_test" {
  description = "Máy Spot chạy IMDS giả để giả lập cảnh báo thu hồi (HM17, thay FIS). Chỉ bật hôm test."
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

variable "web_dev_origins" {
  description = "Địa chỉ chạy web trên máy khi dev, được phép tải file lên và đọc log như địa chỉ CloudFront."
  type        = list(string)
  default     = ["http://localhost:5173"]
}

variable "alert_email" {
  description = "Email nhận cảnh báo, đặt trong terraform.tfvars. Để trống thì không gửi."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Budget mỗi tháng (USD, chưa trừ credit)."
  type        = number
  default     = 10
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

variable "ondemand_warm_pool" {
  description = "Gói Ổn định giữ 1 máy đã cài sẵn ở trạng thái tắt (warm pool) thay cho máy chạy sẵn trong giờ hoạt động; dispatcher bật máy ngay khi có job lúc nhóm 0 máy. Lần đầu bật: apply với false trước để tạo lifecycle hook."
  type        = bool
  default     = false
}

variable "api_rate_limit" {
  description = "Số request/giây trung bình tối đa cho mỗi method của stage prod. Giữ dưới hạn mức 10 Lambda chạy cùng lúc của tài khoản."
  type        = number
  default     = 10
}

variable "api_burst_limit" {
  description = "Số request dồn cùng lúc tối đa cho mỗi method của stage prod."
  type        = number
  default     = 10
}
