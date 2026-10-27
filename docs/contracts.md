# Hợp đồng giữa các thành phần SpotPool

Bản 1 – 18/10/2026, chốt trong buổi họp nhóm.
Bản 2 – 25/10/2026: sau M1. Chuyển mẫu job video sang Khuê, thêm cổng web qua CloudFront, chốt API Gateway REST, cập nhật phụ lục.
Bản 3 – 25/10/2026, chốt trong buổi họp sau M1. Đổi việc: Khuê làm trang người dùng (HM 13), Ngọc Anh làm trang quản trị (HM 15) và mẫu video (HM 8). Chốt luồng job hết trần (mục 8, 10), tỷ giá credit và giờ hoạt động gói Ổn định (mục 13). Worker dùng Python 3.13.

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
2. Kiểm tra đủ file trên S3 bằng `HeadObject`. Thiếu thì trả 400 kèm danh sách file thiếu.
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
     "interruptCount": 0, "resultUrl": "https://…", "logUrl": "https://…"}
  ]
}
```

`interrupted = true` khi có task đang `QUEUED` hoặc `RUNNING` mà `interruptCount > 0`. Khi đó web hiện "bị gián đoạn, đang chạy tiếp".

### POST /jobs/{id}/cancel

- `Jobs.status = CANCELLED`, điều kiện trạng thái hiện tại là `UPLOADING`, `QUEUED`, `RUNNING` hoặc `PAUSED`.
- Mọi task `QUEUED`, `RUNNING`, `PAUSED` chuyển sang `CANCELLED`. Task đang chạy sẽ bị worker dừng ở lần gửi nhịp tim kế tiếp (tối đa 30 giây).
- Message còn trong hàng đợi để nguyên, worker tự xóa khi thấy task đã kết thúc.

### POST /jobs/{id}/topup

Request: `{"amount": 5000}`.

- Một `TransactWriteItems`: `Users` (`creditAvailable -= amount`, `creditHeld += amount`, điều kiện `creditAvailable >= amount`) và `Jobs` (`costCap += amount`, điều kiện trạng thái là `QUEUED`, `RUNNING` hoặc `PAUSED`).
- Nếu job đang `PAUSED`: `Jobs.status = QUEUED`, mọi task `PAUSED` chuyển sang `QUEUED` và được gửi lại message vào hàng đợi ghi trong `Tasks.queue`.

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

Khi lần chạy kết thúc, ghi **trong một `TransactWriteItems`** để không bao giờ cộng trùng:
1. `Tasks`: trạng thái mới, `resultKey`, `logKey`, `finishedAt`, `error` (điều kiện `owner` là mình và `status = RUNNING`).
2. `Jobs`: cộng `usedVcpuSeconds` (trừ khi bị thu hồi). Task kết thúc hẳn (`SUCCEEDED` hoặc `FAILED`) thì `tasksRemaining − 1` và `tasksSucceeded + 1` hoặc `tasksFailed + 1`. Điều kiện: job đang `QUEUED`, `RUNNING` hoặc `PAUSED`. Phải chấp nhận `PAUSED`, vì task chạy song song có thể xong sau khi task khác đã làm job hết trần; nếu từ chối thì task kẹt ở `RUNNING` mãi.
3. `Usage`: thêm một dòng (điều kiện chưa tồn tại).

Gọi lại giao dịch thì dùng cùng `ClientRequestToken`. Giao dịch bị từ chối mà task đã ở đúng trạng thái mình định ghi thì coi như đã ghi xong.

Sau giao dịch:
- Thành công hoặc lỗi: thử chuyển job sang `SUCCEEDED`, không được thì thử `FAILED` (điều kiện ở mục 7.1), rồi xóa message.
- Hết trần: job chuyển `PAUSED`, xóa message.
- Spot bị thu hồi: ngừng nhận việc mới, dừng container, task chuyển `QUEUED`, `interruptCount + 1`. Nếu `interruptCount > 2` thì `Tasks.queue = ondemand`, gửi nguyên văn message sang `sqs-ondemand` rồi xóa message ở `sqs-spot`. Ngược lại đặt visibility về 0 để máy khác lấy.

Quy tắc khi job `PAUSED`: mọi task chưa kết thúc của job phải ở `PAUSED`, không còn task `QUEUED` hay `RUNNING`. Vì vậy worker nhận message của job đã `PAUSED` thì chuyển task `QUEUED` sang `PAUSED` rồi mới xóa message (bước 1 ở trên), và nạp thêm (mục 4) chỉ cần gửi lại các task `PAUSED`. Task bị dừng vì hết trần chạy lại từ đầu sau khi nạp thêm; không lưu trạng thái giữa chừng (checkpoint là hướng mở rộng như báo cáo).

Kết quả và log của mỗi lần chạy nằm trong một thư mục riêng, nên lần chạy cũ không ghi đè được kết quả của lần chạy mới:
- `results/{userId}/{jobId}/{taskId}/{attempt}/result`
- `results/{userId}/{jobId}/{taskId}/{attempt}/log.txt`: stdout và stderr của container, cắt ở 10 MB. Luôn tải log lên, kể cả khi lỗi.

Lambda DLQ nhận message từ `sqs-dlq`:
- Task đang `QUEUED`, hoặc `RUNNING` mà đã mất nhịp tim: chuyển `FAILED` (`tasksRemaining − 1`, `tasksFailed + 1` trong cùng giao dịch), rồi thử kết thúc job.
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
  <image> /input/payload /output/result
```

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
| `PAUSED` (bị cắt vì hết trần, kể cả bị dừng vì task khác đã làm job hết trần) | có | có. Sau khi nạp thêm, task chạy lại từ đầu và lần chạy mới tính tiền riêng |
| `FAILED` (lỗi của job) | có | miễn phí nếu còn lượt lỗi miễn phí trong ngày, hết lượt thì tính |
| `INTERRUPTED` (Spot bị thu hồi) | không | không, nền tảng chịu |
| Mất nhịp tim, bị hủy giữa chừng | không có dòng `Usage` | không |

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
- Thêm endpoint: `POST /users`, `POST /users/me/rotate-key`, `POST /jobs/{id}/start`, `GET /jobs`, `POST /credits`, `GET /admin/overview`.
- Số lần bị thu hồi lưu trong bảng `Tasks` chứ không nằm trong message SQS (Bảng 2.2, giao diện 9).
- Thời gian chạy tối đa do worker tính từ ngân sách còn lại lúc bắt đầu chạy, không nằm trong message, vì nó thay đổi khi người dùng nạp thêm.
- Thêm Lambda xử lý `sqs-dlq` và Lambda billing chạy theo DynamoDB Stream của `Jobs`.
- Cổng web đặt sau CloudFront thay vì mở thẳng từ S3, vì static website hosting của S3 chỉ có HTTP.
- Máy worker cài Docker và nạp worker agent bằng user data khi khởi động, thay cho Custom AMI.

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
- Hạng mục 15: `GET /admin/overview` (quyền `ec2:DescribeInstances`, `sqs:GetQueueAttributes`, `ce:GetCostAndUsage`; chi phí AWS cập nhật mỗi ngày một lần, không gọi Cost Explorer mỗi lần tải trang) và trang quản trị.

**Khuê** (HM 12 phần lõi xong trước M2, chủ nhật 1/11)
- `worker/agent.py` theo quy tắc job `PAUSED` ở mục 8: nhận message của job `PAUSED` thì chuyển task sang `PAUSED`; nhịp tim thấy job `PAUSED` thì kết thúc lần chạy là `PAUSED`; giao dịch kết thúc chấp nhận job `PAUSED`.
- Hạng mục 12: xử lý thu hồi Spot.
- Hạng mục 13: dựng app React trong `web/`, các trang người dùng (gọi REST API của Ngọc Anh).

**Nghĩa**
- Hạng mục 9: test worker chạy Python 3.13 trên máy thật (user data đã đổi).
- Hạng mục 10 (xong trước thứ 4, 28/10), 11: ASG Spot nhiều AZ (`c7i-flex.large`, `m7i-flex.large`), co giãn theo backlog, giờ hoạt động gói Ổn định theo mục 13.
- `iam.tf`: worker chỉ được dùng bảng `Jobs`, `Tasks`, `Usage` và hàng đợi `sqs-ondemand`, `sqs-spot`.
- Hạng mục 16: bật cost allocation tag `Project` sớm (Cost Explorer chỉ có dữ liệu từ ngày bật).
- `web.tf`: bucket `web-<accountId>` và CloudFront.
