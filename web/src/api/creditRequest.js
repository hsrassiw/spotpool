// Retain uncertain virtual-credit requests across navigation/reload, scoped to the current user.
export function createCreditRequestStore(userId, operation, suppliedStorage) {
  let storage = suppliedStorage;
  if (storage === undefined) { try { storage = globalThis.sessionStorage; } catch { storage = null; } }
  const key = `spotpool:${userId}:${operation}:pending`;
  return {
    load() {
      try {
        const value = JSON.parse(storage?.getItem(key) ?? 'null');
        return value && typeof value.requestId === 'string' && /^[0-9a-f-]{36}$/i.test(value.requestId)
          && Number.isSafeInteger(value.amount) && value.amount > 0 && value.amount <= 100000000
          && typeof value.jobId === 'string' ? value : null;
      } catch { return null; }
    },
    save(value) {
      try {
        if (value) storage?.setItem(key, JSON.stringify({ requestId: value.requestId, amount: value.amount, jobId: value.jobId }));
        else storage?.removeItem(key);
      } catch { /* Memory retry remains available when browser storage is disabled. */ }
    },
  };
}
