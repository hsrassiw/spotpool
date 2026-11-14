"""Job script thử checkpoint của SpotPool.

Tính tổng bình phương 0² + 1² + ... + n² theo 180 bước, mỗi bước khoảng 1 giây,
nên job chạy khoảng 3 phút. Sau mỗi bước, trạng thái được ghi vào
$SPOTPOOL_CHECKPOINT_DIR/state.json; cứ 30 bước ghi thêm một file vào thư mục con
nhat-ky/. Nếu máy Spot bị thu hồi giữa chừng, lượt sau đọc lại trạng thái và chạy
tiếp từ bước kế tiếp. Cuối job, script so tổng với công thức n(n+1)(2n+1)/6 và in
ĐÚNG hoặc SAI.

Ghi file an toàn: ghi ra *.tmp rồi os.replace, worker bỏ qua file *.tmp.
stdout là file kết quả, stderr hiện trong log trên web.
"""

import json
import os
import sys
import time

STEPS = 180
CHUNK = 200_000
PACE_SECONDS = 1

folder = os.environ.get("SPOTPOOL_CHECKPOINT_DIR")


def now(fmt="%H:%M:%S"):
    """Giờ Việt Nam (UTC+7); container chạy theo UTC."""
    return time.strftime(fmt, time.gmtime(time.time() + 7 * 3600))


def log(message):
    print(f"[{now()}] {message}", file=sys.stderr)


def write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def load():
    if folder:
        path = os.path.join(folder, "state.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
    return {"step": 0, "total": 0, "runs": []}


def save(state):
    if folder:
        write_atomic(os.path.join(folder, "state.json"), json.dumps(state, ensure_ascii=False))


if not folder:
    log("Không có SPOTPOOL_CHECKPOINT_DIR: chạy không lưu checkpoint.")

state = load()
state["runs"].append({"luot": len(state["runs"]) + 1, "tu_buoc": state["step"] + 1,
                      "luc": now("%Y-%m-%d %H:%M:%S")})
if state["step"]:
    files = sorted(os.listdir(os.path.join(folder, "nhat-ky"))) if os.path.isdir(
        os.path.join(folder, "nhat-ky")) else []
    log(f"KHÔI PHỤC: lượt {len(state['runs'])}, chạy tiếp từ bước {state['step'] + 1}/{STEPS}, "
        f"tổng tạm {state['total']}; nhat-ky/ có {len(files)} file: {', '.join(files) or '(trống)'}")
else:
    log(f"Bắt đầu từ bước 1/{STEPS}.")
save(state)

for step in range(state["step"], STEPS):
    low = step * CHUNK
    state["total"] += sum(i * i for i in range(low, low + CHUNK))
    state["step"] = step + 1
    save(state)
    if state["step"] % 30 == 0:
        write_atomic(os.path.join(folder, "nhat-ky", f"buoc-{state['step']:03d}.txt"),
                     f"bước {state['step']}, tổng {state['total']}, lúc {now()}\n") \
            if folder else None
        log(f"Xong bước {state['step']}/{STEPS}.")
    time.sleep(PACE_SECONDS)

n = STEPS * CHUNK - 1
expected = n * (n + 1) * (2 * n + 1) // 6
ok = state["total"] == expected
log(f"Xong. Kết quả {'ĐÚNG' if ok else 'SAI'}.")

print(f"Tổng bình phương từ 0 đến {n}: {state['total']}")
print(f"Theo công thức: {expected} -> {'ĐÚNG' if ok else 'SAI'}")
print(f"Số lượt chạy: {len(state['runs'])}")
for run in state["runs"]:
    print(f"  Lượt {run['luot']}: bắt đầu từ bước {run['tu_buoc']} lúc {run['luc']}")
