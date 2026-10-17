# infra

Terraform cho hạ tầng AWS (region `ap-southeast-1`).

## Quy ước

Tên tài nguyên theo Bảng 2.1 trong báo cáo:

- Hàng đợi SQS: `sqs-ondemand`, `sqs-spot`, `sqs-dlq`.
- Bảng DynamoDB: `Users`, `Jobs`, `Usage`.
- S3 bucket: `input-<suffix>`, `output-<suffix>`. Tên bucket phải duy nhất trên toàn AWS nên thêm hậu tố, mặc định là account id.
- Mọi tài nguyên có tag `Project=spotpool` (đã gắn sẵn bằng `default_tags`).

Các IAM role trong `iam.tf` chỉ cấp quyền cho đúng các tên trên, nên đặt tên khác thì worker và Lambda sẽ không truy cập được.

## Dựng lần đầu trên một tài khoản AWS

```bash
# 1. Tạo bucket lưu state (một lần)
cd infra/bootstrap
terraform init
terraform apply          # ghi lại tên bucket ở output tfstate_bucket

# 2. Dựng hạ tầng nền
cd ..
terraform init -backend-config="bucket=<tên bucket ở trên>" -backend-config="region=ap-southeast-1"
terraform plan
terraform apply
```

`terraform destroy` trong `infra/` để dọn khi không dùng.
