# Hợp đồng giữa các thành phần SpotPool

Bản 1 – 18/10/2026, chốt trong buổi họp nhóm.
Bản 2 – 25/10/2026: sau M1. Chuyển mẫu job video sang Khuê, thêm cổng web qua CloudFront, chốt API Gateway REST, cập nhật phụ lục.
Bản 3 – 25/10/2026, chốt trong buổi họp sau M1. Đổi việc: Khuê làm trang người dùng (HM 13), Ngọc Anh làm trang quản trị (HM 15) và mẫu video (HM 8). Chốt luồng job hết trần (mục 8, 10), tỷ giá credit và giờ hoạt động gói Ổn định (mục 13). Worker dùng Python 3.13.
Bản 4 – 30/10/2026: theo mockup web. Thêm `GET /users/me`, chốt response của `GET /jobs` và `GET /admin/overview` (mục 4).

File này quy định tên tài nguyên, tên trường, trạng thái và cách các phần (API, dispatcher, worker, tính phí, web) trao đổi với nhau. Code khác với file này là code sai. Muốn đổi thì sửa file này trước (mục 14).

## 1. Quy ước chung

| Quy ước | Nội dung |
|---|---|
| Tên trường | `camelCase` trong JSON và DynamoDB |
| Giá trị `tier`, `kind` | chữ thường |
| Trạng thái | CHỮ HOA |
| `userId`, `jobId` | UUID v4 do hệ thống sinh |
| `taskId` | `task-0001`, `task-0002`… theo thứ tự file trong job, tối đa 100 file |
| Thời gian | số nguyên, số giây epoch (UTC), tên trường kết thúc bằng `At` |
| Ngày (để đếm theo ngày) | chuỗi `YYYY-MM-DD` theo giờ Việt Nam (UTC+7) |
| Tiền | số nguyên, đơn vị credit. Không dùng số thực (boto3 không ghi được `float` vào DynamoDB) |
| Đơn giá | số nguyên, credit cho 1 vCPU-giờ |
| vCPU-giây | số nguyên, làm tròn lên |
| Python | 3.13 cho cả Lambda, worker và image mẫu |
| Hằng số dùng chung | khai báo một lần trong `spotpool_core/contracts.py`, mọi phần import từ đây, không tự gõ chuỗi |

Giá trị cho phép:
- `tier`: `ondemand` (gói Ổn định) hoặc `spot` (gói Tiết kiệm). Web hiển thị "Ổn định" / "Tiết kiệm".
- `kind`: `video` hoặc `script`.
- `vcpu`: `1` (suất thường: 1 vCPU, 1,5 GB) hoặc `2` (suất đôi: 2 vCPU, 3 GB). Tạm thời API chỉ nhận `1` cho đến khi worker hỗ trợ suất đôi.
- `role`: `user` hoặc `admin`.

## 2. Tài nguyên AWS và biến môi trường

| Loại | Tên | Ghi chú |
|---|---|---|
| Bảng DynamoDB | `Users`, `Jobs`, `Tasks`, `Usage` | `Tasks` là bảng mới so với báo cáo, lưu từng việc của một job (mục 6.3). Bật DynamoDB Stream trên `Jobs` cho phần tính phí |
| Hàng đợi SQS (Standard) | `sqs-ondemand`, `sqs-spot` | visibility timeout 120 giây, long polling 20 giây, chuyển sang DLQ sau 10 lần nhận |
| DLQ | `sqs-dlq` | dùng chung cho cả hai hàng đợi |
| S3 | `input-<accountId>` | xóa sau 7 ngày; CORS cho phép `PUT` từ địa chỉ web |
| S3 | `output-<accountId>` | xóa sau 7 ngày; CORS cho phép `GET` từ địa chỉ web (xem log ngay trên trang) |
| ECR | `spotpool-video`, `spotpool-script` | danh sách image được phép |
| API Gateway | REST API, Lambda authorizer kiểu `TOKEN` | không dùng HTTP API: authorizer đọc `authorizationToken`, `methodArn` và Lambda đọc `requestContext.authorizer.userId`, chỉ REST API có |
| S3 + CloudFront | bucket `web-<accountId>` (private) sau CloudFront | cổng web và trang quản trị; CloudFront cho HTTPS, đọc bucket qua Origin Access Control |
| EC2 | `c7i-flex.large` (2 vCPU, 4 GB); gói Tiết kiệm dùng thêm `m7i-flex.large` (2 vCPU, 8 GB) | mỗi máy chạy 2 suất 1,5 GB; gói Free của tài khoản cho phép cả hai loại, cả hai có Spot ở 3 AZ |

`maxReceiveCount` để 10 thay vì 5: mỗi lần Spot bị thu hồi hoặc worker chết, message được nhận lại và bị đếm thêm một lần, để thấp thì task bị thu hồi vài lần có thể rơi nhầm vào DLQ.

Biến môi trường do Terraform truyền vào. Code **không đặt giá trị mặc định**, thiếu biến thì dừng ngay khi khởi động:

| Biến | Dùng ở |
|---|---|
| `USERS_TABLE`, `JOBS_TABLE`, `TASKS_TABLE`, `USAGE_TABLE` | mọi phần cần đọc/ghi bảng |
| `INPUT_BUCKET`, `OUTPUT_BUCKET` | API, dispatcher |
| `SQS_ONDEMAND_URL`, `SQS_SPOT_URL` | dispatcher, worker, API (nạp thêm) |
| `VIDEO_JOB_IMAGE`, `SCRIPT_JOB_IMAGE` | worker (URI đầy đủ trên ECR) |
| `PRICE_ONDEMAND`, `PRICE_SPOT` | API (credit/vCPU-giờ) |
| `TASK_MAX_SECONDS` | worker (thời gian chạy tối đa của một task) |
| `AWS_DEFAULT_REGION` | worker (`ap-southeast-1`; Lambda tự có sẵn) |
| `SPOTPOOL_QUEUE` | worker: `ondemand` hoặc `spot`, hàng đợi máy này nhận việc (mục 8) |
| `SPOTPOOL_ASG` | worker: tên ASG của máy, để bật/tắt bảo vệ khi giảm máy (mục 8) |

## 3. Luồng chính

```
1. Web/CLI  -- POST /jobs -->             API: tạo Jobs (UPLOADING), trả link tải lên cho từng file
2. Web/CLI  -- PUT theo link -->          S3 input: uploads/{userId}/{jobId}/{taskId}
3. Web/CLI  -- POST /jobs/{id}/start -->  API: kiểm tra đủ file, số dư, hạn mức; giữ credit;
                                          Jobs -> QUEUED; ghi submissions/{userId}/{jobId}/manifest.json
4. S3 Event (chỉ prefix submissions/) --> dispatcher: tạo Tasks (QUEUED), gửi mỗi task một message vào sqs-{tier}
5. Worker   -- nhận message -->           giành task, chạy container, ghi kết quả và log lên S3 output,
                                          ghi Tasks / Jobs / Usage, xóa message
6. Task cuối cùng kết thúc                worker chuyển job sang SUCCEEDED hoặc FAILED
7. DynamoDB Stream (job kết thúc) -->     billing: tính tiền, trả lại phần credit giữ thừa
8. Web/CLI  -- GET /jobs/{id} -->         API: trạng thái, tiến độ, link tải kết quả và log
```

Link tải lên chỉ trỏ tới `uploads/`, người dùng không ghi được vào `submissions/` nên không giả được manifest.

## 4. API

Mọi endpoint trừ đăng ký cần header `Authorization: Bearer <apiKey>`.

Lambda authorizer:
- Băm SHA-256 khóa, query GSI `apiKeyHash-index` của `Users` (không `scan`).
- Trả `context` gồm `userId`, `role` (chuỗi).
- Policy cho phép `arn:aws:execute-api:<region>:<account>:<apiId>/<stage>/*/*`, không dùng `methodArn`, vì kết quả được cache và dùng lại cho endpoint khác.
- Cache 300 giây: khóa cũ còn dùng được tối đa 5 phút sau khi đổi khóa.

Mọi response có header CORS vì web chạy khác domain với API.

| Method | Path | Mô tả | Hạng mục |
|---|---|---|---|
| POST | `/users` | Đăng ký, không cần khóa. Trả `userId`, `apiKey` (chỉ hiện một lần) | 5 |
| POST | `/users/me/rotate-key` | Đổi khóa API | 5 |
| GET | `/users/me` | Email, vai trò, số dư của mình | 13 |
| POST | `/jobs` | Tạo job, trả link tải lên | 5 |
| POST | `/jobs/{id}/start` | Bấm "Chạy" | 5 |
| GET | `/jobs/{id}` | Trạng thái, tiến độ, link tải | 5 |
| POST | `/jobs/{id}/cancel` | Hủy job | 5 |
| GET | `/jobs` | Danh sách job của mình, mới nhất trước | 13 |
| POST | `/jobs/{id}/topup` | Nâng trần chi phí, chạy tiếp phần tạm dừng | 14 |
| POST | `/credits` | Nạp credit ảo vào tài khoản | 14 |
| GET | `/billing` | Hóa đơn theo job, mức dùng theo ngày | 14 |
| GET | `/admin/overview` | Chỉ `role = admin`: số máy, hàng đợi, doanh thu so với chi phí AWS | 15 |

### POST /jobs

```json
{
  "tier": "spot",
  "kind": "video",
  "vcpu": 1,
  "costCap": 15000,
  "files": ["bai1.mp4", "bai2.mp4"],
  "params": {}
}
```

- `files`: 1–100 tên file gốc, chỉ dùng để hiển thị và đặt tên file khi tải về. Key trên S3 dùng `taskId` chứ không dùng tên này, nên không lo ký tự lạ, `/` hay `..`.
- Không cần gửi `image`: `video` dùng `spotpool-video`, `script` dùng `spotpool-script`.
- Tạo `Jobs` với `status = UPLOADING`, `unitPrice` lấy từ `PRICE_<TIER>` (chụp giá tại thời điểm tạo), `taskCount = tasksRemaining = số file`, các bộ đếm còn lại và `usedVcpuSeconds` bằng 0.

Response:

```json
{
  "jobId": "…",
  "status": "UPLOADING",
  "uploads": [
    {"taskId": "task-0001", "fileName": "bai1.mp4", "url": "https://…", "expiresIn": 3600}
  ]
}
```

### POST /jobs/{id}/start

1. Job phải đang `UPLOADING`. Nếu đã `QUEUED` thì chỉ ghi lại manifest rồi trả 200, nên gọi lại nhiều lần vẫn an toàn.
2. Kiểm tra đủ file trên S3 bằng `HeadObject`. Thiếu thì trả 400 kèm danh sách file thiếu (`missingFiles`). File lớn hơn 500 MB thì trả 400 kèm `maxBytes` và `tooLargeFiles`, vì link PUT không giới hạn được dung lượng (mục 9).
3. Một `TransactWriteItems`:
   - `Users`: `creditAvailable -= costCap`, `creditHeld += costCap`, `runningJobs += 1`, điều kiện `creditAvailable >= costCap AND runningJobs < maxConcurrentJobs`.
   - `Jobs`: `status = QUEUED`, `queuedAt = now`, điều kiện `status = UPLOADING`.
4. Ghi manifest (mục 5.1).

### GET /jobs/{id}

Query `Tasks` theo `jobId`. Link tải tạo mới mỗi lần gọi, hạn 15 phút. Web gọi lại mỗi 5 giây để cập nhật trạng thái.

```json
{
  "jobId": "…",
  "status": "RUNNING",
  "interrupted": true,
  "tier": "spot",
  "kind": "video",
  "vcpu": 1,
  "costCap": 15000,
  "usedVcpuSeconds": 7200,
  "chargedCredit": null,
  "progress": {"total": 20, "succeeded": 12, "failed": 0},
  "createdAt": 1760781600,
  "queuedAt": 1760781700,
  "startedAt": 1760781760,
  "finishedAt": null,
  "tasks": [
    {"taskId": "task-0001", "fileName": "bai1.mp4", "status": "SUCCEEDED",
     "interruptCount": 0, "checkpointAt": null, "resultUrl": "https://…", "logUrl": "https://…"}
  ]
}
```

`interrupted = true` khi có task đang `QUEUED` hoặc `RUNNING` mà `interruptCount > 0`. Khi đó web hiện "bị gián đoạn, đang chạy tiếp".

`checkpointAt` là lần gần nhất worker lưu checkpoint của task (job script và job video, mục 8.1), `null` nếu chưa lưu.

### POST /jobs/{id}/cancel

- `Jobs.status = CANCELLED`, điều kiện trạng thái hiện tại là `UPLOADING`, `QUEUED`, `RUNNING` hoặc `PAUSED`.
- Task `QUEUED`, `PAUSED` chuyển sang `CANCELLED` ngay. Task `RUNNING` giữ ownership đến khi worker dừng ở nhịp tim kế tiếp (tối đa 30 giây) và ghi usage/log cuối; sau đó chuyển `CANCELLED`. Billing chờ không còn task RUNNING mới quyết toán. Worker chết được đóng attempt với phí 0 khi lease hết và SQS giao lại.
- Message còn trong hàng đợi để nguyên, worker tự xóa khi thấy task đã kết thúc.

### POST /jobs/{id}/topup

Request: `{"amount": 5000}`.

Client nên thêm `requestId` (UUID) và giữ nguyên khi retry để không giữ credit hai lần. Chi tiết API credit, hóa đơn, idempotency và triển khai: [HM8/HM14/HM15](hm8-14-15.md).

- Một `TransactWriteItems`: `Users` (`creditAvailable -= amount`, `creditHeld += amount`, điều kiện `creditAvailable >= amount`) và `Jobs` (`costCap += amount`, điều kiện trạng thái là `QUEUED`, `RUNNING` hoặc `PAUSED`).
- Nếu job đang `PAUSED`: `Jobs.status = QUEUED`, mọi task `PAUSED` chuyển sang `QUEUED` và được gửi lại message vào hàng đợi ghi trong `Tasks.queue`.

### GET /users/me

`GetItem` trên `Users` theo `userId` của authorizer. Không trả `apiKeyHash`.

```json
{
  "userId": "…",
  "email": "khue@example.com",
  "role": "user",
  "creditAvailable": 45000,
  "creditHeld": 5000,
  "runningJobs": 1,
  "maxConcurrentJobs": 2,
  "createdAt": 1760781600
}
```

Web hiện "Credit khả dụng" là `creditAvailable`, "Đang giữ" là `creditHeld`. `Users` không có tên người dùng nên góc trên hiện `email`.

### GET /jobs

Query GSI `userId-createdAt-index`, mới nhất trước (`ScanIndexForward = false`). Chỉ đọc `Jobs`, không query `Tasks`, để trang danh sách nhẹ.

- `?limit=`: mặc định 20, tối đa 50.
- `?next=`: chuỗi `next` của trang trước (`LastEvaluatedKey` dạng JSON, mã hóa base64url). Không còn trang sau thì `next = null`.
- `?status=`: không bắt buộc, lọc theo một trạng thái ở mục 7.1 (`FilterExpression`).

```json
{
  "jobs": [
    {"jobId": "…", "status": "RUNNING", "tier": "spot", "kind": "video",
     "name": "bai-giang-01.mp4", "progress": {"total": 20, "succeeded": 12, "failed": 0},
     "costCap": 5000, "chargedCredit": null, "createdAt": 1760781600}
  ],
  "next": "eyJ1c2VySWQiOi…"
}
```

- `name` = `files[0]` của job. Job chưa có trường tên riêng; web hiện `name`, có nhiều file thì thêm "và N file khác" theo `progress.total`.
- Tìm theo tên trên web lọc trong các job đã tải, không cần API riêng.
- Ô "Đang chạy", "Hoàn thành" ở trang Job của tôi đếm trên danh sách này.

### GET /admin/overview

Chỉ `role = admin`, khác thì trả 403. Quyền: `ec2:DescribeInstances`, `sqs:GetQueueAttributes`, `ce:GetCostAndUsage`, đọc `Jobs`.

```json
{
  "machines": [
    {"tier": "ondemand", "instanceType": "c7i-flex.large", "az": "ap-southeast-1a", "count": 1},
    {"tier": "spot", "instanceType": "c7i-flex.large", "az": "ap-southeast-1b", "count": 2}
  ],
  "queues": {
    "ondemand": {"waiting": 2, "running": 1, "slots": 2},
    "spot": {"waiting": 6, "running": 3, "slots": 6}
  },
  "daily": [
    {"date": "2026-10-29", "revenue": 30000, "awsCost": 21500}
  ],
  "awsCostUpdatedAt": 1761696000
}
```

- `machines`: `DescribeInstances` lọc tag `Project = spotpool`, trạng thái `running`, gộp theo tag `Tier`, loại máy, AZ.
- `queues`: `waiting` = `ApproximateNumberOfMessages`, `running` = `ApproximateNumberOfMessagesNotVisible` của `sqs-<gói>` (đơn vị là task, không phải job). `slots` = 2 × số máy của gói. Suất đang dùng hiện theo gói (`running` / `slots`), không theo từng máy.
- `daily`: 7 ngày gần nhất theo giờ Việt Nam, ngày cũ trước.
  - `revenue`: tổng `chargedCredit` của các job có `settledAt` trong ngày (scan `Jobs`, đủ nhanh ở quy mô đồ án).
  - `awsCost`: Cost Explorer, chi phí theo ngày lọc tag `Project = spotpool`, nhân 26.000 (mục 13), làm tròn. Cost Explorer tính phí mỗi lần gọi và chậm khoảng 1 ngày, nên lưu kết quả vào `cache/aws-cost.json` trong bucket output, quá 24 giờ mới gọi lại. `awsCostUpdatedAt` là lúc gọi gần nhất.

## 5. Manifest và message SQS

### 5.1. Manifest (API ghi, dispatcher đọc)

Key: `submissions/{userId}/{jobId}/manifest.json` trong bucket input.

```json
{
  "schemaVersion": 1,
  "jobId": "…",
  "userId": "…",
  "tier": "spot",
  "kind": "video",
  "vcpu": 1,
  "image": "spotpool-video",
  "params": {},
  "outputBucket": "output-<accountId>",
  "inputs": [
    {"taskId": "task-0001", "fileName": "bai1.mp4",
     "bucket": "input-<accountId>", "key": "uploads/{userId}/{jobId}/task-0001"}
  ]
}
```

Dispatcher kiểm tra key của manifest khớp với `userId`, `jobId` ghi bên trong.

### 5.2. Message SQS (dispatcher gửi, worker đọc)

Mỗi task một message, gửi vào `sqs-{tier}`:

```json
{
  "schemaVersion": 1,
  "jobId": "…",
  "taskId": "task-0001",
  "userId": "…",
  "tier": "spot",
  "kind": "video",
  "vcpu": 1,
  "image": "spotpool-video",
  "params": {},
  "input": {"bucket": "input-<accountId>", "key": "uploads/{userId}/{jobId}/task-0001"},
  "output": {"bucket": "output-<accountId>", "key": "results/{userId}/{jobId}/task-0001"}
}
```

Quy tắc:
- **Nội dung message không bao giờ thay đổi** sau khi dispatcher gửi. Chuyển sang on-demand thì gửi nguyên văn sang `sqs-ondemand`, nhờ vậy fingerprint luôn khớp.
- `tier` là gói người dùng trả tiền, không phải nơi đang chạy. Nơi đang chạy lưu ở `Tasks.queue`.
- Số lần bị thu hồi lưu ở `Tasks.interruptCount`, không nằm trong message.
- Dispatcher và worker phải chịu được message trùng, vì S3 và SQS đều có thể giao trùng: `taskId` cố định, tạo task có điều kiện, giành task có điều kiện.

## 6. Bảng DynamoDB

### 6.1. Users

Khóa: PK `userId`. GSI `apiKeyHash-index` (PK `apiKeyHash`).

| Trường | Kiểu | Ghi chú |
|---|---|---|
| `userId` | S | UUID |
| `email` | S | không bắt buộc duy nhất, vì đăng nhập bằng khóa API |
| `apiKeyHash` | S | SHA-256 của khóa |
| `role` | S | `user` hoặc `admin` (admin đặt tay) |
| `creditAvailable` | N | credit dùng được |
| `creditHeld` | N | credit đang giữ cho các job đã bấm Chạy. Số dư hiển thị = `creditAvailable + creditHeld` |
| `runningJobs` | N | số job đã bấm Chạy mà chưa quyết toán |
| `maxConcurrentJobs` | N | mặc định 2 |
| `freeErrorQuotaPerDay` | N | mặc định 3 |
| `errorsToday` | N | số job lỗi/hủy đã được miễn phí trong ngày |
| `errorsDay` | S | ngày của `errorsToday` |
| `createdAt` | N | |

Tách `creditAvailable` và `creditHeld` vì điều kiện của DynamoDB không làm được phép trừ. Giữ credit chỉ cần điều kiện `creditAvailable >= :amount`.

### 6.2. Jobs

Khóa: PK `jobId`. GSI `userId-createdAt-index` (PK `userId`, SK `createdAt`). Bật Stream `NEW_AND_OLD_IMAGES`.

| Trường | Kiểu | Ai ghi |
|---|---|---|
| `jobId`, `userId`, `tier`, `kind`, `vcpu`, `image`, `params` | | API khi tạo |
| `files` | L | API: tên file gốc theo thứ tự `task-0001`, `task-0002`… |
| `status` | S | xem mục 7.1 |
| `costCap` | N | API (tạo, nạp thêm) |
| `unitPrice` | N | API: credit/vCPU-giờ, chụp lúc tạo |
| `usedVcpuSeconds` | N | worker cộng dồn (mục 10) |
| `taskCount` | N | API khi tạo |
| `tasksRemaining`, `tasksSucceeded`, `tasksFailed` | N | API khởi tạo; worker và Lambda DLQ cập nhật |
| `createdAt`, `queuedAt` | N | API |
| `startedAt`, `finishedAt` | N | worker, Lambda DLQ |
| `chargedCredit`, `settledAt` | N | billing khi quyết toán |

Ngân sách còn lại (vCPU-giây) = `floor(costCap × 3600 / unitPrice) − usedVcpuSeconds`. Không lưu thành trường riêng để khỏi phải đồng bộ khi nạp thêm.

### 6.3. Tasks

Bảng mới so với báo cáo. Báo cáo ghi job video được tách thành nhiều việc, mỗi video một việc, và worker giành việc, gửi nhịp tim trên DynamoDB, nhưng ba bảng `Users`, `Jobs`, `Usage` không có chỗ lưu trạng thái của từng việc. Ví dụ job 20 video thì có 1 dòng trong `Jobs` (thông tin chung, tiến độ tổng) và 20 dòng trong `Tasks` (từng video: đang chờ hay đang chạy, máy nào đang giữ, nhịp tim, số lần bị thu hồi, link kết quả). Không gộp vào `Jobs` vì nhiều worker cùng ghi vào một dòng sẽ đè nhau.

Khóa: PK `jobId`, SK `taskId`.

| Trường | Kiểu | Ghi chú |
|---|---|---|
| các trường của message (mục 5.2) | | dispatcher ghi khi tạo |
| `fileName` | S | tên file gốc |
| `fingerprint` | S | băm nội dung message |
| `status` | S | xem mục 7.2 |
| `queue` | S | `ondemand` hoặc `spot`: hàng đợi hiện tại, ban đầu bằng `tier` |
| `interruptCount` | N | số lần bị Spot thu hồi |
| `attempt` | N | tăng 1 mỗi lần giành task |
| `owner`, `leaseUntil`, `heartbeatAt` | S, N, N | worker đang giữ task |
| `queuedAt`, `startedAt`, `finishedAt` | N | đo thời gian chờ (SLA) |
| `resultKey`, `logKey` | S | key trên bucket output của lần chạy gần nhất |
| `checkpointAttempt`, `checkpointAt` | N, N | lượt chạy có checkpoint mới nhất và lúc lưu (mục 8.1). Chỉ worker đang giữ task được ghi |
| `checkpointSha256` | S | SHA-256 (hex) của `checkpoint.tgz` đó, ghi cùng lệnh với `checkpointAttempt`; worker kiểm tra trước khi giải nén |
| `checkpointBytes`, `checkpointFiles` | N, N | kích thước và số file của checkpoint đó |
| `error` | S | lý do lỗi, tối đa 1000 ký tự |

### 6.4. Usage

Khóa: PK `userId`, SK `usageId` = `{jobId}#{taskId}#{attempt}`. Khóa cố định cho mỗi lần chạy nên ghi lại cũng không bị nhân đôi.

Mỗi lần chạy kết thúc ghi một dòng:

| Trường | Ghi chú |
|---|---|
| `jobId`, `taskId`, `attempt` | |
| `tier`, `vcpu` | |
| `outcome` | `SUCCEEDED`, `FAILED`, `PAUSED` hoặc `INTERRUPTED` (mục 10) |
| `vcpuSeconds` | `vcpu × số giây container chạy`, làm tròn lên |
| `startedAt`, `finishedAt` | |
| `day` | `YYYY-MM-DD`, cho biểu đồ mức dùng theo ngày |
| `lifecycle`, `instanceType`, `az`, `instanceId` | lấy từ instance metadata, dùng cho thí nghiệm chi phí |

## 7. Trạng thái

### 7.1. Job

| Từ | Sang | Ai | Khi nào |
|---|---|---|---|
| (mới) | `UPLOADING` | API | `POST /jobs` |
| `UPLOADING` | `QUEUED` | API | `POST /jobs/{id}/start` thành công |
| `QUEUED` | `RUNNING` | worker | task đầu tiên bắt đầu chạy (ghi `startedAt` nếu chưa có) |
| `QUEUED`, `RUNNING` | `PAUSED` | worker | hết trần chi phí |
| `PAUSED` | `QUEUED` | API | nạp thêm |
| `QUEUED`, `RUNNING` | `SUCCEEDED` | worker, Lambda DLQ | `tasksRemaining = 0` và `tasksFailed = 0` |
| `QUEUED`, `RUNNING` | `FAILED` | worker, Lambda DLQ | `tasksRemaining = 0` và `tasksFailed > 0` |
| `UPLOADING`, `QUEUED`, `RUNNING`, `PAUSED` | `CANCELLED` | API | người dùng hủy |

- Bảng `Jobs` không lưu trạng thái "bị gián đoạn", vì nhiều worker cùng ghi một trạng thái sẽ đè nhau. Web suy ra từ `Tasks` (trường `interrupted` ở mục 4).
- `SUCCEEDED`, `FAILED`, `CANCELLED` là trạng thái cuối, không đổi nữa.
- Mọi lần đổi trạng thái dùng `UpdateItem` có điều kiện trạng thái cũ, điều kiện sai thì bỏ qua.

### 7.2. Task

| Từ | Sang | Ai | Khi nào |
|---|---|---|---|
| (mới) | `QUEUED` | dispatcher | đọc manifest |
| `QUEUED` | `RUNNING` | worker | giành được task |
| `RUNNING` đã mất nhịp tim quá 2 phút | `RUNNING` | worker khác | giành lại |
| `RUNNING` | `QUEUED` | worker | Spot bị thu hồi (`interruptCount + 1`) |
| `RUNNING` | `SUCCEEDED` | worker | container thoát mã 0 và đã ghi kết quả |
| `RUNNING` | `FAILED` | worker | container lỗi hoặc chạy quá `TASK_MAX_SECONDS` |
| `QUEUED`, `RUNNING` | `PAUSED` | worker | hết trần chi phí |
| `PAUSED` | `QUEUED` | API | nạp thêm |
| `QUEUED`, hoặc `RUNNING` đã mất nhịp tim | `FAILED` | Lambda DLQ | message rơi vào `sqs-dlq` |
| `QUEUED`, `RUNNING`, `PAUSED` | `CANCELLED` | API | hủy job |

## 8. Worker

- Máy của gói Tiết kiệm chỉ lấy việc từ `sqs-spot`, máy của gói Ổn định chỉ lấy từ `sqs-ondemand`. Mỗi máy chạy 2 suất.
- Worker chạy task lấy từ hàng đợi của mình, **không so `tier` với hàng đợi**, vì task bị chuyển sang on-demand vẫn mang `tier = spot`. Nếu `Tasks.queue` khác hàng đợi vừa lấy thì gửi message sang đúng hàng đợi rồi xóa message cũ.
- `image` ánh xạ sang URI: `spotpool-video` → `VIDEO_JOB_IMAGE`, `spotpool-script` → `SCRIPT_JOB_IMAGE`. Giá trị khác thì task `FAILED`.

Khi nhận message:
1. Đọc `Jobs` (đọc nhất quán).
   - `CANCELLED`: task chuyển `CANCELLED`, xóa message.
   - `PAUSED` hoặc hết ngân sách: task và job chuyển `PAUSED`, xóa message.
2. Giành task bằng `UpdateItem` có điều kiện: `fingerprint` khớp và (`status = QUEUED` hoặc `status = RUNNING AND leaseUntil < now`); đồng thời `attempt += 1`.
   Giành hụt mà task đã `SUCCEEDED`, `FAILED`, `CANCELLED` hoặc `PAUSED` thì xóa message, các trường hợp khác để message tự hiện lại.
3. Chuyển job `QUEUED` → `RUNNING` (có điều kiện, sai thì bỏ qua).
4. Thời gian tối đa của container = `min(TASK_MAX_SECONDS, ngân sách còn lại / vcpu)`.

Trong lúc chạy:
- Mỗi 30 giây: `leaseUntil = now + 120`, `heartbeatAt = now` (điều kiện `owner` là mình và `status = RUNNING`), gia hạn visibility 120 giây. Trước đó đọc `Jobs`:
  - Job `CANCELLED` hoặc điều kiện của `Tasks` sai (task đã bị API hủy, hoặc worker khác đã giành lại): dừng container, không ghi gì thêm.
  - Job `PAUSED` (task khác đã chạm trần): dừng container và kết thúc lần chạy như hết trần: task `PAUSED`, `Usage.outcome = PAUSED`, có tính tiền phần đã chạy. Không để task ở `RUNNING`.
- Mỗi 5 giây hỏi IMDSv2 `spot/instance-action`.

Bảo vệ khi giảm máy: ASG co giãn theo (task chờ + task đang chạy) / số máy, khi giảm có thể chọn trúng máy đang chạy. Nhận task vào suất đầu tiên thì gọi `SetInstanceProtection(ProtectedFromScaleIn=True)` cho máy mình (`SPOTPOOL_INSTANCE_ID`, `SPOTPOOL_ASG`); cả 2 suất trống thì gọi với `False`. Gọi lỗi thì ghi log rồi chạy tiếp, không dừng worker: máy bị tắt giữa chừng thì task vẫn chạy lại nhờ hết `leaseUntil`.

Khi lần chạy kết thúc, ghi **trong một `TransactWriteItems`** để không bao giờ cộng trùng:
1. `Tasks`: trạng thái mới, `resultKey`, `logKey`, `finishedAt`, `error` (điều kiện `owner` là mình và `status = RUNNING`).
2. `Jobs`: cộng `usedVcpuSeconds` (trừ khi bị thu hồi). Task kết thúc hẳn (`SUCCEEDED` hoặc `FAILED`) thì `tasksRemaining − 1` và `tasksSucceeded + 1` hoặc `tasksFailed + 1`. Điều kiện: job đang `QUEUED`, `RUNNING` hoặc `PAUSED`. Phải chấp nhận `PAUSED`, vì task chạy song song có thể xong sau khi task khác đã làm job hết trần; nếu từ chối thì task kẹt ở `RUNNING` mãi.
3. `Usage`: thêm một dòng (điều kiện chưa tồn tại).

Gọi lại giao dịch thì dùng cùng `ClientRequestToken`. Giao dịch bị từ chối mà task đã ở đúng trạng thái mình định ghi thì coi như đã ghi xong.

Sau giao dịch:
- Thành công hoặc lỗi: thử chuyển job sang `SUCCEEDED`, không được thì thử `FAILED` (điều kiện ở mục 7.1), rồi xóa message.
- Hết trần: job chuyển `PAUSED`, xóa message.
- Spot bị thu hồi: ngừng nhận việc mới, dừng container, task chuyển `QUEUED`, `interruptCount + 1`. Nếu `interruptCount > 2` thì `Tasks.queue = ondemand`, gửi nguyên văn message sang `sqs-ondemand` rồi xóa message ở `sqs-spot`. Ngược lại đặt visibility về 0 để máy khác lấy.

Quy tắc khi job `PAUSED`: mọi task chưa kết thúc của job phải ở `PAUSED`, không còn task `QUEUED` hay `RUNNING`. Vì vậy worker nhận message của job đã `PAUSED` thì chuyển task `QUEUED` sang `PAUSED` rồi mới xóa message (bước 1 ở trên), và nạp thêm (mục 4) chỉ cần gửi lại các task `PAUSED`. Task bị dừng vì hết trần chạy lại sau khi nạp thêm: job script có checkpoint và job video từ 40 giây trở lên thì chạy tiếp từ checkpoint (mục 8.1), còn lại chạy lại từ đầu.

Kết quả và log của mỗi lần chạy nằm trong một thư mục riêng, nên lần chạy cũ không ghi đè được kết quả của lần chạy mới:
- `results/{userId}/{jobId}/{taskId}/{attempt}/result`
- `results/{userId}/{jobId}/{taskId}/{attempt}/log.txt`: stdout và stderr của container, cắt ở 10 MB. Luôn tải log lên, kể cả khi lỗi.
- `results/{userId}/{jobId}/{taskId}/{attempt}/checkpoint.tgz`: checkpoint của lượt chạy đó (mục 8.1).

### 8.1. Checkpoint của job script và job video

Kiểu hợp tác: chương trình trong container tự ghi trạng thái, nền tảng lo lưu và khôi phục. Job script: script của người dùng tự ghi. Job video: image `spotpool-video` tự ghi (bổ sung ngày 13/11/2026), như sau:

- Chia phần hình thành các đoạn `SPOTPOOL_VIDEO_SEGMENT_SECONDS` giây (mặc định 20; đoạn cuối gộp phần lẻ). Nén xong đoạn nào thì ghi `seg_NNNN.mp4` vào thư mục checkpoint (ghi ra `*.tmp` rồi đổi tên). Lượt sau bỏ qua các đoạn đã có, rồi ghép các đoạn bằng concat (copy hình, không nén lại) và nén âm thanh AAC một lần từ file gốc.
- `video.meta` ghi chiều cao, độ dài đoạn, thời lượng và kích thước file đầu vào. Khác với lần trước thì xóa các đoạn cũ và nén từ đầu.
- Video ngắn hơn hai đoạn (40 giây với mặc định), không đọc được thời lượng hoặc không có hình thì nén một lượt như trước, không dùng checkpoint.
- Các đoạn đã nén tính vào giới hạn 64 MB bên dưới. Video dài vượt giới hạn thì từ đó không lưu thêm (log ghi một dòng), lượt sau chạy tiếp từ bản cuối đã lưu được.

Quy tắc chung cho cả hai loại:

- Worker mount thư mục rỗng vào `/checkpoint` (đọc ghi) và đặt `SPOTPOOL_CHECKPOINT_DIR=/checkpoint`. Script hoặc mẫu video ghi file trạng thái vào đây, ghi ra `*.tmp` rồi đổi tên để không bị lưu file ghi dở.
- Lưu file thường trong cả cây thư mục, tối đa 8 cấp thư mục con; bỏ qua `*.tmp` (cả thư mục tên `*.tmp`), symlink, file đặc biệt và thư mục rỗng. Tối đa 1000 file, 64 MB và 5000 mục phải duyệt; vượt thì không lưu và ghi một dòng vào log.
- Không đi theo symlink kể cả khi script tráo đúng lúc worker đang đọc: worker chạy bằng root, máy chạy task của nhiều người, nên trên Linux mỗi thư mục con và mỗi file được mở bằng tên một cấp, tương đối với descriptor của thư mục cha (`dir_fd`), có `O_NOFOLLOW` (`O_DIRECTORY` với thư mục). Đi theo đường dẫn đầy đủ thì script tráo một cấp thư mục thành symlink là worker đọc được file của máy chủ hoặc của task khác.
- Khi nào lưu: sau mỗi nhịp tim thành công nếu file đổi (máy chết đột ngột vẫn còn bản gần nhất), và một lần cuối sau khi dừng container vì Spot bị thu hồi hoặc hết trần. Lưu lần cuối không bị ngắt bởi thông báo thu hồi.
- Bản lưu phải nhất quán: worker ghi lại tên, inode, kích thước, thời điểm sửa của các file trước khi đóng gói và so lại sau khi đóng gói. Khác nhau nghĩa là script vừa ghi file giữa chừng, bản đó bị bỏ và nhịp tim sau lưu lại.
- Cách lưu: tải `checkpoint.tgz` lên key của lượt chạy hiện tại, rồi `UpdateItem` `Tasks` với `checkpointAttempt = attempt`, `checkpointSha256` (SHA-256 của file `checkpoint.tgz`), `checkpointAt`, `checkpointBytes`, `checkpointFiles`, điều kiện `owner` là mình và `status = RUNNING`. Worker cũ mất quyền giữ task không ghi được `checkpointAttempt`, nên lượt sau không đọc nhầm bản của nó. Lỗi khi lưu không làm hỏng task.
- Khôi phục: lúc giành task, `UpdateItem` trả về `checkpointAttempt` và `checkpointSha256` (nếu có). Sau khi tải file đầu vào, worker tải checkpoint của lượt đó, so SHA-256 với `checkpointSha256`, giải nén vào thư mục rỗng rồi mới chạy container. Chỉ nhận file thường; mỗi cấp tên phải hợp lệ (không rỗng, không `.`, `..`, `\`), không quá 8 cấp, không trùng, không vừa là file vừa là thư mục. Không tải được, sai mã băm hoặc archive sai thì xóa phần đã giải nén, ghi một dòng vào log và chạy lại từ đầu.
- Tính tiền không đổi: lượt bị thu hồi vẫn không tính; lượt chạy tiếp chỉ tính thời gian của chính nó, nên người dùng không trả lại phần đã làm. Worker ghi dòng `[SpotPool] …` vào log khi khôi phục hoặc lưu lần cuối.

Lambda DLQ nhận message từ `sqs-dlq`:
- Task đang `QUEUED`, hoặc `RUNNING` mà đã mất nhịp tim: chuyển `FAILED` (`tasksRemaining − 1`, `tasksFailed + 1` trong cùng giao dịch), rồi thử kết thúc job.
- Job đã `CANCELLED` (kể cả bị hủy đúng lúc DLQ đang ghi): task chuyển `CANCELLED` với cùng bộ đếm, giống worker đóng task của máy đã chết. Billing chỉ quyết toán job bị hủy khi không còn task `RUNNING`; giao dịch đổi bản ghi job nên Jobs stream gọi lại billing.
- Các trường hợp khác bỏ qua: dispatcher cố ý gửi trùng message, nên message trùng có thể rơi vào DLQ trong khi task vẫn đang chạy tốt.

## 9. Image và container

Worker chạy container như sau:

```
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 64 --user 65534:65534 \
  --cpus <vcpu> --memory <vcpu × 1536>m --memory-swap <vcpu × 1536>m \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --mount type=bind,source=<file đầu vào>,target=/input/payload,readonly \
  --mount type=bind,source=<thư mục ra>,target=/output \
  [--mount type=bind,source=<thư mục checkpoint>,target=/checkpoint --env SPOTPOOL_CHECKPOINT_DIR=/checkpoint] \
  <image> /input/payload /output/result
```

Phần trong ngoặc vuông là thư mục checkpoint, có ở cả job script và job video (mục 8.1).

Giới hạn dung lượng (bổ sung ngày 13/11/2026). Mỗi máy chạy 2 suất trên cùng một ổ đĩa, nên một task không được làm đầy ổ và làm hỏng task của người khác:
- File của task (đầu vào, `/output`, `/checkpoint`, log) nằm trong `/var/lib/spotpool/work` trên ổ đĩa (biến `SPOTPOOL_WORK_DIR`), không ở `/tmp`: `/tmp` của Amazon Linux 2023 là tmpfs nằm trong RAM, chỉ 1,9 GB trên máy 4 GB.
- File đầu vào tối đa 500 MB. API kiểm tra lúc start; worker vẫn đếm byte trong lúc tải và dừng ngay khi vượt, vì người dùng có thể ghi đè file bằng link PUT cũ sau khi start.
- `/output` và `/checkpoint` cộng lại tối đa 1,5 GB và 20.000 file hoặc thư mục. Worker kiểm tra mỗi giây (không đi theo symlink) và một lần sau khi container dừng; vượt thì xóa container, task `FAILED`, không tải kết quả lên.
- Hai suất dùng nhiều nhất 2 × (500 MB + 1,5 GB) = 4 GB, trong khi ổ còn khoảng 5 GB trống sau khi cài hệ điều hành và image.

Image phải:
- nhận đúng 2 tham số: đường dẫn file vào và đường dẫn file ra;
- ghi kết quả thành một file thường tại `/output/result`;
- thoát mã 0 khi thành công, khác 0 khi lỗi; in log ra stdout/stderr;
- chạy được khi không có mạng và không có quyền root.

| Image | Đầu vào | Đầu ra | `params` |
|---|---|---|---|
| `spotpool-video` | một file video | file MP4 | `{"height": 720}`, mặc định 720 |
| `spotpool-script` | file `.py` (định dạng chi tiết chốt ở hạng mục 8) | nội dung stdout | không có |

## 10. Tính phí

Worker chỉ ghi sự kiện (`Usage`, `Jobs.usedVcpuSeconds`). Lambda billing quyết định số tiền.

| `outcome` của lần chạy | Tính vào ngân sách job | Tính tiền |
|---|---|---|
| `SUCCEEDED` | có | có |
| `PAUSED` (bị cắt vì hết trần, kể cả bị dừng vì task khác đã làm job hết trần) | có | có. Sau khi nạp thêm, task chạy lại (job script có checkpoint thì chạy tiếp từ checkpoint) và lần chạy mới tính tiền riêng |
| `FAILED` (lỗi của job) | có | miễn phí nếu còn lượt lỗi miễn phí trong ngày, hết lượt thì tính |
| `INTERRUPTED` (Spot bị thu hồi) | không | không, nền tảng chịu |
| `CANCELLED` (người dùng hủy khi task đang chạy, worker dừng ở nhịp tim kế tiếp) | có | như `FAILED`: miễn phí nếu còn lượt lỗi miễn phí trong ngày, hết lượt thì tính |
| Mất nhịp tim (worker chết, task được giao lại) | không có dòng `Usage`. Nếu job đã bị hủy thì lượt chạy dở được đóng với `CANCELLED`, 0 vCPU-giây | không |

Quyết toán: Lambda billing đọc DynamoDB Stream của `Jobs`, chạy khi job chuyển sang `SUCCEEDED`, `FAILED` hoặc `CANCELLED` mà chưa có `settledAt`. Job chưa từng bấm Chạy (không có `queuedAt`) thì bỏ qua vì chưa giữ credit.
- `charge = ceil(tổng vCPU-giây tính tiền × unitPrice / 3600)`, **không bao giờ vượt `costCap`**. Khi nhiều task chạy song song có thể lố trần một chút, phần lố nền tảng chịu.
- Job có task lỗi hoặc bị hủy: nếu `errorsToday < freeErrorQuotaPerDay` thì phần lỗi miễn phí và `errorsToday + 1` (sang ngày mới thì đặt lại).
- Một `TransactWriteItems`: `Users` (`creditHeld − costCap`, `creditAvailable + (costCap − charge)`, `runningJobs − 1`) và `Jobs` (`chargedCredit`, `settledAt`, điều kiện `settledAt` chưa tồn tại).
- Task chạy bằng on-demand sau khi bị thu hồi quá 2 lần vẫn tính theo giá của `tier = spot`. Phần chênh lệch là chi phí rủi ro của nền tảng.

## 11. Ai được ghi gì

| Nơi | Được ghi bởi |
|---|---|
| `Users` | API, billing |
| `Jobs` | API, worker, Lambda DLQ, billing (mỗi bên chỉ các trường ghi ở mục 6.2) |
| `Tasks` | dispatcher (tạo), worker, API (hủy, nạp thêm), Lambda DLQ |
| `Usage` | chỉ worker |
| S3 `uploads/` | người dùng, qua link tải lên |
| S3 `submissions/` | chỉ API |
| S3 `results/` | chỉ worker |
| `sqs-ondemand`, `sqs-spot` | gửi: dispatcher, worker (chuyển hàng đợi), API (nạp thêm); nhận và xóa: worker |
| `sqs-dlq` | Lambda DLQ |

Code:
- `spotpool_core/contracts.py` chứa hằng số và hàm kiểm tra dữ liệu dùng chung. Khuê giữ, sửa qua PR có cả nhóm xem. Mọi Lambda đóng gói kèm thư mục `spotpool_core/`.
- Terraform chung một thư mục `infra/`, mỗi người một file: `network.tf`, `iam.tf`, `compute.tf`, `web.tf` (Nghĩa); `queues.tf`, `dispatcher.tf` (Khuê); `tables.tf`, `buckets.tf`, `api.tf` (Ngọc Anh).
- Image job: `job-templates/script/` Khuê (đã xong); `job-templates/video/` Ngọc Anh.
- Code web và trang quản trị trong `web/`, một app React chung: Khuê dựng app và các trang người dùng (HM 13); Ngọc Anh thêm trang quản trị (HM 15) và trang hóa đơn (HM 14).
- Bucket input chỉ có **một** `aws_s3_bucket_notification`, đặt trong `dispatcher.tf`. Khai báo hai cái cho cùng một bucket thì cái sau ghi đè cái trước.
- Khi bảng và hàng đợi đã có trong Terraform, `iam.tf` tham chiếu trực tiếp (ví dụ `aws_dynamodb_table.jobs.arn`) thay vì gõ tên.

## 12. Khác so với báo cáo tiến độ (ghi vào báo cáo cuối kỳ)

- Thêm bảng `Tasks` để theo dõi từng việc của một job.
- Thêm endpoint: `POST /users`, `POST /users/me/rotate-key`, `GET /users/me`, `POST /jobs/{id}/start`, `GET /jobs`, `POST /credits`, `GET /admin/overview`.
- Số lần bị thu hồi lưu trong bảng `Tasks` chứ không nằm trong message SQS (Bảng 2.2, giao diện 9).
- Thời gian chạy tối đa do worker tính từ ngân sách còn lại lúc bắt đầu chạy, không nằm trong message, vì nó thay đổi khi người dùng nạp thêm.
- Thêm Lambda xử lý `sqs-dlq` và Lambda billing chạy theo DynamoDB Stream của `Jobs`.
- Cổng web đặt sau CloudFront thay vì mở thẳng từ S3, vì static website hosting của S3 chỉ có HTTP.
- Máy worker cài Docker và nạp worker agent bằng user data khi khởi động, thay cho Custom AMI.
- Thêm checkpoint cho job script và job video (mục 8.1): Báo cáo 1 và 2 để checkpoint là hướng mở rộng, nhóm làm thêm sau khi xong 24 hạng mục.
- Backlog-per-instance tính cả task đang chạy: (`ApproximateNumberOfMessagesVisible` + `ApproximateNumberOfMessagesNotVisible`) / `GroupInServiceInstances`, mục tiêu 2 (Bảng 2.2, giao diện 15 chỉ ghi số task chờ). Chỉ tính task chờ thì lúc hàng đợi vừa hết mà máy còn đang chạy, ASG thấy backlog bằng 0 và giảm máy quá sớm.

## 13. Chưa chốt

| Vấn đề | Đề xuất | Ai chốt |
|---|---|---|
| Đơn giá mỗi gói | Chốt sau khi đo ở tuần 3–4 như báo cáo. Tạm dùng `PRICE_ONDEMAND = 6000`, `PRICE_SPOT = 3000` | Cả nhóm |
| Hạn mức mặc định | Credit ban đầu 50.000; 2 job đồng thời; 3 job lỗi miễn phí mỗi ngày | Cả nhóm |
| Định dạng job script | Tạm thời một file `.py`, kết quả là stdout (image hiện tại). Có cần `.zip` kèm dữ liệu không | Khuê, hạng mục 8 |
| Giới hạn dung lượng file tải lên | Link `PUT` không giới hạn được dung lượng; cần thì đổi sang presigned POST | Ngọc Anh |
| Thời gian chạy tối đa của một task | `TASK_MAX_SECONDS = 7200` | Khuê |
| Suất đôi trên worker | Trường `vcpu` đã có; API chỉ mở `vcpu = 2` khi worker hỗ trợ | Khuê |
| Job `PAUSED` quá 7 ngày | File đầu vào đã bị xóa nên không chạy tiếp được, coi như hủy | Cả nhóm |
| Quy đổi credit | Đã chốt: 1 credit = 1 đồng. Trang quản trị quy đổi chi phí AWS theo tỷ giá cố định 1 USD = 26.000 credit | Cả nhóm |
| Giờ hoạt động của gói Ổn định | Đã chốt: mặc định 8h–23h giờ Việt Nam, mọi ngày; đổi được qua biến Terraform `ondemand_hours_start`, `ondemand_hours_end` (ví dụ mở rộng hôm demo). Ngoài giờ ASG Ổn định về 0 máy, job vẫn chạy nhờ luật bật máy từ 0 nhưng không cam kết chờ ≤ 1 phút | Nghĩa, hạng mục 11 |

## 14. Đổi hợp đồng

1. Mở PR sửa file này trước, ghi rõ lý do. Cả nhóm xem, Tv1 duyệt.
2. Merge xong thì báo group, sau đó mới sửa code.
3. Đổi cấu trúc manifest hoặc message thì tăng `schemaVersion`.

## Phụ lục: việc cần sửa

Việc của bản 1 đã xong hết; M1 chạy được trên AWS ngày 25/10. Việc còn lại sau M1:

**Ngọc Anh** (HM 5 xong trước thứ 4, 28/10)
- `api.tf`: đổi HTTP API sang REST API (xóa HTTP API `SpotPool-API` đang rỗng); resource `/jobs`, `/jobs/{id}`, `/jobs/{id}/start` tích hợp `AWS_PROXY`; authorizer `TOKEN` cache 300 giây; `deployment`, `stage`, `lambda_permission`; CORS.
- Lambda `POST /users`, `POST /users/me/rotate-key`, `POST /jobs/{id}/cancel` (mục 4).
- `/start`: giữ credit bằng `TransactWriteItems` (mục 4); job đã `QUEUED` thì ghi lại manifest; đổi `Decimal` trong `params` về số trước khi `json.dumps` (job video có `params.height` cần việc này).
- `POST /jobs`: `costCap` phải là số nguyên dương. Không chặn thì `costCap` âm sẽ cộng credit cho người dùng khi giữ credit.
- `GET /jobs/{id}`: thêm `interrupted`, `queuedAt`, `startedAt`, `finishedAt`, `chargedCredit` (mục 4).
- Xóa `services/api/db_schema.md` (schema cũ, khác mục 6).
- Hạng mục 8: image `spotpool-video` trong `job-templates/video/` (FFmpeg, ra MP4, `params.height` mặc định 720).
- Hạng mục 14: billing, nạp thêm theo quy tắc job `PAUSED` ở mục 8; trang hóa đơn.
- Hạng mục 15: `GET /admin/overview` theo response ở mục 4 (quyền `ec2:DescribeInstances`, `sqs:GetQueueAttributes`, `ce:GetCostAndUsage`; chi phí AWS lưu ở `cache/aws-cost.json`, không gọi Cost Explorer mỗi lần tải trang) và một trang Tổng quan quản trị; các mục Máy xử lý, Hàng đợi, Doanh thu riêng để hướng mở rộng.

**Khuê** (HM 12 phần lõi xong trước M2, chủ nhật 1/11)
- `worker/agent.py` theo quy tắc job `PAUSED` ở mục 8: nhận message của job `PAUSED` thì chuyển task sang `PAUSED`; nhịp tim thấy job `PAUSED` thì kết thúc lần chạy là `PAUSED`; giao dịch kết thúc chấp nhận job `PAUSED`.
- Hạng mục 12: xử lý thu hồi Spot.
- Hạng mục 13: dựng app React trong `web/`, các trang người dùng (gọi REST API của Ngọc Anh). Lambda `GET /jobs`, `GET /users/me` (mục 4) trong `services/api/`, thêm resource vào `api.tf` theo mẫu các endpoint đã có.

**Nghĩa**
- Hạng mục 9: test worker chạy Python 3.13 trên máy thật (user data đã đổi).
- Hạng mục 10 (xong trước thứ 4, 28/10), 11: ASG Spot nhiều AZ (`c7i-flex.large`, `m7i-flex.large`), co giãn theo backlog, giờ hoạt động gói Ổn định theo mục 13.
- `iam.tf`: worker chỉ được dùng bảng `Jobs`, `Tasks`, `Usage` và hàng đợi `sqs-ondemand`, `sqs-spot`.
- Hạng mục 16: bật cost allocation tag `Project` sớm (Cost Explorer chỉ có dữ liệu từ ngày bật).
- `web.tf`: bucket `web-<accountId>` và CloudFront.
