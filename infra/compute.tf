# ---------- ECR: image cho job ----------

resource "aws_ecr_repository" "job" {
  for_each             = toset(["spotpool-video", "spotpool-script"])
  name                 = each.key
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Chỉ giữ 5 image gần nhất cho đỡ tốn dung lượng
resource "aws_ecr_lifecycle_policy" "job" {
  for_each   = aws_ecr_repository.job
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Giu 5 image gan nhat"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# ---------- Worker EC2 ----------

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  # Mỗi gói một ASG, máy chỉ nhận việc từ hàng đợi của gói mình
  worker_tiers       = toset(["ondemand", "spot"])
  worker_asg_names   = { for tier in local.worker_tiers : tier => "${var.name_prefix}-worker-${tier}" }
  worker_queue_names = { ondemand = aws_sqs_queue.ondemand.name, spot = aws_sqs_queue.spot.name }
  scaling_tiers      = var.enable_worker ? local.worker_tiers : toset([])

  # Giờ Việt Nam lúc tạo ASG, để tạo ngoài giờ thì không bật sẵn máy On-Demand
  vn_hour           = tonumber(formatdate("h", timeadd(timestamp(), "7h")))
  ondemand_in_hours = local.vn_hour >= var.ondemand_hours_start && local.vn_hour < var.ondemand_hours_end

  # Code worker để trên S3, máy tải về khi khởi động. User data giới hạn 16 KB nên không nhét code vào được nữa.
  worker_code_bucket = "worker-${local.suffix}"
  worker_files = {
    "worker/agent.py"            = "${path.module}/../worker/agent.py"
    "worker/spot.py"             = "${path.module}/../worker/spot.py"
    "spotpool_core/__init__.py"  = "${path.module}/../spotpool_core/__init__.py"
    "spotpool_core/contracts.py" = "${path.module}/../spotpool_core/contracts.py"
  }
  # Sửa code thì đổi thư mục trên S3, user data đổi theo nên ASG thay máy mới
  worker_code_version = substr(md5(join(",", [for name, src in local.worker_files : "${name}:${filemd5(src)}"])), 0, 12)

  worker_user_data = { for tier in local.worker_tiers : tier => templatefile("${path.module}/templates/worker-user-data.sh", {
    region           = var.region
    registry         = "${local.account_id}.dkr.ecr.${var.region}.amazonaws.com"
    sqs_ondemand_url = aws_sqs_queue.ondemand.url
    sqs_spot_url     = aws_sqs_queue.spot.url
    video_image      = "${aws_ecr_repository.job["spotpool-video"].repository_url}:latest"
    script_image     = "${aws_ecr_repository.job["spotpool-script"].repository_url}:latest"
    worker_queue     = tier
    worker_asg       = local.worker_asg_names[tier]
    code_url         = "s3://${aws_s3_bucket.worker_code.id}/${local.worker_code_version}"
    worker_files     = keys(local.worker_files)
  }) }
}

resource "aws_s3_bucket" "worker_code" {
  bucket        = local.worker_code_bucket
  force_destroy = true
}

resource "aws_s3_object" "worker_code" {
  for_each = local.worker_files
  bucket   = aws_s3_bucket.worker_code.id
  key      = "${local.worker_code_version}/${each.key}"
  source   = each.value
  etag     = filemd5(each.value)

  # Máy cũ đang chạy vẫn có code, nhưng máy mới bật trong lúc apply cần bản mới có sẵn trước
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_launch_template" "worker" {
  for_each               = local.worker_tiers
  name                   = "${var.name_prefix}-worker-${each.key}"
  image_id               = data.aws_ssm_parameter.al2023.value
  instance_type          = var.ondemand_instance_type
  vpc_security_group_ids = [aws_security_group.worker.id]
  user_data              = base64encode(local.worker_user_data[each.key])

  iam_instance_profile {
    name = aws_iam_instance_profile.worker.name
  }

  # Bắt buộc IMDSv2, worker đọc cảnh báo thu hồi Spot qua đây
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  # default_tags chỉ gắn lên launch template, máy và ổ đĩa ASG bật lên phải ghi Project ở đây
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.name_prefix}-worker-${each.key}", Project = "spotpool", Tier = each.key }
  }

  tag_specifications {
    resource_type = "volume"
    tags          = { Project = "spotpool", Tier = each.key }
  }

  depends_on = [aws_s3_object.worker_code]
}

# Gói Ổn định: On-Demand, giữ sẵn 1 máy trong giờ hoạt động
resource "aws_autoscaling_group" "ondemand" {
  count                   = var.enable_worker ? 1 : 0
  name                    = local.worker_asg_names["ondemand"]
  min_size                = local.ondemand_in_hours ? 1 : 0
  max_size                = var.ondemand_max_size
  vpc_zone_identifier     = aws_subnet.public[*].id
  default_instance_warmup = 180
  enabled_metrics         = ["GroupInServiceInstances", "GroupDesiredCapacity"]

  launch_template {
    id      = aws_launch_template.worker["ondemand"].id
    version = aws_launch_template.worker["ondemand"].latest_version
  }

  # Sửa code worker thì thay máy mới
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 0
    }
  }

  # Sau khi tạo, min_size do lịch giờ hoạt động đặt
  lifecycle {
    ignore_changes = [min_size]
  }
}

# Gói Tiết kiệm: toàn bộ là Spot, chọn loại máy và AZ ít bị thu hồi mà rẻ. Hết việc thì về 0 máy.
resource "aws_autoscaling_group" "spot" {
  count                   = var.enable_worker ? 1 : 0
  name                    = local.worker_asg_names["spot"]
  min_size                = 0
  max_size                = var.spot_max_size
  vpc_zone_identifier     = aws_subnet.public[*].id
  default_instance_warmup = 180
  enabled_metrics         = ["GroupInServiceInstances", "GroupDesiredCapacity"]

  mixed_instances_policy {
    instances_distribution {
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = 0
      spot_allocation_strategy                 = "price-capacity-optimized"
    }

    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.worker["spot"].id
        version            = aws_launch_template.worker["spot"].latest_version
      }

      dynamic "override" {
        for_each = var.spot_instance_types
        content {
          instance_type = override.value
        }
      }
    }
  }

  # Sửa code worker thì thay máy mới
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 0
    }
  }
}

# ---------- Co giãn ----------

# Backlog-per-instance: (task chờ + task đang chạy) / số máy. Mỗi máy 2 suất nên giữ quanh 2.
# Task đang chạy vẫn nằm trong hàng đợi (worker gia hạn visibility), nên máy bận không bị tính là thừa.
resource "aws_autoscaling_policy" "backlog" {
  for_each               = local.scaling_tiers
  name                   = "backlog-per-instance"
  autoscaling_group_name = local.worker_asg_names[each.key]
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    target_value = 2

    customized_metric_specification {
      metrics {
        id          = "visible"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesVisible"
            dimensions {
              name  = "QueueName"
              value = local.worker_queue_names[each.key]
            }
          }
        }
      }

      metrics {
        id          = "inflight"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesNotVisible"
            dimensions {
              name  = "QueueName"
              value = local.worker_queue_names[each.key]
            }
          }
        }
      }

      metrics {
        id          = "inservice"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "AWS/AutoScaling"
            metric_name = "GroupInServiceInstances"
            dimensions {
              name  = "AutoScalingGroupName"
              value = local.worker_asg_names[each.key]
            }
          }
        }
      }

      # 0 máy thì phép chia không ra số, việc bật máy đầu tiên do alarm bên dưới lo
      metrics {
        id          = "backlog"
        expression  = "(visible + inflight) / inservice"
        label       = "Backlog per instance"
        return_data = true
      }
    }
  }

  depends_on = [aws_autoscaling_group.ondemand, aws_autoscaling_group.spot]
}

# Bật máy đầu tiên khi nhóm đang 0 máy mà hàng đợi có việc
resource "aws_autoscaling_policy" "from_zero" {
  for_each               = local.scaling_tiers
  name                   = "start-from-zero"
  autoscaling_group_name = local.worker_asg_names[each.key]
  policy_type            = "SimpleScaling"
  adjustment_type        = "ExactCapacity"
  scaling_adjustment     = 1
  cooldown               = 180

  depends_on = [aws_autoscaling_group.ondemand, aws_autoscaling_group.spot]
}

resource "aws_cloudwatch_metric_alarm" "from_zero" {
  for_each            = local.scaling_tiers
  alarm_name          = "${local.worker_asg_names[each.key]}-from-zero"
  alarm_description   = "Hang doi co viec ma nhom dang 0 may"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  # Chỉ số SQS lên CloudWatch trễ 2–3 phút, xét 1 phút gần nhất thì luôn thiếu dữ liệu
  evaluation_periods  = 3
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_autoscaling_policy.from_zero[each.key].arn]

  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      dimensions  = { QueueName = local.worker_queue_names[each.key] }
      period      = 60
      stat        = "Maximum"
    }
  }

  metric_query {
    id = "desired"
    metric {
      namespace   = "AWS/AutoScaling"
      metric_name = "GroupDesiredCapacity"
      dimensions  = { AutoScalingGroupName = local.worker_asg_names[each.key] }
      period      = 60
      stat        = "Maximum"
    }
  }

  # Chỉ khi đang 0 máy, để không kéo nhóm đang có nhiều máy về 1
  metric_query {
    id          = "start"
    expression  = "IF(desired == 0 AND visible > 0, 1, 0)"
    label       = "Start from zero"
    return_data = true
  }
}

# Giờ hoạt động gói Ổn định: trong giờ giữ ít nhất 1 máy chờ sẵn, ngoài giờ cho về 0.
# Chỉ đổi min_size (-1 là giữ nguyên): đóng giờ không tắt máy ngay, co giãn theo backlog giảm dần khi hết việc.
resource "aws_autoscaling_schedule" "ondemand_open" {
  count                  = var.enable_worker ? 1 : 0
  scheduled_action_name  = "open"
  autoscaling_group_name = aws_autoscaling_group.ondemand[0].name
  recurrence             = "0 ${var.ondemand_hours_start} * * *"
  time_zone              = "Asia/Ho_Chi_Minh"
  min_size               = 1
  max_size               = -1
  desired_capacity       = -1
}

resource "aws_autoscaling_schedule" "ondemand_close" {
  count                  = var.enable_worker ? 1 : 0
  scheduled_action_name  = "close"
  autoscaling_group_name = aws_autoscaling_group.ondemand[0].name
  recurrence             = "0 ${var.ondemand_hours_end} * * *"
  time_zone              = "Asia/Ho_Chi_Minh"
  min_size               = 0
  max_size               = -1
  desired_capacity       = -1
}
