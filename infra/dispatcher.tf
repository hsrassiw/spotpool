# Bật sau khi TV3 tạo bucket input và bảng Jobs/Tasks.
variable "enable_dispatcher" {
  type    = bool
  default = false
}

resource "aws_lambda_function" "dispatcher" {
  count            = var.enable_dispatcher ? 1 : 0
  function_name    = "${var.name_prefix}-dispatcher"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.13"
  handler          = "app.lambda_handler"
  filename         = "${path.module}/build/dispatcher.zip"
  source_code_hash = filebase64sha256("${path.module}/build/dispatcher.zip")
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      INPUT_BUCKET     = local.input_bucket
      OUTPUT_BUCKET    = local.output_bucket
      JOBS_TABLE       = "Jobs"
      TASKS_TABLE      = "Tasks"
      SQS_ONDEMAND_URL = aws_sqs_queue.ondemand.url
      SQS_SPOT_URL     = aws_sqs_queue.spot.url
    }
  }
}

resource "aws_lambda_permission" "input_invokes_dispatcher" {
  count          = var.enable_dispatcher ? 1 : 0
  statement_id   = "AllowInputBucketInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.dispatcher[0].function_name
  principal      = "s3.amazonaws.com"
  source_arn     = "arn:aws:s3:::${local.input_bucket}"
  source_account = local.account_id
}

# Bucket input chỉ dùng một tài nguyên notification.
resource "aws_s3_bucket_notification" "input" {
  count  = var.enable_dispatcher ? 1 : 0
  bucket = local.input_bucket
  lambda_function {
    lambda_function_arn = aws_lambda_function.dispatcher[0].arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "submissions/"
    filter_suffix       = "/manifest.json"
  }
  depends_on = [aws_lambda_permission.input_invokes_dispatcher]
}

resource "aws_lambda_function" "dlq" {
  count            = var.enable_dispatcher ? 1 : 0
  function_name    = "${var.name_prefix}-dlq"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.13"
  handler          = "dlq.lambda_handler"
  filename         = "${path.module}/build/dlq.zip"
  source_code_hash = filebase64sha256("${path.module}/build/dlq.zip")
  timeout          = 60
  memory_size      = 256
  environment {
    variables = {
      JOBS_TABLE  = "Jobs"
      TASKS_TABLE = "Tasks"
    }
  }
}

resource "aws_lambda_event_source_mapping" "dlq" {
  count                   = var.enable_dispatcher ? 1 : 0
  event_source_arn        = aws_sqs_queue.dlq.arn
  function_name           = aws_lambda_function.dlq[0].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
}
