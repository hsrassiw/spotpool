import test from 'node:test';
import assert from 'node:assert/strict';
import { createTaskFilesStore } from '../src/jobs/taskFilesStore.js';
import { ApiError } from '../src/api/client.js';

const url = (version) => `https://output.s3.ap-southeast-1.amazonaws.com/result?Signature=${version}`;
const task = (taskId = 'task-0001', status = 'SUCCEEDED', version = 'old') => ({ taskId, fileName: 'test.py', status, logUrl: url(version), resultUrl: url(version) });
const job = (version = 'old') => ({ tasks: [task('task-0001', 'SUCCEEDED', version), task('task-0002', 'FAILED', version)] });
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };

test('logs are read only on demand and expired links refresh once without retrying forever', async () => {
  const reads = []; let refreshes = 0;
  const store = createTaskFilesStore({ getCurrentJob: () => job(), refreshJob: async () => { refreshes++; return job('fresh'); },
    readLog: async (address) => { reads.push(address); if (reads.length === 1) throw new ApiError('Expired', { status: 403, code: 'FILE_HTTP' }); return { text: 'result', truncated: false }; } });
  assert.equal(reads.length, 0);
  await store.showLog('task-0001');
  assert.deepEqual(reads, [url('old'), url('fresh')]); assert.equal(refreshes, 1);
  assert.equal(store.getSnapshot().log.text, 'result'); assert.equal(store.getSnapshot().log.url, url('fresh'));
  const failing = createTaskFilesStore({ getCurrentJob: () => job(), refreshJob: async () => { refreshes++; return job('fresh'); },
    readLog: async () => { throw new Error(url('secret')); } });
  await failing.showLog('task-0001');
  assert.equal(refreshes, 2); assert.equal(failing.getSnapshot().log.error.includes('secret'), false);
  store.stop(); failing.stop();
});

test('closing log and switching tasks abort old reads and ignore late content', async () => {
  const late = deferred(); const signals = [];
  const store = createTaskFilesStore({ getCurrentJob: () => job(), readLog: async (address, { signal }) => {
    signals.push(signal); return signals.length === 1 ? late.promise : { text: 'new task log', truncated: false };
  } });
  const old = store.showLog('task-0001');
  await store.showLog('task-0002'); assert.equal(signals[0].aborted, true);
  late.resolve({ text: 'stale task log', truncated: false }); await old;
  assert.equal(store.getSnapshot().log.taskId, 'task-0002'); assert.equal(store.getSnapshot().log.text, 'new task log');
  store.closeLog(); assert.equal(signals[1].aborted, true); assert.equal(store.getSnapshot().log, null); store.stop();
});

test('download refreshes before navigation, guards task success and deduplicates double-clicks', async () => {
  const response = deferred(); const navigated = []; let opens = 0;
  const target = { closed: false, opener: 'original', location: { replace: (address) => navigated.push(address) }, close() { this.closed = true; } };
  const store = createTaskFilesStore({ getCurrentJob: () => job(), refreshJob: () => response.promise,
    openWindow: () => { opens++; return target; } });
  await store.downloadResult('task-0002'); assert.equal(opens, 0);
  const pending = store.downloadResult('task-0001'); await store.downloadResult('task-0001');
  assert.equal(opens, 1); assert.equal(target.opener, null); assert.equal(navigated.length, 0);
  response.resolve(job('fresh')); await pending;
  assert.deepEqual(navigated, [url('fresh')]); store.stop();
  assert.equal(target.closed, false); // Không đóng tab kết quả đã bàn giao cho người dùng.
});

test('blocked popup offers the freshly signed link and missing files never navigate', async () => {
  const store = createTaskFilesStore({ getCurrentJob: () => job(), refreshJob: async () => job('fresh'), openWindow: () => null });
  await store.downloadResult('task-0001'); assert.equal(store.getSnapshot().download.url, url('fresh'));
  let closed = false; let navigated = false;
  const missing = createTaskFilesStore({ getCurrentJob: () => job(), refreshJob: async () => ({ tasks: [] }),
    openWindow: () => ({ location: { replace: () => { navigated = true; } }, close: () => { closed = true; } }) });
  await missing.downloadResult('task-0001'); assert.equal(closed, true); assert.equal(navigated, false);
  assert.ok(missing.getSnapshot().download.error); store.stop(); missing.stop();
});

test('logout or route cleanup closes pending windows and cannot navigate after a late reply', async () => {
  const response = deferred(); let closed = false; let navigated = false;
  const store = createTaskFilesStore({ getCurrentJob: () => job(), refreshJob: () => response.promise,
    openWindow: () => ({ location: { replace: () => { navigated = true; } }, close: () => { closed = true; } }) });
  const pending = store.downloadResult('task-0001'); store.stop();
  assert.equal(closed, true); response.resolve(job('fresh')); await pending;
  assert.equal(navigated, false); assert.deepEqual(store.getSnapshot(), { log: null, download: null });
});
