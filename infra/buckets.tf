resource "aws_s3_bucket" "input_bucket" {
  bucket        = "spotpool-input-bucket-ngocanh"
  force_destroy = true
}

resource "aws_s3_bucket_cors_configuration" "input_cors" {
  bucket = aws_s3_bucket.input_bucket.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "PUT", "POST", "DELETE"]
    allowed_origins = ["*"]
    expose_headers  = ["ETag"]
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "input_lifecycle" {
  bucket = aws_s3_bucket.input_bucket.id

  rule {
    id     = "expire_after_7_days"
    status = "Enabled"

    expiration {
      days = 7
    }
  }
}

resource "aws_s3_bucket" "output_bucket" {
  bucket        = "spotpool-output-bucket-ngocanh"
  force_destroy = true
}

resource "aws_s3_bucket_cors_configuration" "output_cors" {
  bucket = aws_s3_bucket.output_bucket.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "PUT", "POST"]
    allowed_origins = ["*"]
    expose_headers  = ["ETag"]
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "output_lifecycle" {
  bucket = aws_s3_bucket.output_bucket.id

  rule {
    id     = "expire_after_7_days"
    status = "Enabled"

    expiration {
      days = 7
    }
  }
}