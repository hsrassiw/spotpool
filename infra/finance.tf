# HM14/HM15 dùng chung package API hiện tại và REST authorizer.
locals {
  finance_functions = {
    credits        = { handler = "finance.credits_handler", timeout = 30 }
    topup          = { handler = "finance.topup_handler", timeout = 60 }
    invoices       = { handler = "finance.invoices_handler", timeout = 30 }
    admin_overview = { handler = "admin_overview.lambda_handler", timeout = 60 }
  }
  finance_routes = {
    credits        = { resource = aws_api_gateway_resource.credits.id, method = "POST", function = "credits" }
    topup          = { resource = aws_api_gateway_resource.job_topup.id, method = "POST", function = "topup" }
    invoices       = { resource = aws_api_gateway_resource.invoices.id, method = "GET", function = "invoices" }
    admin_overview = { resource = aws_api_gateway_resource.admin_overview.id, method = "GET", function = "admin_overview" }
  }
}

resource "aws_lambda_function" "finance" {
  for_each         = local.finance_functions
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-${replace(each.key, "_", "-")}"
  role             = each.key == "admin_overview" ? aws_iam_role.admin_overview.arn : aws_iam_role.lambda.arn
  handler          = each.value.handler
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256
  timeout          = each.value.timeout
  memory_size      = 256
  environment {
    variables = {
      USERS_TABLE      = aws_dynamodb_table.users.name
      JOBS_TABLE       = aws_dynamodb_table.jobs.name
      TASKS_TABLE      = aws_dynamodb_table.tasks.name
      USAGE_TABLE      = aws_dynamodb_table.usage.name
      OUTPUT_BUCKET    = aws_s3_bucket.output_bucket.id
      SQS_ONDEMAND_URL = aws_sqs_queue.ondemand.url
      SQS_SPOT_URL     = aws_sqs_queue.spot.url
    }
  }
}

resource "aws_api_gateway_resource" "credits" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_rest_api.spotpool_api.root_resource_id
  path_part   = "credits"
}
resource "aws_api_gateway_resource" "invoices" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_rest_api.spotpool_api.root_resource_id
  path_part   = "billing"
}
resource "aws_api_gateway_resource" "job_topup" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_resource.job_item.id
  path_part   = "topup"
}
resource "aws_api_gateway_resource" "admin" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_rest_api.spotpool_api.root_resource_id
  path_part   = "admin"
}
resource "aws_api_gateway_resource" "admin_overview" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "overview"
}
resource "aws_api_gateway_method" "finance" {
  for_each      = local.finance_routes
  rest_api_id   = aws_api_gateway_rest_api.spotpool_api.id
  resource_id   = each.value.resource
  http_method   = each.value.method
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.lambda_auth.id
}
resource "aws_api_gateway_integration" "finance" {
  for_each                = local.finance_routes
  rest_api_id             = aws_api_gateway_rest_api.spotpool_api.id
  resource_id             = each.value.resource
  http_method             = aws_api_gateway_method.finance[each.key].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.finance[each.value.function].invoke_arn
}
resource "aws_lambda_permission" "finance" {
  for_each      = local.finance_functions
  statement_id  = "AllowAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.finance[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.spotpool_api.execution_arn}/*/*"
}

# Chỉ Lambda quản trị viên được dùng Cost Explorer và xem thông tin đội máy.
resource "aws_iam_role" "admin_overview" {
  name               = "${var.name_prefix}-admin-overview"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}
resource "aws_iam_role_policy_attachment" "admin_logs" {
  role       = aws_iam_role.admin_overview.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy" "admin_overview" {
  role = aws_iam_role.admin_overview.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ec2:DescribeInstances", "ce:GetCostAndUsage"], Resource = "*" },
    { Effect = "Allow", Action = ["sqs:GetQueueAttributes"], Resource = [aws_sqs_queue.ondemand.arn, aws_sqs_queue.spot.arn] },
    { Effect = "Allow", Action = ["dynamodb:GetItem"], Resource = aws_dynamodb_table.users.arn },
    { Effect = "Allow", Action = ["dynamodb:Scan"], Resource = aws_dynamodb_table.jobs.arn },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.output_bucket.arn}/cache/aws-cost.json" },
    # Thiếu ListBucket thì S3 trả AccessDenied thay vì NoSuchKey khi cache chưa có, lần gọi đầu bị lỗi 500
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.output_bucket.arn },
  ] })
}
