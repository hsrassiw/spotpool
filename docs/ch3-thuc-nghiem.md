# 3.2. Thực nghiệm và đánh giá

Các thực nghiệm chạy ngày 11/11/2026 trên hệ thống đã triển khai (region ap-southeast-1; gói Ổn định chạy trên c7i-flex.large, gói Tiết kiệm được Auto Scaling cấp m7i-flex.large ở mọi lần chạy trong ngày; cả hai loại đều 2 vCPU nên số vCPU-giây và hóa đơn không phụ thuộc loại máy), gọi qua API công khai như người dùng thật. Mỗi kịch bản dùng một tài khoản người dùng mới với 50.000 credit. Thời gian lấy từ bảng Jobs và Tasks (queuedAt, startedAt, finishedAt), chi phí lấy từ hóa đơn từng job (`GET /billing`) và từ AWS Cost Explorer.

Đơn giá dùng trong toàn bộ thực nghiệm là đơn giá đã nêu ở Báo cáo 2: 6000 credit/vCPU-giờ cho gói Ổn định và 3000 credit/vCPU-giờ cho gói Tiết kiệm. Khi quy đổi, nhóm dùng tỷ giá 1 USD = 26.000 credit, cũng là tỷ giá trang quản trị đang dùng.

## 3.2.1. Kiểm thử chức năng đầu cuối

Bảng 3.1. Kết quả các kịch bản chức năng

| Kịch bản | Cách làm | Kết quả |
| --- | --- | --- |
| Job script, gói Ổn định | 3 file: 2 script tính toán, 1 script cố ý thoát với lỗi | 2 task SUCCEEDED, 1 task FAILED, job FAILED. Log của task lỗi ghi đúng thông báo lỗi. Chờ 5 s |
| Nén video, gói Ổn định | 2 video: 1920×1080 dài 20 s (có audio) và 1280×960 dài 15 s; `height = 720` | Đầu ra 1280×720 và 960×720, giữ tỷ lệ khung hình, H.264 yuv420p, AAC. Cả job xong sau 29 s |
| Job gói Tiết kiệm khi cụm đang 0 máy | 1 script dài 6 phút và 1 script ngắn | Alarm bật máy đầu tiên sau 2 phút 37 giây, task bắt đầu chạy sau 3 phút 38 giây |
| Hết trần chi phí | Gói Ổn định, trần 50 credit (đủ 30 giây), script chạy 90 giây | Job chuyển PAUSED sau 31 vCPU-giây, giữ 50 credit. Nạp thêm 300 credit thì job chạy lại sau 2 giây và hoàn thành |
| Hủy job đang chạy | 2 script dài, hủy sau 15 giây | Job CANCELLED ngay. Các task dừng ở nhịp tim kế tiếp (tối đa 30 s); 62 vCPU-giây đã chạy nằm trong hạn mức lỗi miễn phí trong ngày nên tính 0 credit |
| Spot bị thu hồi | Mục 3.2.2 | Đạt |

Trường hợp hết trần cho thấy cách tính tiền đúng với thiết kế. Script bị dừng lúc hết trần sẽ chạy lại từ đầu sau khi nạp thêm, nên job dùng tổng cộng 122 vCPU-giây. Hóa đơn là ⌈122 × 6000 / 3600⌉ = 204 credit, số dư khả dụng giảm đúng 204 (từ 50.000 còn 49.796) và creditHeld về 0 sau khi quyết toán.

## 3.2.2. Thu hồi máy Spot

Tài khoản ở gói Free của AWS không dùng được AWS FIS (lỗi `SubscriptionRequiredException`), nên nhóm giả lập thu hồi bằng một IMDS giả trên chính máy Spot. Dịch vụ này chỉ được cài khi apply với `-var spot_interrupt_test=true`. Worker đọc metadata qua IMDS giả; mọi đường dẫn khác được chuyển tiếp nguyên vẹn tới IMDS thật, riêng `spot/instance-action` trả về thông báo thu hồi giống định dạng của AWS khi có file kích hoạt. Script `experiments/spot_interrupt.sh` tạo file kích hoạt, đợi đủ 2 phút như AWS rồi tắt máy bằng `terminate-instances`.

Đường xử lý của worker (đọc thông báo, ngừng nhận việc, trả việc về hàng đợi) giống hệt khi AWS thu hồi thật. Phần khác duy nhất là nguồn của thông báo. Trường hợp bị thu hồi lần thứ 3 thì chuyển sang hàng đợi On-Demand; trường hợp này chưa chạy trên AWS, mới kiểm tra bằng unit test (`tests/test_worker_spot.py`).

Bảng 3.2. Diễn biến khi thu hồi máy đang chạy task (job 410aaec5)

| Thời điểm | Sự kiện |
| --- | --- |
| 10:46:24 | Hai task bắt đầu chạy trên máy Spot. Task ngắn xong sau 3 giây |
| 10:46:48 | Bật thông báo thu hồi trên máy |
| 10:46:49 | Worker nhận thông báo, ngừng nhận việc mới |
| 10:46:50 | Task dài được trả về hàng đợi, interruptCount = 1 |
| 10:49:04 | Hết 2 phút, máy bị tắt |
| 10:50:32 | Auto Scaling phát hiện máy đã tắt, bật máy thay thế |
| 10:51:35 | Task dài chạy lại từ đầu trên máy mới (attempt = 2) |
| 10:57:38 | Task dài hoàn thành, job SUCCEEDED |

Kết quả:

- Worker phản ứng trong vòng 1 giây sau thông báo, sớm hơn nhiều so với hạn 2 phút.
- Task đã xong trước khi máy bị thu hồi được giữ nguyên, không chạy lại.
- Người dùng không trả tiền cho lần chạy bị gián đoạn. Job được tính 368 vCPU-giây: 364 của lần chạy lại của task dài và 4 của task ngắn, nên hóa đơn là ⌈368 × 3000 / 3600⌉ = 307 credit. Lần chạy bị gián đoạn dài 26 vCPU-giây, được ghi trong bảng Usage với kết quả INTERRUPTED và không được cộng vào.
- Từ lúc có thông báo tới lúc task chạy lại mất 4 phút 47 giây. Máy đã nhận thông báo vẫn tính là InService cho tới khi bị tắt, nên Auto Scaling chỉ bật máy thay thế sau thời điểm đó.

## 3.2.3. Thời gian chờ và khả năng co giãn

Để đo thời gian chờ khi có nhiều việc cùng lúc, nhóm gửi đồng thời hai job, mỗi gói một job. Mỗi job gồm 12 task, mỗi task chiếm trọn 1 vCPU trong 60 giây. Thực nghiệm chạy hai lần: lần 1 mỗi cụm có sẵn 1 máy (2 suất), lần 2 mỗi cụm có sẵn 4 máy (8 suất).

Bảng 3.3. Thời gian chờ của từng task (từ queuedAt tới startedAt) và tổng thời gian job

| | Lần 1, Ổn định | Lần 1, Tiết kiệm | Lần 2, Ổn định | Lần 2, Tiết kiệm |
| --- | --- | --- | --- | --- |
| Máy có sẵn | 1 | 1 | 4 | 4 |
| Chờ ngắn nhất / trung vị / dài nhất | 0 / 182 / 303 s | 0 / 182 / 364 s | 1 / 1 / 61 s | 1 / 1 / 61 s |
| Thời gian job (queuedAt → finishedAt) | 365 s | 606 s | 126 s | 127 s |
| vCPU-giây được tính | 732 | 732 | 732 | 732 |
| Hóa đơn | 1220 credit | 610 credit | 1220 credit | 610 credit |

Ở lần 1, các task chạy lần lượt từng cặp trên một máy. Auto Scaling quyết định tăng lên 4 máy sau 4 phút 55 giây (Ổn định) và 5 phút 46 giây (Tiết kiệm), lúc đó hàng đợi gần hết việc nên các máy mới không kịp nhận task.

Nguyên nhân là chính sách target tracking của AWS chỉ tăng máy khi chỉ số vượt ngưỡng 3 phút liên tiếp, cộng với việc chỉ số SQS lên CloudWatch trễ 2–3 phút. Chiều giảm cũng chậm: target tracking chỉ giảm máy sau 15 phút liên tục dưới ngưỡng, nên các máy thừa chạy rảnh thêm khoảng 15 phút. Với job dài vài chục phút trở lên, độ trễ này nhỏ so với thời gian chạy. Với đợt job ngắn như thực nghiệm thì chưa tận dụng được co giãn.

Ở lần 2, với 4 máy sẵn sàng, 8 task chạy ngay và 4 task còn lại chạy khi có suất trống. Thời gian job giảm từ 365 s xuống 126 s, gần mức lý tưởng 2 × 61 s.

Lần 1 của gói Tiết kiệm có 2 task phải chạy lại do một lỗi được phát hiện trong thực nghiệm (mục 3.2.5), nên job lâu hơn gói Ổn định. Lần 2 chạy sau khi sửa lỗi, cả 24 task đều xong ở lần chạy đầu tiên.

Bảng 3.4. Đối chiếu với mục tiêu thời gian chờ ở Báo cáo 2 (tính đến khi task đầu tiên của job bắt đầu chạy)

| Gói | Mục tiêu | Số job đo | Chờ dài nhất | Đánh giá |
| --- | --- | --- | --- | --- |
| Ổn định | ≤ 1 phút | 8 | 5 s | Đạt |
| Tiết kiệm | ≤ 15 phút | 3 | 218 s (cụm đang 0 máy) | Đạt |

Số job đo còn ít, chưa đủ để kết luận chắc chắn cho mức 95% số job. Mục tiêu được tính theo lúc job bắt đầu chạy. Trong một đợt lớn, các task cuối của job vẫn có thể chờ vài phút (Bảng 3.3, lần 1).

## 3.2.4. Chi phí

### Đơn giá máy thật trên hóa đơn AWS

Theo Cost Explorer, từ 20/10 đến trước ngày 11/11 nhóm dùng 1,81 giờ máy c7i-flex.large On-Demand và 2,57 giờ máy c7i-flex.large Spot.

- Đơn giá thực tế của On-Demand là 0,09778 USD/giờ.
- Đơn giá thực tế của Spot là 0,0492–0,0515 USD/giờ với c7i-flex.large, và 0,0542–0,0589 USD/giờ với m7i-flex.large (ngày 11/11 chạy 2,72 giờ, hết 0,16 USD). Tức là bằng khoảng 50–60% On-Demand.
- Mỗi máy còn tốn 0,005 USD/giờ cho địa chỉ IPv4 công khai.

Tất cả các khoản còn lại (Lambda, API Gateway, DynamoDB, S3, SQS, ổ đĩa EBS) cộng lại khoảng 0,04 USD.

Bảng dưới là chi phí trước khi trừ credit, lấy từ Cost Explorer ngày 12/11. Ngày 11/11 là ngày chạy thực nghiệm, gồm máy On-Demand chạy sẵn trong giờ hoạt động và các lần bật 4 máy mỗi gói ở mục 3.2.3. Số của tháng 11 là số tạm tính vì tháng chưa kết thúc.

| Khoản | Giá trị |
| --- | --- |
| Chi phí EC2 ngày 11/11 | 1,61 USD (máy 1,51 USD; IPv4 công khai 0,08 USD; ổ đĩa EBS 0,02 USD) |
| Tổng chi phí AWS ngày 11/11 | 1,65 USD (gồm 0,03 USD cho các lần gọi Cost Explorer) |
| Tổng chi phí AWS từ 12/10 đến hết 11/11 | 2,04 USD |
| Credit AWS còn lại | 197,95 USD, hết hạn ngày 7/3/2027 |

### So sánh với các cách chạy khác

Bảng 3.5 so sánh chi phí người dùng phải trả cho cùng khối lượng việc ở mục 3.2.3: 12 task, mỗi task 1 vCPU trong 60 giây, tổng 732 vCPU-giây.

- **SpotPool:** lấy từ hóa đơn thật.
- **EC2 tự thuê:** tính theo đơn giá thực tế ở trên, cộng 71 giây khởi động máy. Đây là thời gian đo được từ lúc bật máy tới lúc worker sẵn sàng.
- **Fargate:** tính theo bảng giá ap-southeast-1 (0,05056 USD/vCPU-giờ và 0,00553 USD/GB-giờ), với cấu hình nhỏ nhất cho 1 vCPU là 2 GB. Mỗi task được giả định tính 90 giây, gồm 60 giây chạy và khoảng 30 giây tải image, vì Fargate tính tiền từ lúc bắt đầu tải image.
- **AWS Batch:** không thu phí riêng, chi phí bằng EC2 hoặc Fargate bên dưới.

Bảng 3.5. Chi phí cho 12 task × 60 giây

| Cách chạy | Chi phí | Quy ra credit | Ghi chú |
| --- | --- | --- | --- |
| SpotPool, gói Ổn định | 0,0469 USD | 1220 | Theo hóa đơn |
| SpotPool, gói Tiết kiệm | 0,0235 USD | 610 | Theo hóa đơn |
| EC2 On-Demand tự thuê, 1 máy | 0,0125 USD | 324 | Chạy 366 s + 71 s khởi động; xong sau khoảng 7,3 phút |
| EC2 On-Demand tự thuê, 4 máy | 0,0225 USD | 585 | 4 máy × (126 s + 71 s); xong sau khoảng 3,3 phút |
| Fargate | 0,0185 USD | 481 | 12 task × 90 s, mỗi task 1 vCPU + 2 GB |
| AWS Batch | Bằng EC2 hoặc Fargate | | Cộng thời gian khởi tạo môi trường khi đang 0 máy |

Tự thuê máy hoặc dùng Fargate rẻ hơn giá bán của SpotPool. Điều này khớp với nhận định ở Báo cáo 2: người thành thạo AWS có thể tự dùng AWS Batch với giá gốc. Giá của SpotPool gồm cả phần máy chờ sẵn, phần chạy lại khi bị thu hồi và biên lợi nhuận.

Đổi lại, người dùng SpotPool không cần tài khoản AWS hay thẻ quốc tế, biết trước trần chi phí và không phải tự tắt máy. Gói Tiết kiệm có giá bằng một nửa gói Ổn định đúng như thiết kế. Giá cho 732 vCPU-giây chỉ chênh khoảng 0,001 USD so với 4 máy On-Demand tự thuê.

### Điểm hòa vốn

Bảng 3.6. Điểm hòa vốn theo hệ số sử dụng của cụm

| | Ổn định | Tiết kiệm |
| --- | --- | --- |
| Doanh thu một máy đầy 2 suất trong 1 giờ | 2 × 6000 = 12.000 credit = 0,4615 USD | 2 × 3000 = 6000 credit = 0,2308 USD |
| Chi phí một máy trong 1 giờ (kể cả IPv4) | 0,1028 USD | 0,0639 USD (m7i-flex.large ngày 11/11) |
| Hệ số sử dụng để hòa vốn | 22% | 28% |

Gói Tiết kiệm phụ thuộc vào loại máy mà Auto Scaling chọn theo giá Spot lúc đó. Nếu được cấp c7i-flex.large (khoảng 0,054 USD/giờ, kể cả IPv4) thì hệ số hòa vốn giảm còn khoảng 23%. Nghĩa là cụm chỉ cần bán được khoảng một phần tư số suất là đủ trả tiền máy. Phần còn lại bù cho máy chạy rảnh lúc co giãn, các lần chạy lại và lợi nhuận.

Gói Ổn định có thêm chi phí cố định cho máy chờ sẵn trong giờ hoạt động (8h–23h): 15 giờ × 0,1028 USD = 1,54 USD mỗi ngày, tương đương 40.084 credit. Để bù riêng khoản này, mỗi ngày cần bán khoảng 6,7 vCPU-giờ ở gói Ổn định.

Hai gói có hệ số hòa vốn gần nhau (22% và 23–28%). Vì vậy nhóm giữ nguyên đơn giá 6000 và 3000 credit/vCPU-giờ đã nêu ở Báo cáo 2.

## 3.2.5. Lỗi phát hiện qua thực nghiệm

Thực nghiệm trên AWS phát hiện ba lỗi mà unit test chưa bắt được. Cả ba đã được sửa, có thêm test và đã chạy kiểm tra lại trên AWS cùng ngày.

1. **Hai task của cùng một job kết thúc cùng lúc.** Cả hai cùng ghi vào bản ghi job trong DynamoDB, nên một giao dịch bị hủy với lỗi `TransactionConflict`. Worker coi đây là lỗi thật, nên task phải chạy lại từ đầu sau khi hết visibility timeout. Nếu task cuối cùng của job gặp lỗi này, job có thể bị kẹt ở RUNNING. Người dùng không bị tính tiền hai lần, vì giao dịch bị hủy không ghi gì.
   - Cách sửa: worker thử lại giao dịch khi gặp lỗi xung đột, tối đa 5 lần với cùng ClientRequestToken.
   - Kiểm tra lại: ở lần 2 của mục 3.2.3, cả 24 task đều xong ở lần chạy đầu tiên.
2. **Task đầu tiên trên máy mới bị tính cả thời gian tải image.** Worker để Docker tự tải image khi chạy task đầu tiên, nên thời gian tải bị tính vào vCPU-giây của người dùng, và log của người dùng lẫn dòng tải image.
   - Cách sửa: tải trước cả hai image lúc máy khởi động.
   - Kết quả: cùng một job script, hóa đơn giảm từ 10 xuống 4 credit. Cùng job video, hóa đơn giảm từ 74 xuống 54 credit.
3. **Video đầu vào không phải yuv420p cho ra video yuv444p**, định dạng nhiều trình duyệt và điện thoại không phát được.
   - Cách sửa: mẫu nén video luôn xuất yuv420p.

## 3.2.6. Hạn chế

- Co giãn chậm khi có một đợt nhiều job ngắn: khoảng 5 phút mới tăng máy, khoảng 15 phút mới giảm máy (mục 3.2.3). Hướng cải thiện là thêm một chính sách step scaling dùng alarm 1 điểm dữ liệu, giống alarm bật máy từ 0, để tăng máy sớm hơn.
- Sau khi nhận thông báo thu hồi, máy thay thế chỉ được bật khi máy cũ đã tắt, nên task phải chờ khoảng 5 phút mới chạy lại (mục 3.2.2).
- Thu hồi Spot được giả lập bằng IMDS giả thay cho AWS FIS. Đường xử lý của worker giống hệt, nhưng chưa quan sát được lần thu hồi thật nào từ AWS.
- Số lần đo còn ít và chỉ trong một ngày. Kết quả về thời gian chờ mới mang tính minh họa, chưa đủ để kết luận cho mức 95% số job.
