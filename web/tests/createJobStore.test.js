import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError } from '../src/api/client.js';
import { createJobStore } from '../src/jobs/createJobStore.js';

const id = '22222222-2222-4222-8222-222222222222';
const file = (name = 'same.py') => new File(['print(42)'], name);
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = () => new Promise((resolve) => setImmediate(resolve));

function setup({ uploadFile = async () => {}, refreshProfile, now, overrides = {} } = {}) {
  const calls = { create: [], start: [], cancel: [], read: [] };
  const api = {
    async createJob(payload) {
      calls.create.push(payload);
      return { jobId: id, status: 'UPLOADING', uploads: payload.files.map((name, index) => ({
        taskId: `task-${String(index + 1).padStart(4, '0')}`, fileName: name,
        url: `https://input.s3.ap-southeast-1.amazonaws.com/${index}?Signature=private`, expiresIn: 3600,
      })) };
    },
    async startJob(jobId) { calls.start.push(jobId); return { jobId }; },
    async cancelJob(jobId) { calls.cancel.push(jobId); return { jobId }; },
    async getJobState(jobId) { calls.read.push(jobId); return { jobId, status: 'RUNNING' }; },
    ...overrides,
  };
  const store = createJobStore({ api, uploadFile, refreshProfile, now });
  store.setFiles([file()]);
  return { store, api, calls };
}

test('create validates before POST, freezes config and never starts until manual action', async () => {
  let creditReads = 0;
  const { store, calls } = setup({ refreshProfile: async () => { creditReads += 1; } });
  store.configure({ costCap: '-5' }); await store.create();
  assert.equal(calls.create.length, 0); assert.ok(store.getSnapshot().errors.costCap);
  store.configure({ costCap: '5000' }); await store.create();
  store.configure({ tier: 'ondemand' }); store.setFiles([file('replacement.py')]);
  assert.equal(store.getSnapshot().draft.tier, 'spot');
  assert.equal(store.getSnapshot().files[0].name, 'same.py');
  assert.equal(calls.start.length, 0); assert.equal(creditReads, 0);
  await store.start();
  assert.deepEqual(calls.start, [id]); assert.equal(creditReads, 1);
  assert.equal(store.getSnapshot().phase, 'started'); assert.equal(store.getSnapshot().remoteStatus, 'RUNNING');
  await store.start(); assert.equal(calls.start.length, 1);
});

test('uploads cap concurrency at 3, bind duplicate files by index and retry only the failed task', async () => {
  const pending = [];
  const sent = [];
  let live = 0; let max = 0;
  const inputs = [file(), file(), file(), file()];
  const { store, calls } = setup({ uploadFile: (url, input, options) => {
    const item = deferred(); pending.push(item); sent.push({ url, input, options });
    live += 1; max = Math.max(max, live);
    return item.promise.finally(() => { live -= 1; });
  } });
  store.setFiles(inputs);
  const creating = store.create(); await tick();
  assert.equal(sent.length, 3);
  await store.start(); assert.equal(calls.start.length, 0);
  pending[0].resolve(); await tick(); assert.equal(sent.length, 4);
  pending[1].reject(new ApiError('Upload interrupted')); pending[2].resolve(); pending[3].resolve();
  await creating;
  assert.equal(max, 3); assert.equal(store.getSnapshot().rows[1].status, 'error');
  assert.deepEqual(sent.slice(0, 4).map((item) => item.input), inputs);
  const retry = store.uploadPending('task-0002'); await tick();
  assert.equal(sent.length, 5); assert.equal(sent[4].input, inputs[1]); assert.equal(sent[4].url, sent[1].url);
  pending[4].resolve(); await retry;
  assert.equal(calls.create.length, 1); assert.equal(calls.start.length, 0);
  assert.equal(store.getSnapshot().rows.every((row) => row.status === 'uploaded'), true);
  assert.equal(JSON.stringify(store.getSnapshot()).includes('Signature=private'), false);
});

test('expired URL blocks PUT retries while uploaded files still allow manual start', async () => {
  let clock = 1000; let uploads = 0;
  const { store } = setup({ now: () => clock, uploadFile: async () => { uploads += 1; throw new Error('Network'); } });
  await store.create(); clock += 3600 * 1000;
  await store.uploadPending();
  assert.equal(uploads, 1); assert.equal(store.getSnapshot().rows[0].expired, true);
  const successful = setup({ now: () => clock });
  await successful.store.create(); clock += 3600 * 1000;
  await successful.store.start(); assert.equal(successful.calls.start.length, 1);
});

test('ambiguous POST create is locked against repeat until explicit acknowledgement; known rejection keeps draft', async () => {
  let attempts = 0;
  const { store, api } = setup({ overrides: { createJob: async () => { attempts += 1; throw new ApiError('Timeout', { code: 'TIMEOUT' }); } } });
  await store.create(); await store.create();
  assert.equal(attempts, 1); assert.equal(store.getSnapshot().phase, 'create-unknown');
  store.acknowledgeUnknownCreate(); await store.create(); assert.equal(attempts, 2);
  store.acknowledgeUnknownCreate();
  api.createJob = async () => { throw new ApiError('Bad request', { code: 'HTTP', status: 400 }); };
  await store.create(); assert.equal(store.getSnapshot().phase, 'draft');
  assert.equal(store.getSnapshot().files.length, 1);
});

test('invalid create response with known jobId can be cancelled, not silently recreated', async () => {
  const { store, calls } = setup({ overrides: { createJob: async () => { throw new ApiError('Invalid response', { code: 'INVALID_RESPONSE', details: { jobId: id } }); } } });
  await store.create(); store.acknowledgeUnknownCreate(); await store.create();
  assert.equal(store.getSnapshot().jobId, id); assert.equal(store.getSnapshot().phase, 'create-unknown');
  await store.cancelJob(); assert.deepEqual(calls.cancel, [id]); assert.equal(store.getSnapshot().phase, 'cancelled');
});

test('double-click start sends one POST; failed credit refresh does not undo acknowledged start', async () => {
  const pending = deferred(); let starts = 0;
  const { store } = setup({ refreshProfile: async () => { throw new Error('Offline'); }, overrides: {
    startJob: async () => { starts += 1; return pending.promise; },
  } });
  await store.create(); const starting = store.start(); await store.start();
  assert.equal(starts, 1); pending.resolve({ jobId: id }); await starting;
  assert.equal(store.getSnapshot().phase, 'started'); assert.ok(store.getSnapshot().profileError);
});

test('missingFiles marks only server-listed taskIds; insufficient credit preserves uploads and jobId', async () => {
  let fail = new ApiError('Missing files', { status: 400, details: { missingFiles: ['task-0002'] } });
  const { store, calls } = setup({ overrides: {
    startJob: async () => { throw fail; }, getJobState: async () => ({ jobId: id, status: 'UPLOADING' }),
  } });
  store.setFiles([file(), file()]); await store.create(); await store.start();
  assert.equal(store.getSnapshot().rows[0].status, 'uploaded'); assert.equal(store.getSnapshot().rows[1].status, 'error');
  assert.equal(store.getSnapshot().jobId, id);
  await store.uploadPending('task-0002');
  fail = new ApiError('Insufficient credit', { status: 400 }); await store.start();
  assert.equal(store.getSnapshot().phase, 'upload'); assert.match(store.getSnapshot().error, /Insufficient credit/);
  assert.equal(store.getSnapshot().rows.every((row) => row.status === 'uploaded'), true);
  assert.equal(calls.create.length, 1);
});

test('ambiguous start checks same job and keeps QUEUED for manual manifest completion before success', async () => {
  let starts = 0; let reads = 0;
  const { store } = setup({ overrides: {
    startJob: async () => { starts += 1; if (starts === 1) throw new ApiError('Timeout', { code: 'TIMEOUT' }); return { jobId: id }; },
    getJobState: async (jobId) => { assert.equal(jobId, id); reads += 1; return { jobId, status: starts === 1 ? 'QUEUED' : 'RUNNING' }; },
  } });
  await store.create(); await store.start();
  assert.equal(starts, 1); assert.equal(reads, 1); assert.equal(store.getSnapshot().phase, 'queued');
  await store.start(); assert.equal(starts, 2); assert.equal(store.getSnapshot().phase, 'started');
});

test('unknown start outcome locks mutation until manual state check; RUNNING or PAUSED never resubmits start', async () => {
  let reads = 0; let starts = 0;
  const { store, api } = setup({ overrides: {
    startJob: async () => { starts += 1; throw new ApiError('Network', { code: 'NETWORK' }); },
    getJobState: async () => { reads += 1; if (reads === 1) throw new Error('Offline'); return { jobId: id, status: 'RUNNING' }; },
  } });
  await store.create(); await store.start(); assert.equal(store.getSnapshot().phase, 'uncertain');
  await store.start(); await store.cancelJob(); assert.equal(starts, 1);
  await store.checkStatus(); assert.equal(store.getSnapshot().phase, 'started'); assert.equal(starts, 1);
  store.reset(); store.setFiles([file()]); await store.create(); api.getJobState = async () => ({ jobId: id, status: 'PAUSED' });
  await store.start(); assert.equal(store.getSnapshot().phase, 'started'); assert.equal(store.getSnapshot().remoteStatus, 'PAUSED');
});

test('cancel aborts all uploads and never starts queued files; late progress/replies cannot restore a cancelled job', async () => {
  const pending = deferred(); const sent = [];
  const { store, calls } = setup({ uploadFile: (_url, _input, options) => { sent.push(options); return pending.promise; } });
  store.setFiles([file(), file(), file(), file()]);
  const creating = store.create(); await tick(); assert.equal(sent.length, 3);
  await store.cancelJob(); await creating;
  assert.equal(sent.every((options) => options.signal.aborted), true);
  sent[0].onProgress({ loaded: 9 }); pending.resolve(); await tick();
  assert.equal(store.getSnapshot().phase, 'cancelled'); assert.equal(sent.length, 3); assert.deepEqual(calls.cancel, [id]);
});

test('cancel failure reads CANCELLED to recognize server mutation; new form needs no extra create', async () => {
  const { store } = setup({ overrides: {
    cancelJob: async () => { throw new ApiError('Server error', { status: 500 }); },
    getJobState: async () => ({ jobId: id, status: 'CANCELLED' }),
  } });
  await store.create(); await store.cancelJob(); assert.equal(store.getSnapshot().phase, 'cancelled');
  store.reset(); assert.equal(store.getSnapshot().phase, 'draft'); assert.equal(store.getSnapshot().jobId, null); assert.deepEqual(store.getSnapshot().files, []);
});

test('page/session cleanup ignores a late create response; StrictMode cleanup before interaction can restart', async () => {
  const pending = deferred(); let uploads = 0;
  const { store } = setup({ uploadFile: async () => { uploads += 1; }, overrides: { createJob: async () => pending.promise } });
  const run = store.create(); store.cancel();
  pending.resolve({ jobId: id, status: 'UPLOADING', uploads: [{ taskId: 'task-0001', url: 'private', expiresIn: 3600 }] });
  await run; assert.equal(store.getSnapshot().jobId, null); assert.equal(uploads, 0);
  const fresh = setup(); fresh.store.cancel(); await fresh.store.create();
  assert.equal(fresh.store.getSnapshot().phase, 'upload');
});
