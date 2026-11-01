# job-templates

Image mẫu đẩy lên ECR, mỗi file đầu vào là một task:

- `script/`: chạy script Python của người dùng (`spotpool-script`). Phụ trách: TV2, đã xong.
- `video/`: nén video bằng FFmpeg (`spotpool-video`). Phụ trách: TV3.

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
