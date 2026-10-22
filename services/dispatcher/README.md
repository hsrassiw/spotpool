# services/dispatcher

TV2 triển khai hai Lambda:

- `app.py`: nhận S3 event ở `submissions/*/manifest.json`, kiểm tra manifest và job, tạo một dòng `Tasks` cho mỗi file, rồi gửi message vào `sqs-ondemand` hoặc `sqs-spot`.
- `dlq.py`: đánh dấu task `FAILED` khi message đã hết 10 lần nhận và task không còn worker giữ hợp lệ.

Dispatcher chịu được S3 event trùng: `PutItem` chỉ tạo task mới, bản ghi đã có phải có cùng fingerprint. Nó gửi lại message sau mỗi event; worker giành task bằng điều kiện nên message trùng không chạy trùng. Nếu Lambda lỗi giữa ghi task và gửi SQS, retry sẽ gửi lại.

Đóng gói Lambda từ gốc repo:

```bash
python3 services/dispatcher/build.py
```

File zip ở `infra/build/` được bỏ qua bởi Git. Khi TV3 đã tạo bucket input và bảng `Jobs`, `Tasks`, bật `enable_dispatcher=true` trong Terraform. `infra/dispatcher.tf` là nơi duy nhất khai báo S3 notification cho bucket input.
