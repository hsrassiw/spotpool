import { ApiError } from './client.js';
import { jobStatuses } from './jobs.js';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const invalid = () => new ApiError('Chưa xác nhận được thông tin job. Hãy kiểm tra job hiện tại trước khi gửi lại.', { code: 'INVALID_RESPONSE' });

export function validateUploadUrl(value) {
  try {
    if (typeof value !== 'string') throw invalid();
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.hash
      || !/(^|\.)s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com$/i.test(url.hostname)) throw invalid();
    return value; // Giữ nguyên query đã ký, không tái tạo URL.
  } catch { throw invalid(); }
}

export function parseCreatedJob(value, names) {
  if (!value || !uuid.test(value.jobId) || value.status !== 'UPLOADING' || !Array.isArray(value.uploads) || value.uploads.length !== names.length) throw invalid();
  const byId = new Map(value.uploads.map((item) => [item?.taskId, item]));
  if (byId.size !== names.length) throw invalid();
  const uploads = names.map((name, index) => {
    const taskId = `task-${String(index + 1).padStart(4, '0')}`;
    const item = byId.get(taskId);
    if (!item || item.fileName !== name || !Number.isSafeInteger(item.expiresIn) || item.expiresIn <= 0) throw invalid();
    return { taskId, fileName: name, url: validateUploadUrl(item.url), expiresIn: item.expiresIn };
  });
  return { jobId: value.jobId, status: value.status, uploads };
}

export function createJobSubmissionApi(client) {
  const path = (jobId) => {
    if (!uuid.test(jobId)) throw invalid();
    return `/jobs/${encodeURIComponent(jobId)}`;
  };
  async function action(jobId, name, signal) {
    const result = await client.request(`${path(jobId)}/${name}`, { method: 'POST', signal });
    if (!result || result.jobId !== jobId) throw invalid();
    return { jobId }; // Không dùng message hoặc URL ngoài hợp đồng.
  }
  return {
    async createJob(payload, { signal } = {}) {
      const value = await client.request('/jobs', { method: 'POST', body: payload, signal });
      try { return parseCreatedJob(value, payload.files); }
      catch (error) {
        if (uuid.test(value?.jobId)) error.details = { jobId: value.jobId };
        throw error;
      }
    },
    startJob: (jobId, { signal } = {}) => action(jobId, 'start', signal),
    cancelJob: (jobId, { signal } = {}) => action(jobId, 'cancel', signal),
    async getJobState(jobId, { signal } = {}) {
      const result = await client.request(path(jobId), { signal });
      if (!result || result.jobId !== jobId || !jobStatuses.includes(result.status)) throw invalid();
      return { jobId, status: result.status };
    },
  };
}
