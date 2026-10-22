resource "aws_sqs_queue" "dlq" {
  name                       = "sqs-dlq"
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 360
}

resource "aws_sqs_queue" "ondemand" {
  name                       = "sqs-ondemand"
  receive_wait_time_seconds  = 20
  visibility_timeout_seconds = 120
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 10
  })
}

resource "aws_sqs_queue" "spot" {
  name                       = "sqs-spot"
  receive_wait_time_seconds  = 20
  visibility_timeout_seconds = 120
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 10
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url            = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ondemand.arn, aws_sqs_queue.spot.arn]
  })
}

output "sqs_ondemand_url" { value = aws_sqs_queue.ondemand.url }
output "sqs_spot_url" { value = aws_sqs_queue.spot.url }
output "sqs_dlq_url" { value = aws_sqs_queue.dlq.url }
