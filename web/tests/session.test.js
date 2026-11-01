import test from 'node:test';
import assert from 'node:assert/strict';
import { createSessionStore } from '../src/auth/sessionStore.js';
import { safeReturnPath } from '../src/auth/redirect.js';

function memoryStorage(initial = '') {
  const values = new Map(initial ? [['spotpool.apiKey', initial]] : []);
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

const profile = { userId: 'test-user', role: 'user' };

test('only a verified key is persisted and logout clears identity and cancels requests', async () => {
  const pending = deferred();
  const storage = memoryStorage();
  const store = createSessionStore({ storage, loadProfile: (key) => { assert.equal(key, 'fake-key'); return pending.promise; } });
  const login = store.signIn('  fake-key  ');
  assert.equal(storage.getItem('spotpool.apiKey'), null);
  pending.resolve(profile);
  await login;
  assert.equal(storage.getItem('spotpool.apiKey'), 'fake-key');
  assert.equal(store.getSnapshot().profile, profile);
  assert.equal(store.getSnapshot().apiKey, undefined);
  const signal = store.getSessionSignal();
  store.signOut();
  assert.equal(signal.aborted, true);
  assert.equal(storage.getItem('spotpool.apiKey'), null);
  assert.equal(store.getApiKey(), '');
  assert.equal(store.getSnapshot().profile, null);
});

test('stored key is checked before restoring a session', async () => {
  const pending = deferred();
  const store = createSessionStore({ storage: memoryStorage('saved-key'), loadProfile: () => pending.promise });
  const restore = store.restore();
  assert.equal(store.getSnapshot().status, 'checking');
  assert.equal(store.getSnapshot().profile, null);
  pending.resolve(profile);
  await restore;
  assert.equal(store.getSnapshot().status, 'authenticated');
});

test('denied stored key is removed; transient restore errors retain it for retry', async () => {
  for (const status of [403, 500]) {
    const storage = memoryStorage('saved-key');
    const store = createSessionStore({ storage, loadProfile: async () => { throw Object.assign(new Error('Controlled failure'), { status }); } });
    await assert.rejects(store.restore());
    assert.equal(store.getSnapshot().status, status === 403 ? 'anonymous' : 'error');
    assert.equal(storage.getItem('spotpool.apiKey'), status === 403 ? null : 'saved-key');
  }
});

test('late authentication response cannot restore a logged out session', async () => {
  const pending = deferred();
  const storage = memoryStorage();
  const store = createSessionStore({ storage, loadProfile: () => pending.promise });
  const login = store.signIn('fake-key');
  store.signOut();
  pending.resolve(profile);
  await assert.rejects(login, (error) => error.name === 'AbortError');
  assert.equal(store.getSnapshot().status, 'anonymous');
  assert.equal(storage.getItem('spotpool.apiKey'), null);
});

test('newer login wins over a delayed older login', async () => {
  const old = deferred();
  const storage = memoryStorage();
  const store = createSessionStore({ storage, loadProfile: (key) => key === 'old' ? old.promise : Promise.resolve({ ...profile, userId: 'new-user' }) });
  const first = store.signIn('old');
  await store.signIn('new');
  old.resolve(profile);
  await assert.rejects(first, (error) => error.name === 'AbortError');
  assert.equal(store.getSnapshot().profile.userId, 'new-user');
  assert.equal(storage.getItem('spotpool.apiKey'), 'new');
});

test('failed replacement key keeps an existing valid session', async () => {
  const store = createSessionStore({ storage: memoryStorage(), loadProfile: async (key) => {
    if (key === 'bad') throw Object.assign(new Error('Denied'), { status: 403 });
    return profile;
  } });
  await store.signIn('good');
  await assert.rejects(store.signIn('bad'));
  assert.equal(store.getApiKey(), 'good');
  assert.equal(store.getSnapshot().status, 'authenticated');
});

test('storage failure falls back to memory and refresh preserves valid requests', async () => {
  const store = createSessionStore({ storage: { setItem() { throw new Error('Blocked'); } }, loadProfile: async () => profile });
  await store.signIn('fake-key');
  assert.equal(store.getSnapshot().storageAvailable, false);
  const signal = store.getSessionSignal();
  await store.refreshProfile();
  assert.equal(signal.aborted, false);
  assert.equal(store.getSnapshot().status, 'authenticated');
});

test('cancelled initial restore can restart, including StrictMode cleanup', async () => {
  const old = deferred();
  let count = 0;
  const store = createSessionStore({ storage: memoryStorage('saved'), loadProfile: () => ++count === 1 ? old.promise : Promise.resolve(profile) });
  const first = store.restore();
  store.cancelPending();
  await store.restore();
  old.resolve(profile);
  await assert.rejects(first, (error) => error.name === 'AbortError');
  assert.equal(store.getSnapshot().status, 'authenticated');
});

test('revoked profile invalidates a live session on refresh', async () => {
  let denied = false;
  const store = createSessionStore({ storage: memoryStorage(), loadProfile: async () => {
    if (denied) throw Object.assign(new Error('Denied'), { status: 401 });
    return profile;
  } });
  await store.signIn('fake-key');
  const signal = store.getSessionSignal();
  denied = true;
  await assert.rejects(store.refreshProfile());
  assert.equal(store.getSnapshot().status, 'anonymous');
  assert.equal(signal.aborted, true);
});

test('return route stays internal and does not loop to login', () => {
  assert.equal(safeReturnPath('/account'), '/account');
  assert.equal(safeReturnPath('/jobs?status=RUNNING'), '/jobs?status=RUNNING');
  for (const invalid of ['https://example.com', '//example.com', '/\\example.com', '/login', '/register', undefined]) {
    assert.equal(safeReturnPath(invalid), '/jobs');
  }
});

test('refresh failure preserves an existing profile and blank key does not request', async () => {
  let count = 0;
  const store = createSessionStore({ storage: memoryStorage(), loadProfile: async () => {
    if (++count > 1) throw Object.assign(new Error('Temporary outage'), { status: 500 });
    return profile;
  } });
  await assert.rejects(store.signIn('   '));
  assert.equal(count, 0);
  await store.signIn('fake-key');
  await assert.rejects(store.refreshProfile());
  assert.equal(store.getSnapshot().profile, profile);
  assert.equal(store.getSnapshot().status, 'authenticated');
});
