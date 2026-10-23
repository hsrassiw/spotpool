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
  # Code worker nhét thẳng vào user data (đã nén), không cần nơi lưu riêng
  worker_user_data = templatefile("${path.module}/templates/worker-user-data.sh", {
    region           = var.region
    registry         = "${local.account_id}.dkr.ecr.${var.region}.amazonaws.com"
    sqs_ondemand_url = aws_sqs_queue.ondemand.url
    sqs_spot_url     = aws_sqs_queue.spot.url
    video_image      = "${aws_ecr_repository.job["spotpool-video"].repository_url}:latest"
    script_image     = "${aws_ecr_repository.job["spotpool-script"].repository_url}:latest"
    worker_queue     = var.worker_queue
    agent_py         = base64gzip(file("${path.module}/../worker/agent.py"))
    contracts_py     = base64gzip(file("${path.module}/../spotpool_core/contracts.py"))
  })
}

resource "aws_launch_template" "worker" {
  name                   = "${var.name_prefix}-worker"
  image_id               = data.aws_ssm_parameter.al2023.value
  instance_type          = var.worker_instance_type
  vpc_security_group_ids = [aws_security_group.worker.id]
  user_data              = base64encode(local.worker_user_data)

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
    tags          = { Name = "${var.name_prefix}-worker" }
  }
}

# Tạm cố định 1 máy. Bật khi đã có bảng và image trên ECR.
resource "aws_autoscaling_group" "worker" {
  count               = var.enable_worker ? 1 : 0
  name                = "${var.name_prefix}-worker"
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1
  vpc_zone_identifier = aws_subnet.public[*].id

  launch_template {
    id      = aws_launch_template.worker.id
    version = aws_launch_template.worker.latest_version
  }

  # Sửa code worker thì thay máy mới
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 0
    }
  }
}
