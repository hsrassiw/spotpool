import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, createApiClient } from '../src/api/client.js';
import { createJobSubmissionApi, parseCreatedJob, validateUploadUrl } from '../src/api/jobSubmission.js';
import { createUploader } from '../src/api/upload.js';
import { validateJobDraft } from '../src/jobs/jobDraft.js';

const id = '22222222-2222-4222-8222-222222222222';
const url = 'https://input-test.s3.ap-southeast-1.amazonaws.com/uploads/test?X-Amz-Signature=private-test-signature';
const file = (name = 'hello.py') => new File(['print(42)'], name, { type: 'text/x-python' });
const draft = { kind: 'script', tier: 'spot', costCap: '5000' };
const response = (names) => ({ jobId: id, status: 'UPLOADING', uploads: names.map((name, index) => ({
  taskId: `task-${String(index + 1).padStart(4, '0')}`, fileName: name, url, expiresIn: 3600,
})) });

test('draft validates integer cap, 1-100 files, kinds and file type without rejecting duplicate names', () => {
  const result = validateJobDraft(draft, [file(), file()]);
  assert.deepEqual(result.payload, { tier: 'spot', kind: 'script', vcpu: 1, costCap: 5000, files: ['hello.py', 'hello.py'], params: {} });
  for (const costCap of ['0', '-1', '1.5', '1e3', '', 'NaN', 'Infinity', '9007199254740992']) {
    assert.ok(validateJobDraft({ ...draft, costCap }, [file()]).errors.costCap);
  }
  assert.ok(validateJobDraft(draft, []).errors.files);
  assert.ok(validateJobDraft(draft, Array.from({ length: 101 }, () => file())).errors.files);
  assert.ok(validateJobDraft(draft, [file('archive.zip')]).errors.files);
  assert.ok(validateJobDraft({ ...draft, tier: 'unknown' }, [file()]).errors.tier);
  assert.ok(validateJobDraft({ ...draft, kind: 'unknown' }, [file()]).errors.kind);
  assert.ok(validateJobDraft({ ...draft, kind: 'video' }, [file()]).errors.files);
  assert.ok(validateJobDraft({ ...draft, kind: 'video' }, [file('lecture.mp4')]).payload);
  assert.ok(validateJobDraft(draft, Array.from({ length: 100 }, () => file())).payload);
});

test('create response binds duplicate filenames by taskId even if response order changes', () => {
  const value = response(['same.py', 'same.py']);
  value.uploads.reverse();
  const parsed = parseCreatedJob(value, ['same.py', 'same.py']);
  assert.deepEqual(parsed.uploads.map((item) => item.taskId), ['task-0001', 'task-0002']);
  assert.equal(parsed.uploads[0].url, url);
  for (const bad of [null, { ...value, jobId: '../escape' }, { ...value, status: 'QUEUED' }, { ...value, uploads: [value.uploads[0]] },
    { ...value, uploads: [value.uploads[0], value.uploads[0]] },
    { ...value, uploads: value.uploads.map((item) => ({ ...item, fileName: 'wrong.py' })) },
    { ...value, uploads: value.uploads.map((item) => ({ ...item, expiresIn: 0 })) }]) {
    assert.throws(() => parseCreatedJob(bad, ['same.py', 'same.py']), { code: 'INVALID_RESPONSE' });
  }
  for (const badUrl of ['http://input.s3.amazonaws.com/a', 'https://s3.amazonaws.com.evil.invalid/a', 'https://evil.invalid/a', 'https://user:password@s3.amazonaws.com/a', 'javascript:alert(1)', null]) {
    assert.throws(() => validateUploadUrl(badUrl), { code: 'INVALID_RESPONSE' });
  }
});

test('submission API supports current create/start/cancel responses and minimal state recovery', async () => {
  const calls = [];
  const signal = new AbortController().signal;
  const api = createJobSubmissionApi({ request: async (path, options = {}) => {
    calls.push({ path, ...options });
    if (path === '/jobs') return response(['hello.py']);
    if (options.method === 'POST') return { jobId: id, message: 'Success' };
    return { jobId: id, status: 'RUNNING', tasks: [{ resultUrl: 'private' }] };
  } });
  const payload = validateJobDraft(draft, [file()]).payload;
  await api.createJob(payload, { signal });
  await api.startJob(id, { signal });
  await api.cancelJob(id, { signal });
  assert.deepEqual(await api.getJobState(id, { signal }), { jobId: id, status: 'RUNNING' });
  assert.deepEqual(calls.map((call) => [call.path, call.method]), [['/jobs', 'POST'], [`/jobs/${id}/start`, 'POST'], [`/jobs/${id}/cancel`, 'POST'], [`/jobs/${id}`, undefined]]);
  assert.equal(calls.every((call) => call.signal === signal), true);
  assert.deepEqual(calls[0].body, payload);
});

test('invalid create response retains a validated jobId for cancellation, never a signed URL in the error', async () => {
  const api = createJobSubmissionApi({ request: async () => ({ ...response(['hello.py']), uploads: [] }) });
  await assert.rejects(api.createJob({ files: ['hello.py'] }), (error) => error.code === 'INVALID_RESPONSE' && error.details.jobId === id && !JSON.stringify(error).includes('Signature'));
});

test('400 start failure keeps only validated missingFiles; private response details are discarded', async () => {
  const client = createApiClient({ baseUrl: 'https://api.example', fetchImpl: async () => new Response(JSON.stringify({
    error: 'Missing files on S3', missingFiles: ['task-0002'], privateUrl: url,
  }), { status: 400 }) });
  await assert.rejects(createJobSubmissionApi(client).startJob(id), (error) => {
    assert.deepEqual(error.details, { missingFiles: ['task-0002'] });
    assert.equal(JSON.stringify(error).includes('private-test-signature'), false);
    return true;
  });
});

class FakeXhr {
  constructor() { this.upload = {}; this.headers = {}; this.status = 200; this.aborted = false; }
  open(method, requestUrl, async) { this.method = method; this.url = requestUrl; this.async = async; }
  setRequestHeader(name, value) { this.headers[name] = value; }
  send(body) { this.body = body; }
  abort() { this.aborted = true; this.onabort?.(); }
}

test('S3 transport sends original file bytes, exact signed URL, progress and no SpotPool authorization', async () => {
  const xhr = new FakeXhr();
  const input = file();
  const progress = [];
  const pending = createUploader({ createXhr: () => xhr })(url, input, { onProgress: (value) => progress.push(value) });
  assert.equal(xhr.method, 'PUT'); assert.equal(xhr.url, url); assert.equal(xhr.body, input);
  assert.deepEqual(xhr.headers, { 'Content-Type': 'application/octet-stream' });
  assert.equal(xhr.withCredentials, false);
  xhr.upload.onprogress({ loaded: input.size, lengthComputable: true });
  assert.deepEqual(progress, [{ loaded: input.size, total: input.size }]);
  let finished = false; pending.then(() => { finished = true; });
  await Promise.resolve(); assert.equal(finished, false); // Bytes sent chưa phải S3 đã xác nhận.
  xhr.onload(); await pending;
  assert.equal(xhr.onload, null);
});

test('S3 transport aborts on session/page cancellation, rejects 403/timeout safely and never retries', async () => {
  const controller = new AbortController();
  const xhr = new FakeXhr();
  let count = 0;
  const upload = createUploader({ createXhr: () => { count += 1; return xhr; } });
  const pending = upload(url, file(), { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' }); assert.equal(xhr.aborted, true);
  await assert.rejects(upload(url, file(), { signal: controller.signal }), { name: 'AbortError' });
  assert.equal(count, 1);
  for (const kind of ['http', 'timeout', 'network']) {
    const next = new FakeXhr();
    const run = createUploader({ createXhr: () => next })(url, file());
    if (kind === 'http') { next.status = 403; next.onload(); }
    if (kind === 'timeout') next.ontimeout();
    if (kind === 'network') next.onerror();
    await assert.rejects(run, (error) => error instanceof ApiError && !error.message.includes('private-test-signature'));
  }
});
