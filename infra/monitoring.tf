# Hạng mục 16. Free plan cho 10 metric alarm: ở đây 4, from_zero trong compute.tf 4.

locals {
  api_name = aws_api_gateway_rest_api.spotpool_api.name
  lambda_names = concat(
    [for fn in [
      aws_lambda_function.authorizer, aws_lambda_function.create_user, aws_lambda_function.get_current_user,
      aws_lambda_function.rotate_key, aws_lambda_function.submit_job, aws_lambda_function.start_job,
      aws_lambda_function.list_jobs, aws_lambda_function.get_job_status, aws_lambda_function.cancel_job,
      aws_lambda_function.billing,
    ] : fn.function_name],
    var.enable_dispatcher ? [aws_lambda_function.dispatcher[0].function_name, aws_lambda_function.dlq[0].function_name] : [],
  )
  alarm_actions = var.alert_email == "" ? [] : [aws_sns_topic.alerts.arn]
}

# ---------- Kênh cảnh báo ----------

resource "aws_sns_topic" "alerts" {
  name = "${var.name_prefix}-alerts"
}

# Phải bấm link xác nhận AWS gửi về email
resource "aws_sns_topic_subscription" "alert_email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---------- Cảnh báo ----------

# Task vào DLQ là đã hỏng hẳn sau 10 lần nhận, cần xem ngay
resource "aws_cloudwatch_metric_alarm" "dlq_messages" {
  alarm_name          = "${var.name_prefix}-dlq-messages"
  alarm_description   = "Co task bi chuyen vao sqs-dlq"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# Billing lỗi thì job không được quyết toán: credit bị giữ, runningJobs không giảm
resource "aws_cloudwatch_metric_alarm" "billing_errors" {
  alarm_name          = "${var.name_prefix}-billing-errors"
  alarm_description   = "Lambda billing loi, job khong duoc quyet toan"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.billing.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# Dispatcher lỗi thì job đã giữ credit nhưng không có task nào được tạo
resource "aws_cloudwatch_metric_alarm" "dispatcher_errors" {
  count               = var.enable_dispatcher ? 1 : 0
  alarm_name          = "${var.name_prefix}-dispatcher-errors"
  alarm_description   = "Lambda dispatcher loi, job ket o QUEUED"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.dispatcher[0].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# Lambda API ném lỗi hoặc timeout đều ra 5XX, một alarm thay cho từng Lambda
resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.name_prefix}-api-5xx"
  alarm_description   = "REST API tra loi 5XX"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5XXError"
  dimensions          = { ApiName = local.api_name, Stage = aws_api_gateway_stage.api_stage.stage_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# ---------- Budget ----------

# Tính trước khi trừ credit, không thì luôn 0 USD đến khi hết credit
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$spotpool"]
  }

  cost_types {
    include_credit = false
    include_refund = false
  }

  dynamic "notification" {
    for_each = var.alert_email == "" ? [] : [
      { type = "ACTUAL", threshold = 50 },
      { type = "ACTUAL", threshold = 100 },
      { type = "FORECASTED", threshold = 100 },
    ]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.alert_email]
    }
  }
}

# ---------- Dashboard ----------

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = var.name_prefix
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "text", x = 0, y = 0, width = 24, height = 2
        properties = {
          markdown = "## SpotPool\nSố máy chỉ có số liệu khi bật worker."
        }
      },
      {
        type = "metric", x = 0, y = 2, width = 12, height = 6
        properties = {
          title  = "Task trong hàng đợi (chờ + đang chạy)"
          region = var.region
          stat   = "Maximum"
          period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.spot.name, { label = "Spot: chờ" }],
            [".", "ApproximateNumberOfMessagesNotVisible", ".", ".", { label = "Spot: đang chạy" }],
            [".", "ApproximateNumberOfMessagesVisible", ".", aws_sqs_queue.ondemand.name, { label = "On-Demand: chờ" }],
            [".", "ApproximateNumberOfMessagesNotVisible", ".", ".", { label = "On-Demand: đang chạy" }],
            [".", "ApproximateNumberOfMessagesVisible", ".", aws_sqs_queue.dlq.name, { label = "DLQ", color = "#d62728" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 2, width = 12, height = 6
        properties = {
          title  = "Số máy đang chạy"
          region = var.region
          stat   = "Maximum"
          period = 60
          metrics = [
            ["AWS/AutoScaling", "GroupInServiceInstances", "AutoScalingGroupName", local.worker_asg_names["spot"], { label = "Spot" }],
            ["...", local.worker_asg_names["ondemand"], { label = "On-Demand" }],
            [".", "GroupDesiredCapacity", ".", local.worker_asg_names["spot"], { label = "Spot: mong muốn", visible = false }],
            ["...", local.worker_asg_names["ondemand"], { label = "On-Demand: mong muốn", visible = false }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 8, width = 12, height = 6
        properties = {
          title       = "Backlog mỗi máy (mục tiêu 2)"
          region      = var.region
          period      = 60
          annotations = { horizontal = [{ value = 2, label = "Mục tiêu" }] }
          metrics = flatten([for tier in ["spot", "ondemand"] : [
            [{ expression = "(${tier}_v + ${tier}_i) / ${tier}_n", label = tier == "spot" ? "Spot" : "On-Demand", id = "${tier}_b" }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", local.worker_queue_names[tier], { id = "${tier}_v", stat = "Average", visible = false }],
            [".", "ApproximateNumberOfMessagesNotVisible", ".", ".", { id = "${tier}_i", stat = "Average", visible = false }],
            ["AWS/AutoScaling", "GroupInServiceInstances", "AutoScalingGroupName", local.worker_asg_names[tier], { id = "${tier}_n", stat = "Average", visible = false }],
          ]])
        }
      },
      {
        type = "metric", x = 12, y = 8, width = 12, height = 6
        properties = {
          title  = "REST API"
          region = var.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiName", local.api_name, "Stage", aws_api_gateway_stage.api_stage.stage_name, { label = "Request" }],
            [".", "4XXError", ".", ".", ".", ".", { label = "4XX" }],
            [".", "5XXError", ".", ".", ".", ".", { label = "5XX", color = "#d62728" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 14, width = 12, height = 6
        properties = {
          title   = "Lỗi Lambda"
          region  = var.region
          stat    = "Sum"
          period  = 300
          metrics = [for name in local.lambda_names : ["AWS/Lambda", "Errors", "FunctionName", name]]
        }
      },
      {
        type = "metric", x = 12, y = 14, width = 12, height = 6
        properties = {
          title   = "Số lần gọi Lambda"
          region  = var.region
          stat    = "Sum"
          period  = 300
          metrics = [for name in local.lambda_names : ["AWS/Lambda", "Invocations", "FunctionName", name]]
        }
      },
    ]
  })
}

output "dashboard_url" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.main.dashboard_name}"
}
