# SpotPool

Nền tảng cho thuê tài nguyên tính toán theo job, đa người dùng, tính phí theo mức sử dụng trên AWS. Đồ án môn INT3319 2 Điện toán đám mây, nhóm G1.

| | Thành viên | MSSV |
| --- | --- | --- |
| TV1 | Phạm Minh Nghĩa (nhóm trưởng) | 24021584 |
| TV2 | Nguyễn Minh Khuê | 24021536 |
| TV3 | Đoàn Ngọc Anh | 24021363 |

## Bản nộp cuối kỳ (15/11/2026)

| Nội dung | Link |
| --- | --- |
| Ứng dụng web | <https://d31wnp5mdclq86.cloudfront.net> |
| REST API | <https://zgpqqlvblk.execute-api.ap-southeast-1.amazonaws.com/prod> |
| Video trình bày và demo | <https://youtu.be/CHEfoXKIVbY> |
| Báo cáo cuối kỳ | [docs/Final Report.pdf](docs/Final%20Report.pdf) |
| Slide | [docs/Final Slides.pdf](docs/Final%20Slides.pdf) |
| Báo cáo tiến độ | [Báo cáo 1](docs/Project%20Progress%201.pdf), [Báo cáo 2](docs/Project%20Progress%202.pdf) |
| Hợp đồng giao diện (API, bảng, hàng đợi, luồng trạng thái) | [docs/contracts.md](docs/contracts.md) |

Hệ thống được giữ chạy trên AWS (ap-southeast-1) ít nhất tới ngày 4/12/2026.

## Tóm tắt kiến trúc

- Cổng web React đặt trên S3, phân phối qua CloudFront. REST API Gateway xác thực khóa API bằng Lambda authorizer, các Lambda phía sau ghi DynamoDB (Users, Jobs, Tasks, Usage).
- Người dùng tải file lên S3 qua presigned URL. Lambda dispatcher tách job thành task (mỗi file một task) và đẩy vào hàng đợi SQS của gói đã chọn.
- Hai cụm EC2, mỗi máy 2 vCPU chạy worker agent và 2 suất Docker:
  - gói Ổn định: On-Demand c7i-flex.large, giữ 1 máy tắt sẵn trong warm pool;
  - gói Tiết kiệm: Spot c7i-flex, m7i-flex.

  Mỗi cụm tự co giãn từ 0 đến 4 máy theo (task chờ + task đang chạy) / số máy, mục tiêu 2.
- Khi máy Spot bị thu hồi, worker trả task về hàng đợi và tách máy khỏi ASG để ASG bật máy thay ngay. Job có checkpoint chạy tiếp từ lần lưu cuối.
- Lambda billing đọc DynamoDB Stream để quyết toán credit theo vCPU-giây. Đơn giá: 4000 credit/vCPU-giờ cho gói Ổn định, 2000 cho gói Tiết kiệm. Lượt bị thu hồi không tính tiền.

## Cấu trúc thư mục

| Thư mục | Nội dung | Hạng mục | Phụ trách |
| --- | --- | --- | --- |
| `infra/` | Terraform: VPC 3 AZ, IAM, S3, DynamoDB, SQS, ECR, REST API Gateway, S3 web và CloudFront, 2 ASG worker (On-Demand có warm pool, Spot), co giãn, CloudWatch, Budgets, IMDS giả thay AWS FIS | 1, 2, 9, 10, 11, 16, 17, 24 | TV1 |
| `services/api/` | Lambda sau REST API Gateway: authorizer, đăng ký, đổi khóa, tạo job, chạy job, trạng thái, danh sách job, hủy, nạp credit, quyết toán, trang quản trị | 3, 4, 5, 14 | TV3 |
| `services/dispatcher/` | Lambda dispatcher: tách job thành task, đẩy vào SQS theo gói, bật máy Ổn định từ warm pool; Lambda DLQ | 6, 23 | TV2 |
| `worker/` | Worker agent trên EC2: long polling, giành task, heartbeat, chạy container, xử lý Spot bị thu hồi, checkpoint, warm pool | 7, 12 | TV2 |
| `job-templates/` | Image mẫu: chạy script Python (TV2), nén video bằng FFmpeg (TV3); cả hai có checkpoint | 8 | TV2, TV3 |
| `web/` | Cổng web người dùng (TV2), trang quản trị và hóa đơn (TV3) | 13, 14, 15 | TV2, TV3 |
| `spotpool_core/` | Hằng số và hàm dùng chung theo hợp đồng giao diện | 22 | TV2 |
| `tests/` | Test tự động cho phần mã Python, dùng client giả, không gọi AWS | 18 | Cả nhóm |
| `experiments/` | Thí nghiệm thời gian chờ, co giãn, thu hồi Spot, chi phí; lần chạy lại ngày 14/11 trên bản nộp | 17, 19 | TV1 |
| `docs/` | Báo cáo, slide, hợp đồng giao diện | 20, 22 | Cả nhóm |

TV1 làm thêm các phần ngoài 24 hạng mục sau mốc M3: checkpoint cho job script và job video, siết giới hạn tài nguyên, thay máy Spot nhanh, job .zip và warm pool.

## Triển khai và kiểm thử

Các bước dựng hệ thống trên một tài khoản AWS nằm ở [infra/README.md](infra/README.md), gồm tạo bucket state, `terraform apply`, bật worker và warm pool. Cách build và đưa web lên S3 nằm ở [web/README.md](web/README.md).

```bash
# Test Python (chạy ở thư mục gốc repo)
pip install -r tests/requirements.txt
python -m unittest discover -s tests

# Test web
cd web && npm ci && npm test
```

## Mốc kiểm soát

- **M1, cuối tuần 2:** luồng chính chạy trọn vẹn từ nộp job đến tải kết quả. Đạt ngày 25/10/2026 (tag `m1`).
- **M2, cuối tuần 3:** tự co giãn, 2 gói và xử lý Spot bị thu hồi. Đạt ngày 1/11/2026 (tag `m2`).
- **M3, cuối tuần 4:** đủ tính năng, dừng thêm tính năng mới. Đạt ngày 10/11/2026, trễ 2 ngày (tag `m3`).
- **M4, tuần 5:** nộp báo cáo, slide và video ngày 15/11/2026.
