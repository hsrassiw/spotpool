import test from 'node:test';
import assert from 'node:assert/strict';
import { createJobDetailApi, parseJobDetail, validateReadUrl } from '../src/api/jobDetail.js';
import { readLog } from '../src/api/readLog.js';

const url = 'https://output.s3.ap-southeast-1.amazonaws.com/result?X-Amz-Signature=private-test';
const task = (taskId = 'task-0001', status = 'SUCCEEDED') => ({ taskId, fileName: 'test.py', status, interruptCount: 0, resultUrl: url, logUrl: url });
const job = () => ({ jobId: 'job-1', status: 'RUNNING', interrupted: false, tier: 'spot', kind: 'script', vcpu: 1,
  costCap: 5000, usedVcpuSeconds: 10, chargedCredit: null, progress: { total: 2, succeeded: 1, failed: 0 },
  createdAt: 1793491200, queuedAt: 1793491201, startedAt: null, finishedAt: null, tasks: [task()] });

test('detail API forwards cancellation, encodes the job id and rejects another job response', async () => {
  const signal = new AbortController().signal;
  let call;
  const api = createJobDetailApi({ request: async (path, options) => { call = { path, options }; return job(); } });
  await api.getJob('job-1', { signal });
  assert.equal(call.path, '/jobs/job-1');
  assert.equal(call.options.signal, signal);
  await assert.rejects(api.getJob('other/id'), { code: 'INVALID_RESPONSE' });
  assert.equal(call.path, '/jobs/other%2Fid');
});

test('detail parser sorts unique task ids, preserves null/zero and drops private fields', () => {
  const parsed = parseJobDetail({ ...job(), chargedCredit: 0, privateKey: 'hidden', tasks: [task('task-0002'),
    { ...task('task-0001', 'RUNNING'), workerId: 'hidden', resultUrl: 'javascript:ignored-non-success' }] }, 'job-1');
  assert.equal(parsed.chargedCredit, 0);
  assert.equal(parsed.startedAt, null);
  assert.deepEqual(parsed.tasks.map((item) => item.taskId), ['task-0001', 'task-0002']);
  assert.equal(parsed.tasks[0].resultUrl, null);
  assert.equal('privateKey' in parsed, false);
  assert.equal('workerId' in parsed.tasks[0], false);
  assert.equal(parseJobDetail({ ...job(), tasks: [] }, 'job-1').tasks.length, 0);
  for (const fields of [{ chargedCredit: -1 }, { createdAt: 8640000000001 }, { status: 'INTERRUPTED' },
    { progress: { total: 2, succeeded: 2, failed: 1 } }, { tasks: [task(), task()] },
    { tasks: [{ ...task(), interruptCount: -1 }] }, { tasks: [{ ...task(), status: 'UPLOADING' }] }]) {
    assert.throws(() => parseJobDetail({ ...job(), ...fields }, 'job-1'), { code: 'INVALID_RESPONSE' });
  }
});

test('signed read links preserve bytes but reject active schemes, credentials and external hosts', () => {
  assert.equal(validateReadUrl(url), url);
  for (const bad of ['javascript:alert(1)', 'http://output.s3.amazonaws.com/a', 'https://evil.invalid/a',
    'https://output.s3.amazonaws.com.evil.invalid/a', 'https://user:pass@output.s3.amazonaws.com/a', `${url}#fragment`]) {
    assert.throws(() => validateReadUrl(bad), { code: 'INVALID_RESPONSE' });
  }
});

test('log transport streams UTF-8 text without SpotPool credentials or reading it as HTML', async () => {
  let call;
  const bytes = new TextEncoder().encode('Chào <script>alert(1)</script>');
  const result = await readLog(url, { fetchImpl: async (address, options) => {
    call = { address, options };
    return new Response(new ReadableStream({ start(controller) {
      controller.enqueue(bytes.subarray(0, 3)); controller.enqueue(bytes.subarray(3)); controller.close();
    } }));
  } });
  assert.equal(result.text, 'Chào <script>alert(1)</script>');
  assert.equal(result.truncated, false);
  assert.equal(call.address, url);
  assert.equal(call.options.credentials, 'omit');
  assert.equal(call.options.headers, undefined);
  assert.equal(call.options.cache, 'no-store');
  assert.equal(call.options.referrerPolicy, 'no-referrer');
});

test('log preview cancels the stream at its byte limit instead of loading the entire file', async () => {
  let cancelled = false;
  const result = await readLog(url, { maxBytes: 8, fetchImpl: async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('0123456789abcdef')); },
    cancel() { cancelled = true; },
  })) });
  assert.deepEqual(result, { text: '01234567', truncated: true });
  assert.equal(cancelled, true);
});

test('log errors hide signed links and distinguish HTTP, cancellation and timeout', async () => {
  await assert.rejects(readLog(url, { fetchImpl: async () => new Response('private S3 error', { status: 403 }) }),
    (error) => error.status === 403 && !error.message.includes('private'));
  await assert.rejects(readLog(url, { fetchImpl: async () => { throw new Error(url); } }),
    (error) => error.code === 'FILE_NETWORK' && !error.message.includes('Signature'));
  const aborter = new AbortController(); aborter.abort();
  let called = false;
  await assert.rejects(readLog(url, { signal: aborter.signal, fetchImpl: async () => { called = true; } }), { name: 'AbortError' });
  assert.equal(called, false);
  await assert.rejects(readLog(url, { timeoutMs: 5, fetchImpl: (address, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new Error(address)), { once: true });
  }) }), { code: 'FILE_TIMEOUT' });
});
