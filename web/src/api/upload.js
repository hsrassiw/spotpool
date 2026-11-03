import { ApiError } from './client.js';
import { validateUploadUrl } from './jobSubmission.js';

// Transport riêng cho S3: không dùng HTTP client có Bearer key của SpotPool.
export function createUploader({ createXhr = () => new XMLHttpRequest(), timeoutMs = 15 * 60 * 1000 } = {}) {
  return function uploadFile(url, file, { signal, onProgress } = {}) {
    return new Promise((resolve, reject) => {
      let xhr;
      let settled = false;
      const abortError = () => new DOMException('Upload cancelled', 'AbortError');
      const finish = (error) => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener('abort', abort);
        if (xhr) { xhr.onload = xhr.onerror = xhr.ontimeout = xhr.onabort = null; xhr.upload.onprogress = null; }
        if (error) reject(error); else resolve();
      };
      const abort = () => { finish(abortError()); xhr?.abort(); };
      try {
        validateUploadUrl(url);
        if (signal?.aborted) { finish(abortError()); return; }
        xhr = createXhr();
        signal?.addEventListener('abort', abort, { once: true });
        xhr.upload.onprogress = (event) => {
          if (!settled && !signal?.aborted && event.lengthComputable) onProgress?.({ loaded: Math.min(event.loaded, file.size), total: file.size });
        };
        xhr.onload = () => {
          if (signal?.aborted) { finish(abortError()); return; }
          if (xhr.status >= 200 && xhr.status < 300) finish();
          else finish(new ApiError(xhr.status === 403 ? 'S3 từ chối tải lên: link có thể hết hạn hoặc quyền/CORS chưa đúng.' : 'S3 chưa nhận được file. Bạn có thể thử tải lại file này.', { code: 'UPLOAD_HTTP', status: xhr.status }));
        };
        xhr.onerror = () => finish(new ApiError('Không tải được file lên S3. Hãy kiểm tra mạng và CORS của bucket input.', { code: 'UPLOAD_NETWORK' }));
        xhr.ontimeout = () => finish(new ApiError('Tải file quá lâu. Bạn có thể thử lại cùng file nếu link còn hạn.', { code: 'UPLOAD_TIMEOUT' }));
        xhr.onabort = () => finish(abortError());
        xhr.open('PUT', url, true);
        xhr.withCredentials = false;
        xhr.timeout = timeoutMs;
        xhr.setRequestHeader('Content-Type', 'application/octet-stream');
        xhr.send(file); // Gửi bytes gốc, không FormData/base64, không tự retry.
      } catch (error) {
        finish(error instanceof ApiError ? error : new ApiError('Không khởi tạo được lượt tải file. Hãy thử lại.', { code: 'UPLOAD_NETWORK' }));
      }
    });
  };
}
