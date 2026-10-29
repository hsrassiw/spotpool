# Tên tài nguyên theo Bảng 2.1 trong báo cáo
locals {
  queue_names = ["sqs-ondemand", "sqs-spot", "sqs-dlq"]
  table_names = ["Users", "Jobs", "Tasks", "Usage"]

  # Tên S3 bucket phải duy nhất toàn AWS nên thêm hậu tố
  input_bucket  = "input-${local.suffix}"
  output_bucket = "output-${local.suffix}"

  sqs_arns = [for q in local.queue_names : "arn:aws:sqs:${var.region}:${local.account_id}:${q}"]
  ddb_arns = flatten([for t in local.table_names : [
    "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${t}",
    "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${t}/index/*",
  ]])

  # Worker không đọc Users và không gửi thẳng vào DLQ
  worker_sqs_arns = [for q in ["sqs-ondemand", "sqs-spot"] : "arn:aws:sqs:${var.region}:${local.account_id}:${q}"]
  worker_ddb_arns = flatten([for t in ["Jobs", "Tasks", "Usage"] : [
    "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${t}",
    "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${t}/index/*",
  ]])
}

# ---------- Worker (EC2) ----------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "${var.name_prefix}-worker"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid = "Queues"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:SendMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
    ]
    resources = local.worker_sqs_arns
  }

  statement {
    sid       = "ReadInput"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.input_bucket}/*"]
  }

  statement {
    sid       = "WriteOutput"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["arn:aws:s3:::${local.output_bucket}/*"]
  }

  statement {
    sid = "Tables"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
    ]
    resources = local.worker_ddb_arns
  }

  # Máy đang chạy task tự bật bảo vệ để ASG không chọn tắt khi giảm máy
  statement {
    sid       = "ScaleInProtection"
    actions   = ["autoscaling:SetInstanceProtection"]
    resources = ["arn:aws:autoscaling:${var.region}:${local.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.name_prefix}-worker-*"]
  }

  statement {
    sid = "PullImages"
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData", "logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "${var.name_prefix}-worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# Worker không mở cổng SSH, vào máy để debug bằng Session Manager
resource "aws_iam_role_policy_attachment" "worker_ssm" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "worker" {
  name = "${var.name_prefix}-worker"
  role = aws_iam_role.worker.name
}

# ---------- Lambda ----------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.name_prefix}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "Queues"
    actions   = ["sqs:SendMessage", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
    resources = local.sqs_arns
  }

  # Lambda xử lý message rơi vào DLQ
  statement {
    sid       = "ReadDlq"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility"]
    resources = ["arn:aws:sqs:${var.region}:${local.account_id}:sqs-dlq"]
  }

  # Lambda billing đọc Stream của bảng Jobs
  statement {
    sid = "JobsStream"
    actions = [
      "dynamodb:DescribeStream",
      "dynamodb:GetRecords",
      "dynamodb:GetShardIterator",
      "dynamodb:ListStreams",
    ]
    resources = ["arn:aws:dynamodb:${var.region}:${local.account_id}:table/Jobs/stream/*"]
  }

  statement {
    sid = "Tables"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:TransactWriteItems",
    ]
    resources = local.ddb_arns
  }

  statement {
    sid     = "Buckets"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${local.input_bucket}",
      "arn:aws:s3:::${local.input_bucket}/*",
      "arn:aws:s3:::${local.output_bucket}",
      "arn:aws:s3:::${local.output_bucket}/*",
    ]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${var.name_prefix}-lambda"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}
