output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "worker_security_group_id" {
  value = aws_security_group.worker.id
}

output "worker_instance_profile" {
  value = aws_iam_instance_profile.worker.name
}

output "lambda_role_arn" {
  value = aws_iam_role.lambda.arn
}

output "input_bucket_name" {
  value = local.input_bucket
}

output "output_bucket_name" {
  value = local.output_bucket
}

output "ecr_repository_urls" {
  value = { for name, repo in aws_ecr_repository.job : name => repo.repository_url }
}
