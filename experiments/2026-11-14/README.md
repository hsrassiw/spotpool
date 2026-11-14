# Thực nghiệm chạy lại ngày 14/11/2026

Chạy lại các thực nghiệm của báo cáo chương 3 (mục 3.2, lần đầu ngày 11/11 và 13/11) trên bản nộp (`main` sau PR #46), cộng thêm hai kịch bản mới: job `.zip` nhiều file và hết trần chi phí với script có checkpoint. Lần thu hồi Spot thứ 3 chạy sau PR #49 (thay máy ngay khi nhận thông báo). PR #47 và #48 chỉ đổi tầng API (vùng của Lambda, cache authorizer, kiểu file log), không ảnh hưởng các số đo.

Mọi job được gửi qua API công khai như người dùng thật; mỗi kịch bản dùng một tài khoản mới có 50.000 credit. Mốc thời gian lấy từ bảng Jobs, Tasks và Usage trong DynamoDB, theo giờ Việt Nam. Gói Ổn định chạy trên c7i-flex.large; gói Tiết kiệm được cấp m7i-flex.large, riêng lần co giãn 4 máy được cấp 3 máy c7i-flex.large và 1 máy m7i-flex.large trên cả 3 AZ.

Số liệu thô của từng job (bản ghi Jobs, Tasks, Usage, số dư tài khoản, log và kết quả) nằm trong [ket-qua.json](ket-qua.json).

## Chức năng

Gói Ổn định có sẵn 1 máy, trừ dòng cuối.

| Kịch bản | Ngày 11/11 | Ngày 14/11 |
| --- | --- | --- |
| Script 3 file: 2 tính toán, 1 cố ý lỗi | 2 task SUCCEEDED, 1 FAILED, job FAILED, chờ 5 s | Như cũ, chờ 5 s; log task lỗi ghi đúng thông báo (job d4719786) |
| Nén video 1920×1080 20 s có âm thanh và 1280×960 15 s, `height = 720` | 1280×720 và 960×720, xong sau 29 s | 1280×720 (H.264 yuv420p, AAC) và 960×720, chờ 1 s, xong sau 25 s; 32 vCPU-giây, 54 credit (job 1d7f159e) |
| Job `.zip` nhiều file (mới) | — | `project_hinh.zip` gồm package `hinh/`, `tien_ich.py` và `__main__.py` import lẫn nhau, chạy đúng; chờ 1 s, 2 credit (job 5d1c373c) |
| Hết trần: trần 50 credit, script 90 s không checkpoint | PAUSED sau 31 vCPU-giây; nạp 300 credit thì chạy lại sau 2 s; 122 vCPU-giây, 204 credit | Như cũ: PAUSED sau 31 vCPU-giây, nạp 300 credit thì chạy lại ngay; 122 vCPU-giây, 204 credit (job 1dd47118) |
| Hết trần với script có checkpoint (mới): `test_checkpoint.py`, trần 50 credit | — | PAUSED sau 31 vCPU-giây (bước 29/180); nạp 300 credit thì chạy tiếp từ bước 30, kết quả đúng; tổng 31 + 154 = 185 vCPU-giây, 309 credit (job b5795844) |
| Hủy job: 2 script dài, hủy sau 15 s | CANCELLED ngay; task dừng trong 30 s; 62 vCPU-giây, 0 credit | CANCELLED ngay; task dừng sau 14 s; 62 vCPU-giây, 0 credit (job 9350a862) |
| Gói Tiết kiệm khi cụm 0 máy: script 10 phút và script ngắn | Alarm bật máy sau 2 phút 37 giây, task chạy sau 3 phút 38 giây | Auto Scaling bật máy sau 3 phút 22 giây, task chạy sau 4 phút 37 giây (job 254fa1b1) |

Khi chạy liền không hết trần, `test_checkpoint.py` tốn 184 vCPU-giây (job d8423e4e ngày 13/11). Nhờ checkpoint, lần hết trần giữa chừng chỉ tốn thêm 1 vCPU-giây. Nếu không có checkpoint, script phải chạy lại cả 180 bước, ước tính 31 + 184 = 215 vCPU-giây (359 credit).

Job b5795844 phải chờ 347 s vì chạy lúc 2 giờ sáng, khi gói Ổn định không có máy chờ (máy chờ chỉ bật 8h–23h). Mẫu này không tính vào thống kê thời gian chờ.

## Thu hồi máy Spot

Thông báo thu hồi được giả lập bằng IMDS giả và `experiments/spot_interrupt.sh` (tài khoản gói Free không dùng được AWS FIS). Máy bị tắt thật bằng `terminate-instances`, máy thay thế do Auto Scaling bật thật.

**Lần 1, 00:40:08.** Máy m7i-flex.large ở AZ 1a đang chạy cùng lúc hai task: script 10 phút không checkpoint (job 254fa1b1) và `test_checkpoint.py` (job dce9c55a, đã xong bước 90/180).

- Worker thấy thông báo lúc 00:40:11, lưu checkpoint lần cuối và trả cả hai task về hàng đợi lúc 00:40:12.
- Máy tắt lúc 00:42:13, Auto Scaling bật máy thay lúc 00:42:32. Cả hai task chạy lại lúc 00:43:47, tức **3 phút 39 giây** sau thông báo (11/11: 4 phút 47 giây; 13/11: 5 phút 8 giây).
- `test_checkpoint.py` khôi phục 4 file (`state.json` và `nhat-ky/buoc-030`, `060`, `090`), chạy tiếp từ bước 91/180 và cho kết quả đúng. Lượt 1 chạy 92 vCPU-giây, không tính tiền. Lượt 2 chạy 96 vCPU-giây, tính 80 credit.
- Script không checkpoint chạy lại từ đầu. Lượt bị gián đoạn (96 vCPU-giây) không tính tiền. Job tính 607 vCPU-giây (603 của lượt chạy lại và 4 của task ngắn), tức ⌈607 × 3000 / 3600⌉ = 506 credit.

**Lần 2, 00:55:43.** Job video 34255557: video 150 s nén về 720p, chia 7 đoạn 20 giây.

- Worker thấy thông báo lúc 00:55:47. Lúc 00:55:48, checkpoint lần cuối (`video.meta` và 3 đoạn đã nén, 16 MB) đã lên S3 và task đã về hàng đợi.
- Task chạy lại lúc 00:59:39, tức **3 phút 56 giây** sau thông báo (13/11: 4 phút 54 giây), khôi phục checkpoint và tiếp tục từ đoạn 4/7.
- Video ra 1280×720, H.264 và AAC, đủ 4500/4500 khung hình, dài 150,00 s. Lượt 1 chạy 74 vCPU-giây, không tính tiền. Lượt 2 chạy 95 vCPU-giây, tính 80 credit.

Hai lần trên cho thấy phần lớn thời gian chờ nằm ở chỗ Auto Scaling vẫn tính máy đã nhận thông báo là InService cho tới khi máy bị tắt (khoảng 2 phút), rồi mới bật máy thay. Nhóm sửa ở PR #49: khi nhận thông báo, worker tự tách khỏi ASG bằng `DetachInstances` với `ShouldDecrementDesiredCapacity=False`, nên ASG bật máy thay ngay; máy cũ vẫn chạy nốt tới khi bị tắt.

**Lần 3, 04:43:05, sau khi sửa.** Job 05d76194: `test_checkpoint.py` ở gói Tiết kiệm, máy c7i-flex.large ở AZ 1b.

| Mốc | Thời điểm | Sau thông báo |
| --- | --- | --- |
| Thông báo thu hồi | 04:43:05 | 0 s |
| Worker trả task về hàng đợi và tách khỏi ASG | 04:43:06 | 1 s |
| ASG bật máy thay (m7i-flex.large, AZ 1c) | 04:43:10 | 5 s |
| Task chạy lại trên máy mới | 04:44:23 | **1 phút 18 giây** |
| Job xong | 04:45:49 | |

- Trước khi sửa, task chạy lại sau 3 phút 39 giây và 3 phút 56 giây (lần 1 và 2 ở trên), 4 phút 47 giây ngày 11/11, 5 phút 8 giây ngày 13/11.
- Lượt 2 khôi phục checkpoint, chạy tiếp từ bước 99/180, kết quả đúng. Lượt 1 chạy 101 vCPU-giây, không tính tiền. Lượt 2 chạy 87 vCPU-giây, tính 73 credit.
- Máy cũ bị tắt lúc hết 2 phút như trước; không còn máy nào ngoài ASG chạy sót.

## Co giãn

Hai job được gửi cùng lúc, mỗi gói một job. Mỗi job có 12 task, mỗi task chiếm trọn 1 vCPU trong 60 giây.

| | Lần 1, Ổn định | Lần 1, Tiết kiệm | Lần 2, Ổn định | Lần 2, Tiết kiệm |
| --- | --- | --- | --- | --- |
| Máy có sẵn | 1 | 1 | 4 | 4 |
| Chờ ngắn nhất / trung vị / dài nhất | 4 / 156 / 308 s | 5 / 157 / 308 s | 5 / 5 / 66 s | 5 / 5 / 66 s |
| Thời gian job, ngày 14/11 | 368 s | 369 s | 126 s | 126 s |
| Thời gian job, ngày 11/11 | 365 s | 606 s (do lỗi đã sửa) | 126 s | 127 s |
| vCPU-giây / hóa đơn | 732 / 1220 credit | 732 / 610 credit | 732 / 1220 credit | 732 / 610 credit |
| Task phải chạy lại | 0 | 0 | 0 | 0 |

- **Lần 1** (nộp 01:04:06). Auto Scaling quyết định tăng lên 4 máy sau 5 phút 6 giây ở gói Ổn định và 5 phút 48 giây ở gói Tiết kiệm (ngày 11/11: 4 phút 55 giây và 5 phút 46 giây). Lúc đó hàng đợi gần hết việc, nên các máy mới không kịp nhận task. Job: 0210db12 (Ổn định), 76efb998 (Tiết kiệm).
- **Lần 2** (nộp 01:21:23). 8 task chạy ngay, 4 task còn lại chạy khi có suất trống. Job: 12b43630 (Ổn định), aa70dc11 (Tiết kiệm).

## Thời gian chờ

Tính từ lúc job vào hàng đợi tới khi task đầu tiên chạy.

- **Gói Ổn định, có máy sẵn:** 17 job ngày 14/11, chờ dài nhất 5 s. Gồm 5 job chức năng, 2 job co giãn và 10 job mẫu; 10 job mẫu chờ 1–2 s. Gộp với ngày 11/11 là 25 job, chờ dài nhất 5 s.
- **Gói Tiết kiệm, cụm đang 0 máy:** 16 job ngày 14/11: job 254fa1b1 (277 s) và 15 job mẫu của `spot_loop.sh` từ 01:51 đến 07:33 (`sample-spot-*` trong `ket-qua.json`). Chờ ngắn nhất / trung vị / dài nhất là 277 / 353 / 375 s, đều trong mục tiêu 15 phút. Phần lớn thời gian là chờ CloudWatch alarm thấy hàng đợi có việc rồi Auto Scaling bật máy Spot. Gộp với 3 job ngày 11/11 là 19 job, chờ dài nhất 375 s.

## Thử warm pool cho gói Ổn định

Gói Ổn định giữ 1 máy On-Demand chạy sẵn 8h–23h để đạt mục tiêu chờ ≤ 1 phút, tốn 15 h × 0,1028 USD = 1,54 USD mỗi ngày. Warm pool của Auto Scaling giữ máy đã cài sẵn ở trạng thái stopped, khi đó chỉ trả tiền ổ đĩa. Phép thử này đo xem bật lại máy như vậy nhanh tới đâu.

`warm_test.sh` bật 1 máy từ launch template `spotpool-worker-ondemand` (không gắn vào ASG), đo lúc service `spotpool-worker` active qua SSM, rồi stop/start 3 lần và xóa máy. Log: `warm-pool.log`.

| Lần | Từ lệnh bật/start tới khi worker active |
|---|---|
| Bật mới (cài Docker, kéo image, tải code) | 72 s |
| Start lại từ stopped, lần 1 / 2 / 3 | 8 / 10 / 9 s |

Image Docker vẫn còn trên ổ đĩa nên start lại không phải kéo lại. Worker báo lỗi `SetInstanceProtection` vì máy thử không thuộc ASG; lỗi này được bắt và worker vẫn nhận việc.

Warm pool chỉ cắt phần bật máy (72 s còn khoảng 10 s). Thời gian chờ khi cụm 0 máy chủ yếu nằm ở bước CloudWatch alarm thấy hàng đợi có việc (gói Tiết kiệm chờ 277–375 s, trong đó bật máy chỉ khoảng 72 s). Muốn bỏ máy chạy sẵn mà vẫn chờ ≤ 1 phút thì còn phải để API tự tăng desired capacity khi nộp job lúc cụm đang 0 máy, và worker không nhận việc trong lúc máy đang ở warm pool. Hai việc này chưa làm.

## Chạy lại

Cần Python 3.11+ với `boto3` và quyền đọc DynamoDB của tài khoản AWS. Nhóm chạy trên Windows bằng Git Bash.

```bash
export SPOTPOOL_API=https://<api-id>.execute-api.ap-southeast-1.amazonaws.com/prod
python run.py func-script                  # tương tự: func-video, func-zip, func-cap, func-cancel
python run.py func-cap test_checkpoint.py func-cap-checkpoint
python run.py burst burst1                 # trước đó đặt mỗi cụm 1 máy (lần 2: 4 máy)
python run.py sample ondemand
bash spot_loop.sh                          # lấy mẫu gói Tiết kiệm khi cụm 0 máy, tới 07:30
python run.py stats                        # tóm tắt kết quả đã lưu
python run.py merge                        # gộp results/ thành ket-qua.json
```

Thu hồi Spot: nộp job gói Tiết kiệm bằng `submit-only`, đợi task chạy bằng `wait-running <jobId> <giây>`, chạy `bash experiments/spot_interrupt.sh` ở thư mục gốc repo, rồi `watch <jobId>`.

Các video thử không đưa lên repo. Tạo lại bằng ffmpeg:

```bash
ffmpeg -f lavfi -i testsrc2=size=1920x1080:rate=30 -f lavfi -i sine=frequency=440:sample_rate=48000 \
  -t 20 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest inputs/clip_1080p_20s.mp4
ffmpeg -f lavfi -i testsrc=size=1280x960:rate=30 -t 15 -c:v libx264 -pix_fmt yuv420p inputs/clip_960p_15s.mp4
```

`video_150s.mp4` là video 1280×720, 30 khung hình/giây, dài 150 s, có âm thanh AAC; nhóm dùng lại video thử ngày 13/11.
