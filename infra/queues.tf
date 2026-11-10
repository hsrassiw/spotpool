resource "aws_sqs_queue" "dlq" {
  name                       = "sqs-dlq"
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 360
}

# Mặc định SQS chỉ giữ message 4 ngày: tắt worker lâu hơn thì task mất message, job kẹt QUEUED.
# Để 7 ngày, ngắn hơn DLQ (14 ngày) vì message chuyển sang DLQ vẫn giữ thời điểm vào hàng ban đầu.
resource "aws_sqs_queue" "ondemand" {
  name                       = "sqs-ondemand"
  message_retention_seconds  = 604800
  receive_wait_time_seconds  = 20
  visibility_timeout_seconds = 120
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 10
  })
}

resource "aws_sqs_queue" "spot" {
  name                       = "sqs-spot"
  message_retention_seconds  = 604800
  receive_wait_time_seconds  = 20
  visibility_timeout_seconds = 120
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 10
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ondemand.arn, aws_sqs_queue.spot.arn]
  })
}

output "sqs_ondemand_url" { value = aws_sqs_queue.ondemand.url }
output "sqs_spot_url" { value = aws_sqs_queue.spot.url }
output "sqs_dlq_url" { value = aws_sqs_queue.dlq.url }
