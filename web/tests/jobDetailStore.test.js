import test from 'node:test';
import assert from 'node:assert/strict';
import { createJobDetailStore } from '../src/jobs/jobDetailStore.js';
import { ApiError } from '../src/api/client.js';

const job = (values = {}) => ({ jobId: 'job-1', status: 'RUNNING', chargedCredit: null, queuedAt: 1, startedAt: 2, ...values });
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
const flush = () => new Promise((resolve) => setImmediate(resolve));
function clock() {
  let time = 0;
  let serial = 0;
  const timers = new Map();
  return { now: () => time, setTimer: (fn, delay) => { const id = ++serial; timers.set(id, { fn, at: time + delay }); return id; },
    clearTimer: (id) => timers.delete(id), count: () => timers.size,
    async tick(ms) {
      const end = time + ms;
      while (true) {
        const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        time = next[1].at; timers.delete(next[0]); next[1].fn(); await flush();
      }
      time = end;
    } };
}

test('polling waits five seconds after completion and shares one in-flight request with manual refresh', async () => {
  const time = clock();
  const response = deferred();
  let count = 0;
  const store = createJobDetailStore({ ...time, loadJob: async () => { count++; return count === 1 ? response.promise : job(); } });
  const first = store.start();
  assert.equal(store.refresh(), first);
  await flush(); await time.tick(15000);
  assert.equal(count, 1); assert.equal(time.count(), 0);
  response.resolve(job()); await first;
  await time.tick(4999); assert.equal(count, 1);
  await time.tick(1); assert.equal(count, 2);
  store.stop(); assert.equal(time.count(), 0);
});

test('hidden tabs abort GET and ignore late replies; becoming visible reads fresh data', async () => {
  const time = clock();
  const late = deferred();
  const signals = [];
  const store = createJobDetailStore({ ...time, loadJob: ({ signal }) => { signals.push(signal); return signals.length === 1 ? late.promise : Promise.resolve(job()); } });
  const first = store.start(); await flush();
  store.setVisible(false); assert.equal(signals[0].aborted, true);
  late.resolve(job({ jobId: 'stale' })); await first;
  assert.equal(store.getSnapshot().job, null);
  await time.tick(10000); assert.equal(signals.length, 1);
  await store.setVisible(true); assert.equal(store.getSnapshot().job.jobId, 'job-1');
  store.stop();
});

test('initially hidden page waits; cleanup can restart without publishing an old response', async () => {
  const time = clock(); let calls = 0;
  const late = deferred();
  const store = createJobDetailStore({ ...time, loadJob: () => ++calls === 1 ? late.promise : Promise.resolve(job()) });
  await store.start(false); assert.equal(calls, 0);
  const old = store.setVisible(true); await flush(); store.stop();
  await store.start(); late.resolve(job({ jobId: 'old-account' })); await old;
  assert.equal(store.getSnapshot().job.jobId, 'job-1');
  store.stop();
});

test('charged zero and cancellation before queuing both stop polling', async () => {
  for (const final of [job({ status: 'SUCCEEDED', chargedCredit: 0 }), job({ status: 'CANCELLED', queuedAt: null, startedAt: null })]) {
    const time = clock(); let calls = 0;
    const store = createJobDetailStore({ ...time, loadJob: async () => { calls++; return final; } });
    await store.start(); await time.tick(120000);
    assert.equal(calls, 1); assert.equal(time.count(), 0); assert.equal(store.getSnapshot().settlementTimedOut, false);
    await store.refresh(); assert.equal(calls, 2); store.stop();
  }
});

test('terminal jobs wait at most 60 seconds for billing, then only manual refresh reads again', async () => {
  const time = clock(); let calls = 0; let chargedCredit = null;
  const store = createJobDetailStore({ ...time, loadJob: async () => { calls++; return job({ status: 'FAILED', chargedCredit }); } });
  await store.start(); await time.tick(60000);
  assert.equal(calls, 12); assert.equal(store.getSnapshot().settlementTimedOut, true); assert.equal(time.count(), 0);
  await time.tick(120000); assert.equal(calls, 12);
  chargedCredit = 15; await store.refresh();
  assert.equal(store.getSnapshot().job.chargedCredit, 15); assert.equal(store.getSnapshot().settlementTimedOut, false);
  assert.equal(time.count(), 0); store.stop();
});

test('PAUSED keeps polling and transient errors retain data; 403/404 clear old data and stop timers', async () => {
  for (const status of [403, 404]) {
    const time = clock(); let response = job({ status: 'PAUSED' });
    const store = createJobDetailStore({ ...time, loadJob: async () => { if (response instanceof Error) throw response; return response; } });
    await store.start(); assert.equal(time.count(), 1);
    response = new ApiError('Temporary outage', { status: 503 }); await store.refresh();
    assert.equal(store.getSnapshot().job.status, 'PAUSED'); assert.equal(time.count(), 1);
    response = new ApiError('No longer available', { status }); await store.refresh();
    assert.equal(store.getSnapshot().job, null); assert.equal(time.count(), 0); store.stop();
  }
});
