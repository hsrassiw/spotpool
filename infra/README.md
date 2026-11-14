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

Code worker (`worker/`, `spotpool_core/`) được apply đẩy lên bucket `worker-<suffix>`, máy tải về khi khởi động. Sửa code rồi apply thì ASG tự thay máy mới.

```bash
terraform apply -var enable_worker=true    # bật 2 ASG
terraform apply -var enable_worker=false   # tắt sau khi test cho đỡ tốn tiền
```

Co giãn (hạng mục 11):

- Mỗi ASG giữ (task chờ + task đang chạy) / số máy quanh 2, tức mỗi máy 2 suất. Tối đa 4 máy mỗi gói (`ondemand_max_size`, `spot_max_size`).
- Đang 0 máy mà hàng đợi có việc thì alarm `spotpool-worker-<gói>-from-zero` bật máy đầu tiên.
- Gói Tiết kiệm hết việc thì về 0 máy. Gói Ổn định giữ sẵn 1 máy từ 8h đến 23h giờ Việt Nam, ngoài giờ cũng về 0. Đổi giờ hôm demo: `terraform apply -var enable_worker=true -var ondemand_hours_end=23 -var ondemand_hours_start=6`.
- Hết việc thì giảm máy sau khoảng 15 phút (target tracking chờ đủ 15 điểm dữ liệu mới giảm).

### Warm pool cho gói Ổn định (`ondemand_warm_pool`)

Thay máy chạy sẵn 8h–23h (khoảng 1,54 USD/ngày) bằng 1 máy đã cài sẵn ở trạng thái tắt, chỉ trả tiền ổ đĩa:

- Máy mới vào warm pool chạy user data như thường. Worker thấy trạng thái đích là `Warmed:Stopped` (IMDS `autoscaling/target-lifecycle-state`) thì không nhận task, chỉ báo lifecycle hook `worker-ready`; ASG tắt máy.
- Có job gói Ổn định lúc nhóm 0 máy thì Lambda dispatcher gọi `SetDesiredCapacity` = 1 ngay, không chờ alarm CloudWatch (chỉ số SQS trễ 2–3 phút). Máy trong warm pool bật lại trong khoảng 10 s (đo ngày 14/11, `experiments/2026-11-14/warm-pool.log`). Alarm `from-zero` vẫn giữ làm dự phòng.
- Hết việc, target tracking giảm về 0 và máy quay lại warm pool (`reuse_on_scale_in`). Lịch giờ hoạt động không còn dùng.

Hook phải có trước khi bật warm pool, nên lần đầu bật làm hai lần apply:

```bash
python3 services/dispatcher/build.py
terraform apply -var enable_worker=true                              # tạo hook, đẩy code worker mới; vẫn chạy như cũ
terraform apply -var enable_worker=true -var ondemand_warm_pool=true # bật warm pool
```

Các lần apply sau luôn thêm `-var ondemand_warm_pool=true`. Quay về máy chạy sẵn: apply không có biến này.

## Giả lập thu hồi Spot (hạng mục 17)

Tài khoản Free không dùng được AWS FIS nên thay bằng IMDS giả trên máy Spot (`worker/imds_mock.py`). IMDS giả chuyển tiếp mọi request tới IMDS thật, chỉ trả cảnh báo thu hồi khi có file `/run/spotpool-interrupt`.

```bash
terraform apply -var enable_worker=true -var spot_interrupt_test=true
# nộp job gói Tiết kiệm, đợi task RUNNING rồi chạy ở thư mục gốc repo:
bash experiments/spot_interrupt.sh
```

Script bật cảnh báo qua SSM, đợi 2 phút như AWS rồi tắt máy. Test xong apply lại không có `spot_interrupt_test`.

## Giám sát và chi phí

Hạng mục 16, trong `monitoring.tf`:

- Dashboard `spotpool` (output `dashboard_url`): hàng đợi, số máy, backlog mỗi máy, lỗi API và Lambda.
- Email cảnh báo khi có task vào DLQ, billing hoặc dispatcher lỗi, API trả 5XX.
- Budget `spotpool-monthly`: 10 USD mỗi tháng cho tài nguyên tag `Project=spotpool`, tính trước khi trừ credit.

Email nhận cảnh báo đặt trong `terraform.tfvars`: `alert_email = "ten@example.com"`. Apply lần đầu xong phải bấm link xác nhận AWS gửi về email.

`terraform destroy` trong `infra/` để dọn khi không dùng.
