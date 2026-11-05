import { ApiError } from './client.js';
import { validateReadUrl } from './jobDetail.js';

export const LOG_PREVIEW_BYTES = 1024 * 1024;

// Client riêng cho S3: tuyệt đối không truyền Authorization của SpotPool.
export async function readLog(url, { signal, fetchImpl = globalThis.fetch, maxBytes = LOG_PREVIEW_BYTES, timeoutMs = 15000 } = {}) {
  validateReadUrl(url);
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  let timedOut = false;
  let reader;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    if (controller.signal.aborted) throw new DOMException('Cancelled', 'AbortError');
    const response = await fetchImpl(url, { method: 'GET', signal: controller.signal, credentials: 'omit', cache: 'no-store',
      redirect: 'error', referrerPolicy: 'no-referrer' });
    if (!response.ok) throw new ApiError('Chưa đọc được log. Link có thể đã hết hạn hoặc file không còn được lưu.', { code: 'FILE_HTTP', status: response.status });
    if (!response.body) return { text: '', truncated: false };
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parts = [];
    let bytes = 0;
    let truncated = false;
    while (true) {
      const { value, done } = await reader.read();
      if (controller.signal.aborted) throw new DOMException('Cancelled', 'AbortError');
      if (done) break;
      const take = Math.min(value.byteLength, maxBytes - bytes);
      parts.push(decoder.decode(value.subarray(0, take), { stream: true }));
      bytes += take;
      if (bytes >= maxBytes) {
        truncated = value.byteLength > take || response.headers.get('content-length') !== String(maxBytes);
        await reader.cancel();
        break;
      }
    }
    parts.push(decoder.decode());
    return { text: parts.join(''), truncated };
  } catch (error) {
    if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
    if (timedOut) throw new ApiError('Đọc log quá thời gian. Vui lòng thử lại.', { code: 'FILE_TIMEOUT' });
    if (error instanceof ApiError) throw error;
    throw new ApiError('Không đọc được log. Hãy kiểm tra kết nối rồi thử lại.', { code: 'FILE_NETWORK' });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    try { await reader?.cancel(); } catch { /* Stream đã đóng hoặc đã bị hủy. */ }
    reader?.releaseLock();
  }
}
