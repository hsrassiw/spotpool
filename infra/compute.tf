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
  worker_tiers = toset(["ondemand", "spot"])

  # Code worker nhét thẳng vào user data (đã nén), không cần nơi lưu riêng
  worker_user_data = { for tier in local.worker_tiers : tier => templatefile("${path.module}/templates/worker-user-data.sh", {
    region           = var.region
    registry         = "${local.account_id}.dkr.ecr.${var.region}.amazonaws.com"
    sqs_ondemand_url = aws_sqs_queue.ondemand.url
    sqs_spot_url     = aws_sqs_queue.spot.url
    video_image      = "${aws_ecr_repository.job["spotpool-video"].repository_url}:latest"
    script_image     = "${aws_ecr_repository.job["spotpool-script"].repository_url}:latest"
    worker_queue     = tier
    agent_py         = base64gzip(file("${path.module}/../worker/agent.py"))
    contracts_py     = base64gzip(file("${path.module}/../spotpool_core/contracts.py"))
  }) }
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

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.name_prefix}-worker-${each.key}", Tier = each.key }
  }

  tag_specifications {
    resource_type = "volume"
    tags          = { Tier = each.key }
  }
}

# Gói Ổn định: On-Demand. Tạm cố định 1 máy, co giãn ở hạng mục 11.
resource "aws_autoscaling_group" "ondemand" {
  count               = var.enable_worker ? 1 : 0
  name                = "${var.name_prefix}-worker-ondemand"
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1
  vpc_zone_identifier = aws_subnet.public[*].id

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
}

# Gói Tiết kiệm: toàn bộ là Spot, chọn loại máy và AZ ít bị thu hồi mà rẻ
resource "aws_autoscaling_group" "spot" {
  count               = var.enable_worker ? 1 : 0
  name                = "${var.name_prefix}-worker-spot"
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1
  vpc_zone_identifier = aws_subnet.public[*].id

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
