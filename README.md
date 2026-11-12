# SpotPool

Nền tảng cho thuê tài nguyên tính toán theo job, đa người dùng, tính phí theo mức sử dụng trên AWS.

| | Thành viên | MSSV |
|---|---|---|
| TV1 | Phạm Minh Nghĩa (nhóm trưởng) | 24021584 |
| TV2 | Nguyễn Minh Khuê | 24021536 |
| TV3 | Đoàn Ngọc Anh | 24021363 |

Hợp đồng giữa các phần (API, bảng, hàng đợi, luồng trạng thái): [docs/contracts.md](docs/contracts.md). Báo cáo: [Báo cáo 1](docs/Project%20Progress%201.pdf), [Báo cáo 2](docs/Project%20Progress%202.pdf).

## Cấu trúc thư mục

| Thư mục | Nội dung | Hạng mục | Phụ trách |
|---|---|---|---|
| `infra/` | Terraform: VPC 3 AZ, IAM, S3, DynamoDB, SQS, ECR, REST API Gateway, 2 ASG worker (On-Demand, Spot), co giãn, CloudWatch, FIS | 2, 9, 10, 11, 16, 17 | TV1 |
| `services/api/` | Lambda sau REST API Gateway: authorizer, đăng ký, đổi khóa, tạo job, chạy job, trạng thái, hủy, nạp thêm, hóa đơn | 3, 4, 5, 14 | TV3 |
| `services/dispatcher/` | Lambda dispatcher: tách job thành task, đẩy vào SQS theo gói; Lambda DLQ | 6, 23 | TV2 |
| `worker/` | Worker agent trên EC2: long polling, giành task, heartbeat, chạy container, xử lý Spot bị thu hồi | 7, 12 | TV2 |
| `job-templates/` | Image mẫu: chạy script Python (TV2), nén video bằng FFmpeg (TV3) | 8 | TV2, TV3 |
| `web/` | Cổng web người dùng (TV2), trang quản trị và hóa đơn (TV3); React trên S3 + CloudFront | 13, 14, 15 | TV2, TV3 |
| `experiments/` | Thí nghiệm chi phí, đo thời gian chờ, điểm hòa vốn | 19 | TV1 |
| `docs/` | Báo cáo, slide, sơ đồ | 20 | Cả nhóm |

## Mốc kiểm soát

- **M1 – cuối tuần 2:** luồng chính chạy trọn vẹn từ nộp job đến tải kết quả. Đạt ngày 25/10/2026 (tag `m1`).
- **M2 – cuối tuần 3:** tự co giãn, 2 gói và xử lý Spot bị thu hồi hoạt động.
- **M3 – cuối tuần 4:** đủ tính năng, dừng thêm tính năng mới.
- **M4 – tuần 5:** nộp báo cáo và demo.
