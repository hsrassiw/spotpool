"""Đếm số nguyên tố nhỏ hơn 2.000.000 bằng sàng Eratosthenes."""
import sys
N = 2_000_000
sieve = bytearray([1]) * N
sieve[0] = sieve[1] = 0
for i in range(2, int(N ** 0.5) + 1):
    if sieve[i]:
        sieve[i * i::i] = bytearray(len(range(i * i, N, i)))
print(f"Số nguyên tố nhỏ hơn {N}: {sum(sieve)}")
print("Đã đếm xong.", file=sys.stderr)
