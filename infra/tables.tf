
resource "aws_dynamodb_table" "users" {
  name         = "Users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "userId"

  attribute {
    name = "userId"
    type = "S"
  }

  attribute {
    name = "apiKeyHash"
    type = "S"
  }

  global_secondary_index {
    name            = "apiKeyHash-index"
    hash_key        = "apiKeyHash"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "jobs" {
  name             = "Jobs"
  billing_mode     = "PAY_PER_REQUEST"
  hash_key         = "jobId"
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  attribute {
    name = "jobId"
    type = "S"
  }

  attribute {
    name = "userId"
    type = "S"
  }

  attribute {
    name = "createdAt"
    type = "N"
  }

  global_secondary_index {
    name            = "userId-createdAt-index"
    hash_key        = "userId"
    range_key       = "createdAt"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "tasks" {
  name         = "Tasks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "jobId"
  range_key    = "taskId"

  attribute {
    name = "jobId"
    type = "S"
  }

  attribute {
    name = "taskId"
    type = "S"
  }
}

resource "aws_dynamodb_table" "usage" {
  name         = "Usage"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "userId"
  range_key    = "usageId"

  attribute {
    name = "userId"
    type = "S"
  }

  attribute {
    name = "usageId"
    type = "S"
  }
}