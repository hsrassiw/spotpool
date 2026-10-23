FROM python:3.13-slim-bookworm

# Chạy được với filesystem chỉ đọc; xuất kết quả không qua bộ đệm Python.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

RUN mkdir -p /input /output && chown 65534:65534 /output

WORKDIR /tmp
USER 65534:65534

# Worker truyền: /input/payload /output/result.
# Đối số sau chuỗi lệnh là $0; hai đường dẫn lần lượt là $1 và $2.
# exec giữ nguyên exit code của script; stderr được worker thu làm log.
ENTRYPOINT ["sh", "-c", "if [ \"$#\" -ne 2 ]; then echo 'Usage: spotpool-script INPUT OUTPUT' >&2; exit 2; fi; exec python -B -u -- \"$1\" > \"$2\"", "spotpool-script"]
