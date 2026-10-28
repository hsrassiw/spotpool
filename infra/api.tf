# --- 1. API GATEWAY KIỂU REST ---
resource "aws_api_gateway_rest_api" "spotpool_api" {
  name        = "${var.name_prefix}-SpotPool-API"
  description = "SpotPool REST API Gateway"
}

# Lambda Authorizer kiểu TOKEN
resource "aws_api_gateway_authorizer" "lambda_auth" {
  name                             = "${var.name_prefix}-lambda-authorizer"
  rest_api_id                      = aws_api_gateway_rest_api.spotpool_api.id
  authorizer_uri                   = aws_lambda_function.authorizer.invoke_arn
  authorizer_result_ttl_in_seconds = 300
  type                             = "TOKEN"
}

# --- 2. ĐÓNG GÓI LAMBDA SOURCE ---
data "archive_file" "api_lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../services/api"
  output_path = "${path.module}/build/api_lambda.zip"
}

# --- 3. KHAI BÁO CÁC HÀM LAMBDA ---

resource "aws_lambda_function" "authorizer" {
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-authorizer"
  role             = aws_iam_role.lambda.arn
  handler          = "authorizer.lambda_handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256
  timeout          = 10
  memory_size      = 256

  environment {
    variables = {
      USERS_TABLE = aws_dynamodb_table.users.name
    }
  }
}

resource "aws_lambda_function" "create_user" {
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-create-user"
  role             = aws_iam_role.lambda.arn
  handler          = "create_user.lambda_handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      USERS_TABLE = aws_dynamodb_table.users.name
    }
  }
}

resource "aws_lambda_function" "rotate_key" {
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-rotate-key"
  role             = aws_iam_role.lambda.arn
  handler          = "rotate_key.lambda_handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      USERS_TABLE = aws_dynamodb_table.users.name
    }
  }
}

resource "aws_lambda_function" "submit_job" {
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-submit-job"
  role             = aws_iam_role.lambda.arn
  handler          = "submit_job.lambda_handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      JOBS_TABLE     = aws_dynamodb_table.jobs.name
      TASKS_TABLE    = aws_dynamodb_table.tasks.name
      INPUT_BUCKET   = aws_s3_bucket.input_bucket.id
      PRICE_ONDEMAND = "6000"
      PRICE_SPOT     = "3000"
    }
  }
}

resource "aws_lambda_function" "start_job" {
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-start-job"
  role             = aws_iam_role.lambda.arn
  handler          = "start_job.lambda_handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      JOBS_TABLE     = aws_dynamodb_table.jobs.name
      TASKS_TABLE    = aws_dynamodb_table.tasks.name
      USERS_TABLE    = aws_dynamodb_table.users.name
      INPUT_BUCKET   = aws_s3_bucket.input_bucket.id
      OUTPUT_BUCKET  = aws_s3_bucket.output_bucket.id
      PRICE_ONDEMAND = "6000"
      PRICE_SPOT     = "3000"
    }
  }
}

resource "aws_lambda_function" "get_job_status" {
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-get-job-status"
  role             = aws_iam_role.lambda.arn
  handler          = "get_job_status.lambda_handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      JOBS_TABLE    = aws_dynamodb_table.jobs.name
      TASKS_TABLE   = aws_dynamodb_table.tasks.name
      OUTPUT_BUCKET = aws_s3_bucket.output_bucket.id
    }
  }
}

resource "aws_lambda_function" "cancel_job" {
  filename         = data.archive_file.api_lambda_zip.output_path
  function_name    = "${var.name_prefix}-cancel-job"
  role             = aws_iam_role.lambda.arn
  handler          = "cancel_job.lambda_handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_lambda_zip.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      JOBS_TABLE  = aws_dynamodb_table.jobs.name
      TASKS_TABLE = aws_dynamodb_table.tasks.name
    }
  }
}


# --- 4. MODULE CẤU HÌNH CORS CHO API GATEWAY REST ---

module "cors_users" {
  source          = "squat/cors/aws"
  version         = "0.4.0"
  api_id          = aws_api_gateway_rest_api.spotpool_api.id
  resource_id     = aws_api_gateway_resource.users.id
  allow_headers   = ["*"]
  allow_methods   = ["POST", "OPTIONS"]
  allow_origin    = "*"
}

module "cors_users_rotate" {
  source          = "squat/cors/aws"
  version         = "0.4.0"
  api_id          = aws_api_gateway_rest_api.spotpool_api.id
  resource_id     = aws_api_gateway_resource.users_rotate_key.id
  allow_headers   = ["*"]
  allow_methods   = ["POST", "OPTIONS"]
  allow_origin    = "*"
}

module "cors_jobs" {
  source          = "squat/cors/aws"
  version         = "0.4.0"
  api_id          = aws_api_gateway_rest_api.spotpool_api.id
  resource_id     = aws_api_gateway_resource.jobs.id
  allow_headers   = ["*"]
  allow_methods   = ["POST", "OPTIONS"]
  allow_origin    = "*"
}

module "cors_job_item" {
  source          = "squat/cors/aws"
  version         = "0.4.0"
  api_id          = aws_api_gateway_rest_api.spotpool_api.id
  resource_id     = aws_api_gateway_resource.job_item.id
  allow_headers   = ["*"]
  allow_methods   = ["GET", "OPTIONS"]
  allow_origin    = "*"
}

module "cors_job_start" {
  source          = "squat/cors/aws"
  version         = "0.4.0"
  api_id          = aws_api_gateway_rest_api.spotpool_api.id
  resource_id     = aws_api_gateway_resource.job_start.id
  allow_headers   = ["*"]
  allow_methods   = ["POST", "OPTIONS"]
  allow_origin    = "*"
}

module "cors_job_cancel" {
  source          = "squat/cors/aws"
  version         = "0.4.0"
  api_id          = aws_api_gateway_rest_api.spotpool_api.id
  resource_id     = aws_api_gateway_resource.job_cancel.id
  allow_headers   = ["*"]
  allow_methods   = ["POST", "OPTIONS"]
  allow_origin    = "*"
}


# --- 5. CẤU TRÚC ROUTING TRÊN REST API GATEWAY ---

# /users
resource "aws_api_gateway_resource" "users" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_rest_api.spotpool_api.root_resource_id
  path_part   = "users"
}

resource "aws_api_gateway_method" "post_users" {
  rest_api_id   = aws_api_gateway_rest_api.spotpool_api.id
  resource_id   = aws_api_gateway_resource.users.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "integration_post_users" {
  rest_api_id             = aws_api_gateway_rest_api.spotpool_api.id
  resource_id             = aws_api_gateway_resource.users.id
  http_method             = aws_api_gateway_method.post_users.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.create_user.invoke_arn
}

# /users/me
resource "aws_api_gateway_resource" "users_me" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_resource.users.id
  path_part   = "me"
}

# /users/me/rotate-key
resource "aws_api_gateway_resource" "users_rotate_key" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_resource.users_me.id
  path_part   = "rotate-key"
}

resource "aws_api_gateway_method" "post_rotate_key" {
  rest_api_id   = aws_api_gateway_rest_api.spotpool_api.id
  resource_id   = aws_api_gateway_resource.users_rotate_key.id
  http_method   = "POST"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.lambda_auth.id
}

resource "aws_api_gateway_integration" "integration_rotate_key" {
  rest_api_id             = aws_api_gateway_rest_api.spotpool_api.id
  resource_id             = aws_api_gateway_resource.users_rotate_key.id
  http_method             = aws_api_gateway_method.post_rotate_key.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.rotate_key.invoke_arn
}

# /jobs
resource "aws_api_gateway_resource" "jobs" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_rest_api.spotpool_api.root_resource_id
  path_part   = "jobs"
}

resource "aws_api_gateway_method" "post_jobs" {
  rest_api_id   = aws_api_gateway_rest_api.spotpool_api.id
  resource_id   = aws_api_gateway_resource.jobs.id
  http_method   = "POST"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.lambda_auth.id
}

resource "aws_api_gateway_integration" "integration_post_jobs" {
  rest_api_id             = aws_api_gateway_rest_api.spotpool_api.id
  resource_id             = aws_api_gateway_resource.jobs.id
  http_method             = aws_api_gateway_method.post_jobs.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.submit_job.invoke_arn
}

# /jobs/{id}
resource "aws_api_gateway_resource" "job_item" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_resource.jobs.id
  path_part   = "{id}"
}

resource "aws_api_gateway_method" "get_job_status" {
  rest_api_id   = aws_api_gateway_rest_api.spotpool_api.id
  resource_id   = aws_api_gateway_resource.job_item.id
  http_method   = "GET"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.lambda_auth.id
}

resource "aws_api_gateway_integration" "integration_get_job_status" {
  rest_api_id             = aws_api_gateway_rest_api.spotpool_api.id
  resource_id             = aws_api_gateway_resource.job_item.id
  http_method             = aws_api_gateway_method.get_job_status.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.get_job_status.invoke_arn
}

# /jobs/{id}/start
resource "aws_api_gateway_resource" "job_start" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_resource.job_item.id
  path_part   = "start"
}

resource "aws_api_gateway_method" "post_job_start" {
  rest_api_id   = aws_api_gateway_rest_api.spotpool_api.id
  resource_id   = aws_api_gateway_resource.job_start.id
  http_method   = "POST"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.lambda_auth.id
}

resource "aws_api_gateway_integration" "integration_post_job_start" {
  rest_api_id             = aws_api_gateway_rest_api.spotpool_api.id
  resource_id             = aws_api_gateway_resource.job_start.id
  http_method             = aws_api_gateway_method.post_job_start.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.start_job.invoke_arn
}

# /jobs/{id}/cancel
resource "aws_api_gateway_resource" "job_cancel" {
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id
  parent_id   = aws_api_gateway_resource.job_item.id
  path_part   = "cancel"
}

resource "aws_api_gateway_method" "post_job_cancel" {
  rest_api_id   = aws_api_gateway_rest_api.spotpool_api.id
  resource_id   = aws_api_gateway_resource.job_cancel.id
  http_method   = "POST"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.lambda_auth.id
}

resource "aws_api_gateway_integration" "integration_post_job_cancel" {
  rest_api_id             = aws_api_gateway_rest_api.spotpool_api.id
  resource_id             = aws_api_gateway_resource.job_cancel.id
  http_method             = aws_api_gateway_method.post_job_cancel.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.cancel_job.invoke_arn
}


# --- 6. DEPLOYMENT & PERMISSIONS (VỚI TRIGGERS TỰ ĐỘNG DEPLOY) ---

resource "aws_api_gateway_deployment" "api_deployment" {
  depends_on = [
    aws_api_gateway_integration.integration_post_users,
    aws_api_gateway_integration.integration_rotate_key,
    aws_api_gateway_integration.integration_post_jobs,
    aws_api_gateway_integration.integration_get_job_status,
    aws_api_gateway_integration.integration_post_job_start,
    aws_api_gateway_integration.integration_post_job_cancel
  ]
  rest_api_id = aws_api_gateway_rest_api.spotpool_api.id

  # Triggers băm nội dung file source để tự động deploy lại khi có thay đổi
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.users.id,
      aws_api_gateway_method.post_users.id,
      aws_api_gateway_integration.integration_post_users.id,
      aws_api_gateway_resource.users_rotate_key.id,
      aws_api_gateway_method.post_rotate_key.id,
      aws_api_gateway_integration.integration_rotate_key.id,
      aws_api_gateway_resource.jobs.id,
      aws_api_gateway_method.post_jobs.id,
      aws_api_gateway_integration.integration_post_jobs.id,
      aws_api_gateway_resource.job_item.id,
      aws_api_gateway_method.get_job_status.id,
      aws_api_gateway_integration.integration_get_job_status.id,
      aws_api_gateway_resource.job_start.id,
      aws_api_gateway_method.post_job_start.id,
      aws_api_gateway_integration.integration_post_job_start.id,
      aws_api_gateway_resource.job_cancel.id,
      aws_api_gateway_method.post_job_cancel.id,
      aws_api_gateway_integration.integration_post_job_cancel.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "api_stage" {
  deployment_id = aws_api_gateway_deployment.api_deployment.id
  rest_api_id   = aws_api_gateway_rest_api.spotpool_api.id
  stage_name    = "prod"
}

locals {
  lambda_functions = {
    create_user    = aws_lambda_function.create_user.arn
    rotate_key     = aws_lambda_function.rotate_key.arn
    submit_job     = aws_lambda_function.submit_job.arn
    start_job      = aws_lambda_function.start_job.arn
    get_job_status = aws_lambda_function.get_job_status.arn
    cancel_job     = aws_lambda_function.cancel_job.arn
    authorizer     = aws_lambda_function.authorizer.arn
  }
}

resource "aws_lambda_permission" "apigw_lambda" {
  for_each      = local.lambda_functions
  statement_id  = "AllowExecutionFromAPIGateway-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = each.value
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.spotpool_api.execution_arn}/*/*"
}

output "rest_api_url" {
  value = "${aws_api_gateway_stage.api_stage.invoke_url}"
}