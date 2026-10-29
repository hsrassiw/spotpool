# web

Cổng web người dùng (TV2) và trang quản trị, trang hóa đơn (TV3), một ứng dụng React tĩnh đặt trên S3, phục vụ qua CloudFront.

## Deploy

Bucket, địa chỉ web và id CloudFront lấy từ `terraform output` trong `infra/` (`web_bucket_name`, `web_url`, `web_distribution_id`).

```bash
npm run build
aws s3 sync dist/ s3://<web_bucket_name> --delete
aws cloudfront create-invalidation --distribution-id <web_distribution_id> --paths "/*"
```

- Bucket private, chỉ CloudFront đọc được, nên mở web qua `web_url` chứ không qua địa chỉ S3.
- Đường dẫn không có file (ví dụ `/jobs/abc`) trả `index.html`, router phía trình duyệt xử lý.
- Link tải file lên và link xem log chỉ chạy từ `web_url` và `http://localhost:5173` (CORS của bucket input, output). Dev ở cổng khác thì thêm vào biến `web_dev_origins`.
