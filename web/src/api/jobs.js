import { ApiError } from './client.js';

export const jobStatuses = Object.freeze(['UPLOADING', 'QUEUED', 'RUNNING', 'PAUSED', 'SUCCEEDED', 'FAILED', 'CANCELLED']);
const isCount = (value) => Number.isSafeInteger(value) && value >= 0;
const invalid = () => new ApiError('Chưa tải được danh sách job đầy đủ. Vui lòng thử lại sau.', { code: 'INVALID_RESPONSE' });

export function parseJobsPage(value) {
  if (!value || !Array.isArray(value.jobs) || !(value.next === null || (typeof value.next === 'string' && value.next.length > 0))) throw invalid();
  const jobs = value.jobs.map((job) => {
    if (!job || typeof job.jobId !== 'string' || !job.jobId || typeof job.name !== 'string' || !job.name
      || !jobStatuses.includes(job.status) || !['spot', 'ondemand'].includes(job.tier) || !['video', 'script'].includes(job.kind)
      || !isCount(job.createdAt) || !isCount(job.costCap) || !(job.chargedCredit === null || isCount(job.chargedCredit))
      || !job.progress || !['total', 'succeeded', 'failed'].every((field) => isCount(job.progress[field]))
      || job.progress.total < 1 || job.progress.succeeded + job.progress.failed > job.progress.total) throw invalid();
    return Object.freeze({
      ...Object.fromEntries(['jobId', 'name', 'status', 'tier', 'kind', 'createdAt', 'costCap', 'chargedCredit'].map((field) => [field, job[field]])),
      progress: Object.freeze({ total: job.progress.total, succeeded: job.progress.succeeded, failed: job.progress.failed }),
    });
  });
  return { jobs, next: value.next };
}

export function createJobsApi(client) {
  return {
    async listJobs({ status = '', next, limit = 20, signal } = {}) {
      if (status && !jobStatuses.includes(status)) throw new ApiError('Bộ lọc trạng thái không hợp lệ.', { code: 'CONFIG' });
      const params = new URLSearchParams({ limit: String(limit) });
      if (status) params.set('status', status);
      if (next) params.set('next', next);
      return parseJobsPage(await client.request(`/jobs?${params}`, { signal }));
    },
  };
}
