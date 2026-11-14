"""Nhân hai ma trận 120x120 số nguyên và in vết (trace) của kết quả."""
n = 120
a = [[(i * j) % 7 for j in range(n)] for i in range(n)]
b = [[(i + j) % 5 for j in range(n)] for i in range(n)]
c = [[sum(a[i][k] * b[k][j] for k in range(n)) for j in range(n)] for i in range(n)]
print("Vết của A×B:", sum(c[i][i] for i in range(n)))
