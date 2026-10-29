
resource "aws_s3_bucket" "input_bucket" {
  bucket        = local.input_bucket
  force_destroy = true
}

resource "aws_s3_bucket_cors_configuration" "input_cors" {
  bucket = aws_s3_bucket.input_bucket.id

  # Web tải file lên bằng link PUT
  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT"]
    allowed_origins = local.web_origins
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
  bucket        = local.output_bucket
  force_destroy = true
}

resource "aws_s3_bucket_cors_configuration" "output_cors" {
  bucket = aws_s3_bucket.output_bucket.id

  # Web đọc log ngay trên trang bằng link GET
  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET"]
    allowed_origins = local.web_origins
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
