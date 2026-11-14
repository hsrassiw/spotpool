"""Chiếm trọn 1 vCPU trong 90 giây (vòng lặp tính toán), không dùng checkpoint."""
import sys, time
start = time.monotonic(); count = 0; mark = 0
while time.monotonic() - start < 90:
    count += sum(i * i for i in range(1000))
    if time.monotonic() - start >= mark:
        print(f"Đang chạy giây {mark}", file=sys.stderr, flush=True); mark += 15
print(f"Xong sau 90 giây, {count} phép tính")
