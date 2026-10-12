# SpotPool

Nền tảng cho thuê tài nguyên tính toán theo job, đa người dùng, tính phí theo mức sử dụng trên AWS.

| | Thành viên | MSSV |
|---|---|---|
| TV1 | Phạm Minh Nghĩa (nhóm trưởng) | 24021584 |
| TV2 | Nguyễn Minh Khuê | 24021363 |
| TV3 | Đoàn Ngọc Anh | 24021536 |

Thiết kế chi tiết: [docs/Project Progress 1.pdf](docs/Project%20Progress%201.pdf).

## Cấu trúc thư mục

| Thư mục | Nội dung | Hạng mục | Phụ trách |
|---|---|---|---|
| `infra/` | Terraform: VPC 3 AZ, IAM, S3, DynamoDB, SQS, ECR, AMI, ASG, co giãn, CloudWatch, FIS | 2, 9, 10, 11, 16, 17 | TV1 |
| `services/api/` | API Gateway + Lambda: authorizer, đăng ký, submit-job, status, cancel, topup, billing | 3, 4, 5, 14 | TV3 |
| `services/dispatcher/` | Lambda dispatcher: tách job video, đẩy vào SQS theo gói | 6 | TV2 |
| `worker/` | Worker agent trên EC2: long polling, giành job, heartbeat, chạy container, xử lý Spot bị thu hồi | 7, 12 | TV2 |
| `job-templates/` | Image mẫu: nén video bằng FFmpeg, chạy script Python | 8 | TV3 |
| `web/` | Cổng web người dùng và trang quản trị (React, host trên S3) | 13, 15 | TV3, TV2 |
| `experiments/` | Thí nghiệm chi phí, đo thời gian chờ, điểm hòa vốn | 19 | TV1 |
| `docs/` | Báo cáo, slide, sơ đồ | 20 | Cả nhóm |

## Mốc kiểm soát

- **M1 – cuối tuần 2:** luồng chính chạy trọn vẹn từ nộp job đến tải kết quả.
- **M2 – cuối tuần 3:** tự co giãn, 2 gói và xử lý Spot bị thu hồi hoạt động.
- **M3 – cuối tuần 4:** đủ tính năng, dừng thêm tính năng mới.
- **M4 – tuần 5:** nộp báo cáo và demo.
