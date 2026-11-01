import test from 'node:test';
import assert from 'node:assert/strict';
import { createJobsApi, parseJobsPage } from '../src/api/jobs.js';
import { createJobsStore } from '../src/jobs/jobsStore.js';
import { ApiError } from '../src/api/client.js';

const job = (jobId = 'job-1', createdAt = 30) => ({ jobId, name: 'first.py', status: 'RUNNING', kind: 'script', tier: 'spot',
  progress: { total: 2, succeeded: 1, failed: 0 }, costCap: 5000, chargedCredit: null, createdAt });
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test('jobs API encodes the opaque cursor once, sends status and forwards cancellation', async () => {
  const signal = new AbortController().signal;
  const calls = [];
  const api = createJobsApi({ request: async (path, options) => { calls.push({ path, options }); return { jobs: [], next: null }; } });
  await api.listJobs({ status: 'PAUSED', next: 'opaque-_=%/', signal });
  const url = new URL(calls[0].path, 'https://test.invalid');
  assert.equal(url.pathname, '/jobs');
  assert.equal(url.searchParams.get('status'), 'PAUSED');
  assert.equal(url.searchParams.get('next'), 'opaque-_=%/');
  assert.equal(url.searchParams.get('limit'), '20');
  assert.equal(url.searchParams.has('userId'), false);
  assert.equal(calls[0].options.signal, signal);
  await api.listJobs();
  assert.equal(new URL(calls[1].path, url).searchParams.has('next'), false);
  assert.equal(new URL(calls[1].path, url).searchParams.has('status'), false);
  await assert.rejects(api.listJobs({ status: 'INTERRUPTED' }), { code: 'CONFIG' });
  assert.equal(calls.length, 2);
});

test('job parser keeps contract fields and distinguishes zero credit from null', () => {
  const value = parseJobsPage({ jobs: [{ ...job(), chargedCredit: 0, internalDetails: 'private', progress: { ...job().progress, extra: 'private' } }], next: null });
  assert.equal(value.jobs[0].chargedCredit, 0);
  assert.equal('internalDetails' in value.jobs[0], false);
  assert.equal('extra' in value.jobs[0].progress, false);
  assert.equal(parseJobsPage({ jobs: [job()], next: null }).jobs[0].chargedCredit, null);
  const bad = [null, { jobs: [] }, { jobs: [], next: '' }, { jobs: [job()], next: {} },
    ...[{ status: 'INTERRUPTED' }, { kind: 'unknown' }, { tier: 'standard' }, { chargedCredit: -1 }, { createdAt: 1.5 },
      { progress: { total: 2, succeeded: 2, failed: 1 } }, { progress: { total: 0, succeeded: 0, failed: 0 } }]
      .map((fields) => ({ jobs: [{ ...job(), ...fields }], next: null }))];
  for (const page of bad) assert.throws(() => parseJobsPage(page), { code: 'INVALID_RESPONSE' });
});

test('empty filtered page retains cursor; load more merges, sorts and updates duplicate jobs', async () => {
  const calls = [];
  const pages = [{ jobs: [], next: 'page-2' }, { jobs: [job('a', 30), job('b', 20)], next: 'page-3' },
    { jobs: [{ ...job('b', 20), status: 'SUCCEEDED' }, job('c', 10)], next: null }];
  const store = createJobsStore({ status: 'PAUSED', loadPage: async (options) => { calls.push(options); return pages.shift(); } });
  await store.refresh();
  assert.equal(store.getSnapshot().hasLoaded, true);
  assert.deepEqual(store.getSnapshot().jobs, []);
  assert.equal(store.getSnapshot().next, 'page-2');
  await store.loadMore();
  await store.loadMore();
  assert.deepEqual(calls.map(({ next, status }) => ({ next, status })), [
    { next: undefined, status: 'PAUSED' }, { next: 'page-2', status: 'PAUSED' }, { next: 'page-3', status: 'PAUSED' },
  ]);
  assert.deepEqual(store.getSnapshot().jobs.map(({ jobId }) => jobId), ['a', 'b', 'c']);
  assert.equal(store.getSnapshot().jobs[1].status, 'SUCCEEDED');
  await store.loadMore();
  assert.equal(calls.length, 3);
});

test('failed next page keeps jobs and cursor; manual retry requests that same page', async () => {
  let count = 0;
  const calls = [];
  const store = createJobsStore({ loadPage: async ({ next }) => {
    calls.push(next); count += 1;
    if (count === 2) throw new ApiError('Temporary outage', { status: 503 });
    return count === 1 ? { jobs: [job()], next: 'next-page' } : { jobs: [job('older', 10)], next: null };
  } });
  await store.refresh();
  await store.loadMore();
  assert.equal(store.getSnapshot().jobs.length, 1);
  assert.equal(store.getSnapshot().next, 'next-page');
  assert.equal(store.getSnapshot().moreError.status, 503);
  assert.equal(store.getSnapshot().loadingMore, false);
  await store.loadMore();
  assert.deepEqual(calls, [undefined, 'next-page', 'next-page']);
  assert.equal(store.getSnapshot().jobs.length, 2);
  assert.equal(store.getSnapshot().moreError, null);
});

test('refresh replaces loaded pages; failed refresh preserves data and exposes warning', async () => {
  let reply = { jobs: [job()], next: 'next-page' };
  const store = createJobsStore({ loadPage: async () => { if (reply instanceof Error) throw reply; return reply; } });
  await store.refresh();
  reply = { jobs: [job('older', 10)], next: null };
  await store.loadMore();
  reply = new Error('Offline');
  await store.refresh();
  assert.equal(store.getSnapshot().jobs.length, 2);
  assert.equal(store.getSnapshot().error.message, 'Offline');
  reply = { jobs: [job('fresh', 50)], next: 'new-cursor' };
  await store.refresh();
  assert.deepEqual(store.getSnapshot().jobs.map(({ jobId }) => jobId), ['fresh']);
  assert.equal(store.getSnapshot().next, 'new-cursor');
  assert.equal(store.getSnapshot().error, null);
});

test('double click load more sends one request; refresh supersedes an unfinished page', async () => {
  const pending = deferred();
  const calls = [];
  const store = createJobsStore({ loadPage: (options) => {
    calls.push(options);
    if (calls.length === 2) return pending.promise;
    return Promise.resolve({ jobs: [job(calls.length === 1 ? 'original' : 'fresh')], next: 'page-2' });
  } });
  await store.refresh();
  const first = store.loadMore();
  await store.loadMore();
  assert.equal(calls.length, 2);
  await store.refresh();
  assert.equal(calls[1].signal.aborted, true);
  pending.resolve({ jobs: [job('stale')], next: 'page-3' });
  await first;
  assert.deepEqual(store.getSnapshot().jobs.map(({ jobId }) => jobId), ['fresh']);
  assert.equal(store.getSnapshot().next, 'page-2');
});

test('cancel ignores late replies; StrictMode setup after cleanup can load again', async () => {
  const pending = deferred();
  let call = 0;
  let signal;
  const store = createJobsStore({ loadPage: (options) => {
    call += 1; signal = options.signal;
    return call === 1 ? pending.promise : Promise.resolve({ jobs: [job('new')], next: null });
  } });
  const old = store.refresh();
  store.cancel();
  assert.equal(signal.aborted, true);
  await store.refresh();
  pending.resolve({ jobs: [job('old')], next: null });
  await old;
  assert.deepEqual(store.getSnapshot().jobs.map(({ jobId }) => jobId), ['new']);
  assert.equal(store.getSnapshot().loading, false);
});

test('new filter or identity starts empty and never inherits another store cursor or jobs', async () => {
  const old = createJobsStore({ status: 'RUNNING', loadPage: async () => ({ jobs: [job('old-user')], next: 'private-old-cursor' }) });
  await old.refresh();
  const calls = [];
  const fresh = createJobsStore({ status: 'PAUSED', loadPage: async (options) => { calls.push(options); return { jobs: [], next: null }; } });
  assert.deepEqual(fresh.getSnapshot().jobs, []);
  assert.equal(fresh.getSnapshot().next, null);
  await fresh.refresh();
  assert.equal(calls[0].next, undefined);
  assert.equal(calls[0].status, 'PAUSED');
});

test('initial request failure can retry; repeated cursor is an error with data retained', async () => {
  let call = 0;
  const store = createJobsStore({ loadPage: async () => {
    call += 1;
    if (call === 1) throw new ApiError('Access denied', { status: 403 });
    return { jobs: [job()], next: 'same-cursor' };
  } });
  await store.refresh();
  assert.equal(store.getSnapshot().hasLoaded, false);
  assert.equal(store.getSnapshot().error.status, 403);
  await store.refresh();
  await store.loadMore();
  assert.equal(store.getSnapshot().moreError.code, 'INVALID_RESPONSE');
  assert.equal(store.getSnapshot().jobs.length, 1);
  assert.equal(store.getSnapshot().next, 'same-cursor');
});
