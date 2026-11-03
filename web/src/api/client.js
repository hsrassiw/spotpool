export class ApiError extends Error {
  constructor(message, { status = 0, code = 'REQUEST_FAILED', details = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function httpMessage(status, body) {
  if (status === 401 || status === 403) return 'Khóa API không hợp lệ hoặc chưa được cho phép truy cập.';
  if (status === 404) return 'Chưa tìm thấy dữ liệu hoặc endpoint chưa được triển khai. Vui lòng kiểm tra với nhóm.';
  if (status === 429) return 'Có quá nhiều yêu cầu. Vui lòng chờ một chút rồi thử lại.';
  if (status >= 500) return 'Dịch vụ đang gặp lỗi. Vui lòng thử lại sau.';
  if (body?.error === 'Insufficient credit or maxConcurrentJobs limit reached') return 'Không đủ credit hoặc đã đạt hạn mức job đồng thời. Hãy làm mới hồ sơ và thử lại khi đủ điều kiện.';
  if (body?.error === 'Missing files on S3') return 'S3 chưa nhận đủ file. Hãy tải lại các file được đánh dấu rồi mới chạy job.';
  const message = body?.error ?? body?.message;
  return typeof message === 'string' && message.length <= 220 ? message : 'Yêu cầu chưa thực hiện được. Vui lòng kiểm tra lại.';
}

export function createApiClient({ baseUrl, fetchImpl = globalThis.fetch, timeoutMs = 15000 }) {
  async function request(path, { method = 'GET', body, apiKey, signal, sessionSignal } = {}) {
    if (!baseUrl || !/^https?:\/\//i.test(baseUrl)) {
      throw new ApiError('Chưa cấu hình địa chỉ API. Cần thiết lập VITE_API_BASE_URL rồi khởi động lại web.', { code: 'CONFIG' });
    }
    if (!path.startsWith('/') || path.startsWith('//')) throw new ApiError('Đường dẫn API không hợp lệ.', { code: 'CONFIG' });

    const controller = new AbortController();
    const signals = [signal, sessionSignal].filter(Boolean);
    const abort = () => controller.abort();
    for (const source of signals) {
      if (source.aborted) controller.abort();
      source.addEventListener('abort', abort, { once: true });
    }
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);

    try {
      if (controller.signal.aborted) throw new DOMException('Request cancelled', 'AbortError');
      const headers = { Accept: 'application/json' };
      if (apiKey) headers.Authorization = `Bearer ${apiKey}`;
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      const result = await fetchImpl(`${baseUrl.replace(/\/+$/, '')}${path}`, {
        method, headers, signal: controller.signal, cache: 'no-store', credentials: 'omit',
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const text = await result.text();
      if (controller.signal.aborted) throw new DOMException('Request cancelled', 'AbortError');
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch {
        if (result.ok) throw new ApiError('API trả dữ liệu không đúng định dạng.', { code: 'INVALID_RESPONSE' });
      }
      if (!result.ok) {
        const missing = data?.missingFiles;
        const details = Array.isArray(missing) && missing.length <= 100 && missing.every((id) => typeof id === 'string' && /^task-\d{4}$/.test(id))
          ? { missingFiles: [...missing] } : null;
        throw new ApiError(httpMessage(result.status, data), { status: result.status, code: 'HTTP', details });
      }
      return data;
    } catch (error) {
      if (signals.some((source) => source.aborted)) throw new DOMException('Request cancelled', 'AbortError');
      if (timedOut) throw new ApiError('Yêu cầu mất quá nhiều thời gian. Hãy kiểm tra kết quả trước khi gửi lại thao tác.', { code: 'TIMEOUT' });
      if (error instanceof ApiError) throw error;
      throw new ApiError('Không kết nối được tới API. Hãy kiểm tra mạng hoặc cấu hình CORS.', { code: 'NETWORK' });
    } finally {
      clearTimeout(timeout);
      for (const source of signals) source.removeEventListener('abort', abort);
    }
  }
  return { request };
}
