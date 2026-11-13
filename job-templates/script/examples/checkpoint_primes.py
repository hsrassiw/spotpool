"""Job script mẫu có checkpoint: đếm số nguyên tố nhỏ hơn 3.000.000 theo 300 khối.

Mỗi khối tính xong thì ghi trạng thái vào $SPOTPOOL_CHECKPOINT_DIR. Nếu máy Spot bị
thu hồi hoặc job hết trần chi phí, lần chạy sau đọc lại trạng thái và làm tiếp từ
khối kế tiếp thay vì từ đầu. Kết quả đúng là 216816.

Cách ghi an toàn: ghi ra file .tmp rồi os.replace, để worker không lưu file ghi dở.
Dòng in ra stdout là kết quả, dòng in ra stderr hiện trong log trên web.
"""

import json
import math
import os
import sys
import time

LIMIT = 3_000_000
BLOCKS = 300
BLOCK = LIMIT // BLOCKS
PACE_SECONDS = 1  # Giãn mỗi khối 1 giây để job chạy khoảng 5 phút, đủ thời gian demo thu hồi.

folder = os.environ.get("SPOTPOOL_CHECKPOINT_DIR")
state_path = os.path.join(folder, "state.json") if folder else None


def load():
    if state_path and os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as handle:
            return json.load(handle)
    return {"block": 0, "count": 0, "resumed": 0}


def save(state):
    if not state_path:
        return
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(tmp, state_path)


def small_primes(limit):
    sieve = bytearray([1]) * (limit + 1)
    sieve[0:2] = b"\x00\x00"
    for n in range(2, math.isqrt(limit) + 1):
        if sieve[n]:
            sieve[n * n::n] = bytearray(len(sieve[n * n::n]))
    return [n for n in range(limit + 1) if sieve[n]]


def count_block(low, high, primes):
    """Sàng đoạn [low, high)."""
    marks = bytearray([1]) * (high - low)
    for p in primes:
        if p * p >= high:
            break
        start = max(p * p, (low + p - 1) // p * p)
        marks[start - low::p] = bytearray(len(marks[start - low::p]))
    if low < 2:
        marks[0:2 - low] = bytearray(2 - low)
    return sum(marks)


state = load()
first_block = state["block"]
if first_block:
    state["resumed"] += 1
    print(f"Tiếp tục từ khối {first_block + 1}/{BLOCKS} (đã đếm {state['count']} số nguyên tố).",
          file=sys.stderr)
else:
    print(f"Bắt đầu từ khối 1/{BLOCKS}.", file=sys.stderr)

primes = small_primes(math.isqrt(LIMIT) + 1)
for block in range(first_block, BLOCKS):
    low = block * BLOCK
    state["count"] += count_block(low, low + BLOCK, primes)
    state["block"] = block + 1
    save(state)
    if (block + 1) % 30 == 0:
        print(f"Xong khối {block + 1}/{BLOCKS}.", file=sys.stderr)
    time.sleep(PACE_SECONDS)

print(f"Số số nguyên tố nhỏ hơn {LIMIT}: {state['count']}")
print(f"Bắt đầu lượt cuối từ khối {first_block + 1}; số lần chạy tiếp từ checkpoint: {state['resumed']}")
