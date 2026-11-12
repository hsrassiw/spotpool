import { ApiError } from './client.js';
import { jobStatuses } from './jobs.js';

const invalid = () => new ApiError('Chưa tải được thông tin job đầy đủ. Vui lòng thử lại.', { code: 'INVALID_RESPONSE' });
const isCount = (value) => Number.isSafeInteger(value) && value >= 0;
const isDate = (value) => isCount(value) && value <= 8640000000000;
const taskStatuses = jobStatuses.filter((status) => status !== 'UPLOADING');

export function validateReadUrl(value) {
  try {
    if (typeof value !== 'string') throw invalid();
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.hash
      || !/(^|\.)s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com$/i.test(url.hostname)) throw invalid();
    return value; // Giữ nguyên URL và query đã ký; không tự dựng key S3.
  } catch { throw invalid(); }
}

export function parseJobDetail(value, jobId) {
  if (!value || value.jobId !== jobId || !jobStatuses.includes(value.status) || typeof value.interrupted !== 'boolean'
    || !['spot', 'ondemand'].includes(value.tier) || !['script', 'video'].includes(value.kind)
    || ![1, 2].includes(value.vcpu) || !isCount(value.costCap) || !isCount(value.usedVcpuSeconds)
    || !(value.chargedCredit === null || isCount(value.chargedCredit)) || !isDate(value.createdAt)
    || !['queuedAt', 'startedAt', 'finishedAt'].every((key) => value[key] === null || isDate(value[key]))
    || !value.progress || !['total', 'succeeded', 'failed'].every((key) => isCount(value.progress[key]))
    || value.progress.total < 1 || value.progress.succeeded + value.progress.failed > value.progress.total
    || !Array.isArray(value.tasks) || value.tasks.length > value.progress.total) throw invalid();
  const ids = new Set();
  const tasks = value.tasks.map((task) => {
    if (!task || typeof task.taskId !== 'string' || !/^task-\d{4}$/.test(task.taskId) || ids.has(task.taskId)
      || typeof task.fileName !== 'string' || !task.fileName || !taskStatuses.includes(task.status)
      || !isCount(task.interruptCount) || !(task.checkpointAt == null || isDate(task.checkpointAt))) throw invalid();
    ids.add(task.taskId);
    return Object.freeze({ taskId: task.taskId, fileName: task.fileName, status: task.status, interruptCount: task.interruptCount,
      checkpointAt: task.checkpointAt ?? null,
      resultUrl: task.resultUrl == null || task.status !== 'SUCCEEDED' ? null : validateReadUrl(task.resultUrl),
      logUrl: task.logUrl == null ? null : validateReadUrl(task.logUrl) });
  }).sort((a, b) => a.taskId.localeCompare(b.taskId));
  return Object.freeze({ ...Object.fromEntries(['jobId', 'status', 'interrupted', 'tier', 'kind', 'vcpu', 'costCap',
    'usedVcpuSeconds', 'chargedCredit', 'createdAt', 'queuedAt', 'startedAt', 'finishedAt'].map((key) => [key, value[key]])),
  progress: Object.freeze({ total: value.progress.total, succeeded: value.progress.succeeded, failed: value.progress.failed }),
  tasks: Object.freeze(tasks) });
}

export function createJobDetailApi(client) {
  return {
    async getJob(jobId, { signal } = {}) {
      if (typeof jobId !== 'string' || !jobId || jobId.length > 128) throw invalid();
      return parseJobDetail(await client.request(`/jobs/${encodeURIComponent(jobId)}`, { signal }), jobId);
    },
  };
}
