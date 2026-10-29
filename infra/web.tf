# ---------- Cổng web: S3 private sau CloudFront ----------

locals {
  web_bucket = "web-${local.suffix}"
  # Trình duyệt chỉ được tải file lên, xem log từ đúng địa chỉ web (và máy dev)
  web_origins = concat(["https://${aws_cloudfront_distribution.web.domain_name}"], var.web_dev_origins)
}

resource "aws_s3_bucket" "web" {
  bucket        = local.web_bucket
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# CloudFront đọc bucket bằng Origin Access Control, bucket không mở công khai
resource "aws_cloudfront_origin_access_control" "web" {
  name                              = local.web_bucket
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  default_root_object = "index.html"
  # Gồm các điểm ở châu Á, rẻ hơn PriceClass_All
  price_class = "PriceClass_200"
  comment     = "SpotPool web"

  origin {
    origin_id                = "web"
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id       = "web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true
  }

  # Ứng dụng React một trang: đường dẫn không có file thì trả index.html để router phía trình duyệt xử lý
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }

  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "web_bucket" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket     = aws_s3_bucket.web.id
  policy     = data.aws_iam_policy_document.web_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.web]
}

output "web_url" {
  value = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "web_bucket_name" {
  value = aws_s3_bucket.web.bucket
}

output "web_distribution_id" {
  value = aws_cloudfront_distribution.web.id
}
