# job-templates

Image mẫu đẩy lên ECR, mỗi file đầu vào là một task:

- `script/`: chạy script Python của người dùng (`spotpool-script`). Phụ trách: TV2, đã xong.
- `video/`: nén video bằng FFmpeg (`spotpool-video`). Phụ trách: TV3, đã xong.

## Script có checkpoint

Job script được mount thêm thư mục `/checkpoint` (biến `SPOTPOOL_CHECKPOINT_DIR`). Script ghi
trạng thái vào đây thì khi máy Spot bị thu hồi hoặc job hết trần, lần chạy sau tiếp tục từ
trạng thái đó thay vì chạy lại từ đầu. Quy tắc:

- Ghi ra `ten.tmp` rồi `os.replace` sang tên thật, để không bị lưu file ghi dở.
- Được dùng thư mục con (tối đa 8 cấp). Chỉ file thường được lưu, không lưu symlink và thư mục rỗng; tối đa 1000 file, 64 MB.
- Nền tảng lưu sau mỗi nhịp tim 30 giây nếu file đổi, và một lần cuối khi bị thu hồi hoặc hết trần.
  Nếu script ghi file đúng lúc nền tảng đang đóng gói, bản đó bị bỏ và lưu lại ở nhịp sau.
  Khi khôi phục, bản lưu phải khớp mã SHA-256 đã ghi nhận, nếu không script chạy lại từ đầu.
- stdout của lần chạy cuối là kết quả, nên phần kết quả cần giữ qua các lần chạy cũng phải nằm trong checkpoint.

Ví dụ: `script/examples/checkpoint_primes.py` đếm số nguyên tố nhỏ hơn 3.000.000 theo 300 khối,
mỗi khối 1 giây; chạy tiếp từ khối kế tiếp sau khi bị thu hồi. Hợp đồng chi tiết ở `docs/contracts.md` mục 8.1.

## Video có checkpoint

Người dùng không phải làm gì: `spotpool-video` thấy `SPOTPOOL_CHECKPOINT_DIR` thì chia phần hình
thành các đoạn 20 giây (`SPOTPOOL_VIDEO_SEGMENT_SECONDS`), nén xong đoạn nào lưu `seg_NNNN.mp4`
vào thư mục checkpoint. Bị thu hồi hoặc hết trần thì lượt sau chỉ nén các đoạn còn thiếu, rồi
ghép lại (copy hình) và nén âm thanh một lần. Video ngắn hơn 40 giây nén một lượt như cũ.
Log có dòng "Chia video … thành N đoạn", "Xong đoạn i/N" và "Tiếp tục từ đoạn …".

## Video (HM8)

```sh
docker build -t spotpool-video job-templates/video
```

Build từ thư mục gốc repo; đẩy image lên ECR `spotpool-video:latest` trước khi chạy worker.
Worker mount đầu vào chỉ đọc tại `/input/payload`, thư mục đầu ra tại `/output`, rồi
truyền hai đối số `/input/payload /output/result`. Đầu vào và đầu ra không cần phần mở rộng.
Kết quả là MP4 (H.264/libx264, AAC nếu có audio), `faststart`, CRF 23; giữ tỷ lệ khung hình,
chiều rộng chẵn. `params.height` được worker truyền bằng `SPOTPOOL_VIDEO_HEIGHT` (mặc định
720, số nguyên chẵn từ 2 đến 4320). Container chạy bằng UID/GID 65534 nên thư mục output
trên host phải cho UID này ghi. Worker đã chuẩn bị quyền và cờ cách ly container.

Smoke test ngoài container: cài `imageio-ffmpeg` và có POSIX `sh`, rồi chạy
`python -m unittest discover -s tests -p test_video_template.py`.
Test sinh video ngắn, chạy đúng entrypoint và kiểm tra đầu ra MP4/H.264.
Đây không thay thế kiểm thử image trong Docker và chạy thử end-to-end trên AWS.
