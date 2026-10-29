# infra

Terraform cho hạ tầng AWS (region `ap-southeast-1`).

## Quy ước

Tên tài nguyên theo Bảng 2.1 trong báo cáo:

- Hàng đợi SQS: `sqs-ondemand`, `sqs-spot`, `sqs-dlq`.
- Bảng DynamoDB: `Users`, `Jobs`, `Tasks`, `Usage`.
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

Trước khi apply, đóng gói Lambda dispatcher: `python3 services/dispatcher/build.py` (Lambda API tự đóng gói khi plan).

## Bật, tắt worker

Mỗi gói một ASG: `spotpool-worker-ondemand` (On-Demand `c7i-flex.large`) và `spotpool-worker-spot` (Spot `c7i-flex.large`, `m7i-flex.large`, 3 AZ). Máy chỉ nhận việc từ hàng đợi của gói mình (`SPOTPOOL_QUEUE`).

```bash
terraform apply -var enable_worker=true    # bật 2 ASG
terraform apply -var enable_worker=false   # tắt sau khi test cho đỡ tốn tiền
```

Co giãn (hạng mục 11):

- Mỗi ASG giữ (task chờ + task đang chạy) / số máy quanh 2, tức mỗi máy 2 suất. Tối đa 4 máy mỗi gói (`ondemand_max_size`, `spot_max_size`).
- Đang 0 máy mà hàng đợi có việc thì alarm `spotpool-worker-<gói>-from-zero` bật máy đầu tiên.
- Gói Tiết kiệm hết việc thì về 0 máy. Gói Ổn định giữ sẵn 1 máy từ 8h đến 23h giờ Việt Nam, ngoài giờ cũng về 0. Đổi giờ hôm demo: `terraform apply -var enable_worker=true -var ondemand_hours_end=23 -var ondemand_hours_start=6`.
- Hết việc thì giảm máy sau khoảng 15 phút (target tracking chờ đủ 15 điểm dữ liệu mới giảm).

`terraform destroy` trong `infra/` để dọn khi không dùng.
