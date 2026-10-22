
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