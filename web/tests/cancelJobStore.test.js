import test from 'node:test';
import assert from 'node:assert/strict';
import { createCancelJobStore } from '../src/jobs/cancelJobStore.js';
import { ApiError } from '../src/api/client.js';

const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
const live = { status: 'RUNNING' };

test('cancel sends one POST, reloads actual job/profile and does not mistake a profile error for cancellation failure', async () => {
  const response = deferred(); let posts = 0; let reads = 0;
  const store = createCancelJobStore({ getJob: () => live, cancelJob: () => { posts++; return response.promise; },
    refreshJob: async () => { reads++; return { status: 'CANCELLED' }; }, refreshProfile: async () => { throw new Error('Offline'); } });
  const first = store.cancel(); await store.cancel(); assert.equal(posts, 1); assert.equal(reads, 0);
  response.resolve(); await first;
  assert.equal(reads, 1); assert.equal(store.getSnapshot().unknown, false);
  assert.equal(store.getSnapshot().error, null); assert.ok(store.getSnapshot().notice); assert.ok(store.getSnapshot().profileError);
  store.stop();
});

test('a failed POST checks the same job and recognizes server cancellation without another POST', async () => {
  let posts = 0;
  const store = createCancelJobStore({ getJob: () => live, cancelJob: async () => { posts++; throw new ApiError('Timeout', { code: 'TIMEOUT' }); },
    refreshJob: async () => ({ status: 'CANCELLED' }), refreshProfile: async () => {} });
  await store.cancel(); assert.equal(posts, 1); assert.equal(store.getSnapshot().error, null);
  assert.ok(store.getSnapshot().notice); store.stop();
});

test('unknown cancellation blocks mutations until an explicit GET resolves its state', async () => {
  let posts = 0; let response = null;
  const store = createCancelJobStore({ getJob: () => live, cancelJob: async () => { posts++; throw new ApiError('Offline'); },
    refreshJob: async () => response, refreshProfile: async () => {} });
  await store.cancel(); assert.equal(store.getSnapshot().unknown, true);
  await store.cancel(); assert.equal(posts, 1);
  response = live; await store.checkStatus(); assert.equal(posts, 1); assert.equal(store.getSnapshot().unknown, false);
  response = { status: 'CANCELLED' }; await store.cancel(); assert.equal(posts, 2); assert.equal(store.getSnapshot().error, null);
  store.stop();
});

test('cleanup aborts pending cancellation and prevents late replies from refreshing another session', async () => {
  let signal; let reads = 0;
  const late = deferred();
  const store = createCancelJobStore({ getJob: () => live, cancelJob: (options) => { signal = options.signal; return late.promise; },
    refreshJob: async () => { reads++; return live; }, refreshProfile: async () => {} });
  const first = store.cancel(); store.stop(); assert.equal(signal.aborted, true);
  late.resolve(); await first; assert.equal(reads, 0); assert.equal(store.getSnapshot().busy, false);
});

test('terminal jobs cannot be cancelled and an acknowledged request with stale active status requires rechecking', async () => {
  let response = { status: 'SUCCEEDED' }; let posts = 0;
  const store = createCancelJobStore({ getJob: () => response, cancelJob: async () => { posts++; },
    refreshJob: async () => response, refreshProfile: async () => {} });
  await store.cancel(); assert.equal(posts, 0);
  response = live; await store.cancel(); assert.equal(posts, 1); assert.equal(store.getSnapshot().unknown, true);
  response = { status: 'CANCELLED' }; await store.checkStatus(); assert.equal(posts, 1); assert.equal(store.getSnapshot().unknown, false);
  store.stop();
});
