# Thiết kế Cơ sở dữ liệu DynamoDB - SpotPool

## 1. Bảng Users (Quản lý tài khoản & credit)
* **Partition Key (PK):** `userId` (String) - ID hoặc email của người dùng.
* **Attributes:**
  * `apiKeyHash` (String): Mã băm SHA-256 của khóa API dùng để xác thực.
  * `creditBalance` (Number): Số dư credit hiện tại trong tài khoản.
  * `limits` (Map): 
    * `maxConcurrentJobs`: Số job tối đa được chạy đồng thời.
    * `freeErrorQuotaPerDay`: Số job lỗi được miễn phí mỗi ngày.

## 2. Bảng Jobs (Quản lý vòng đời công việc)
* **Partition Key (PK):** `jobId` (String) - UUID định danh job.
* **Attributes:**
  * `userId` (String): Chủ sở hữu job.
  * `status` (String): Trạng thái (`pending`, `running`, `interrupted`, `completed`, `failed`, `paused`).
  * `packageType` (String): Gói dịch vụ (`stable` hoặc `saving`).
  * `costCap` (Number): Trần chi phí tối đa do người dùng đặt.
  * `inputUri` (String): Đường dẫn S3 file đầu vào.
  * `outputUri` (String): Đường dẫn S3 file kết quả.
  * `createdAt` (String): Timestamp thời điểm nộp job.

## 3. Bảng Usage (Đo lường vCPU-giây và tính phí)
* **Partition Key (PK):** `jobId` (String).
* **Attributes:**
  * `userId` (String).
  * `vCpuSeconds` (Number): Số vCPU-giây được cấp và tiêu thụ.
  * `cost` (Number): Chi phí tính tiền thực tế tương ứng.
  * `updatedAt` (String).