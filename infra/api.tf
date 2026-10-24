
resource "aws_apigatewayv2_api" "spotpool_api" {
  name          = "SpotPool-API"
  protocol_type = "HTTP"

  cors_configuration {
    allow_headers = [
      "authorization",
      "content-type",
      "x-amz-date",
      "x-api-key"
    ]

    allow_methods = [
      "GET",
      "POST",
      "PUT",
      "DELETE",
      "OPTIONS"
    ]

    allow_origins = ["*"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_stage" "default_stage" {
  api_id      = aws_apigatewayv2_api.spotpool_api.id
  name        = "$default"
  auto_deploy = true
}

# Đóng gói thư mục services/api/ thành file zip để deploy Lambda
data "archive_file" "api_lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../services/api"
  output_path = "${path.module}/files/api_lambda.zip"
}

# 1. Lambda: submit_job
resource "aws_lambda_function" "submit_job" {
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-submit-job"
  role             = aws_iam_role.lambda.arn
  handler          = "submit_job.lambda_handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256

  environment {
    variables = {
      JOBS_TABLE      = aws_dynamodb_table.jobs.name
      TASKS_TABLE     = aws_dynamodb_table.tasks.name
      INPUT_BUCKET    = aws_s3_bucket.input_bucket.id
      PRICE_ONDEMAND  = "6000"
      PRICE_SPOT      = "3000"
    }
  }
}

# 2. Lambda: start_job
resource "aws_lambda_function" "start_job" {
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-start-job"
  role             = aws_iam_role.lambda.arn
  handler          = "start_job.lambda_handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256

  environment {
    variables = {
      JOBS_TABLE      = aws_dynamodb_table.jobs.name
      TASKS_TABLE     = aws_dynamodb_table.tasks.name
      USERS_TABLE     = aws_dynamodb_table.users.name
      INPUT_BUCKET    = aws_s3_bucket.input_bucket.id
      OUTPUT_BUCKET   = aws_s3_bucket.output_bucket.id
      PRICE_ONDEMAND  = "6000"
      PRICE_SPOT      = "3000"
    }
  }
}

# 3. Lambda: get_job_status
resource "aws_lambda_function" "get_job_status" {
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-get-job-status"
  role             = aws_iam_role.lambda.arn
  handler          = "get_job_status.lambda_handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256

  environment {
    variables = {
      JOBS_TABLE    = aws_dynamodb_table.jobs.name
      TASKS_TABLE   = aws_dynamodb_table.tasks.name
      OUTPUT_BUCKET = aws_s3_bucket.output_bucket.id
    }
  }
}