import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, createApiClient } from '../src/api/client.js';
import { createUsersApi, parseProfile } from '../src/api/users.js';

const profile = {
  userId: 'test-user', email: '', role: 'user', creditAvailable: 0, creditHeld: 0,
  runningJobs: 0, maxConcurrentJobs: 2, createdAt: 1793491200,
};

test('profile uses Bearer key and registration does not send Authorization', async () => {
  const calls = [];
  const client = createApiClient({ baseUrl: 'https://api.example/prod/', fetchImpl: async (url, options) => {
    calls.push({ url, ...options });
    return new Response(JSON.stringify(url.endsWith('/users/me') ? profile : { userId: 'new-user', apiKey: 'fake-new-key' }), { status: url.endsWith('/users/me') ? 200 : 201 });
  } });
  const users = createUsersApi(client);
  assert.deepEqual(await users.getCurrentUser('fake-key'), profile);
  await users.registerUser('test@example.com');
  assert.equal(calls[0].url, 'https://api.example/prod/users/me');
  assert.equal(calls[0].headers.Authorization, 'Bearer fake-key');
  assert.equal(calls[0].cache, 'no-store');
  assert.equal(calls[1].headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(calls[1].body), { email: 'test@example.com' });
});

test('public profile drops secrets and preserves zero credit', () => {
  const parsed = parseProfile({ ...profile, apiKeyHash: 'private', internal: 'private' });
  assert.equal(parsed.creditAvailable, 0);
  assert.equal(parsed.apiKeyHash, undefined);
  assert.equal(parsed.internal, undefined);
  assert.throws(() => parseProfile({ ...profile, role: 'superadmin' }), ApiError);
  assert.throws(() => parseProfile({ ...profile, creditAvailable: '0' }), ApiError);
});

test('a failed POST is never retried and network details are not exposed', async () => {
  let count = 0;
  const users = createUsersApi(createApiClient({ baseUrl: 'https://api.example', fetchImpl: async () => {
    count += 1;
    throw new Error('private network diagnostics');
  } }));
  await assert.rejects(users.registerUser(''), (error) => error.code === 'NETWORK' && !error.message.includes('private'));
  assert.equal(count, 1);
});

test('backend 5xx and invalid JSON produce controlled errors', async () => {
  const client = createApiClient({ baseUrl: 'https://api.example', fetchImpl: async () => new Response('{"error":"internal database traceback"}', { status: 500 }) });
  await assert.rejects(client.request('/users/me'), (error) => error.status === 500 && !error.message.includes('traceback'));
  const malformed = createApiClient({ baseUrl: 'https://api.example', fetchImpl: async () => new Response('<html>gateway</html>') });
  await assert.rejects(malformed.request('/users/me'), (error) => error.code === 'INVALID_RESPONSE');
});

test('timeout aborts the request and is distinguished from session cancellation', async () => {
  const client = createApiClient({ baseUrl: 'https://api.example', timeoutMs: 10, fetchImpl: (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  }) });
  await assert.rejects(client.request('/users/me'), (error) => error.code === 'TIMEOUT');
  const controller = new AbortController();
  const request = client.request('/users/me', { sessionSignal: controller.signal });
  controller.abort();
  await assert.rejects(request, (error) => error.name === 'AbortError');
});

test('already aborted session does not fetch and missing config is actionable', async () => {
  let count = 0;
  const controller = new AbortController();
  controller.abort();
  const client = createApiClient({ baseUrl: 'https://api.example', fetchImpl: async () => { count += 1; return new Response('{}'); } });
  await assert.rejects(client.request('/users/me', { sessionSignal: controller.signal }), (error) => error.name === 'AbortError');
  assert.equal(count, 0);
  await assert.rejects(createApiClient({}).request('/users/me'), (error) => error.code === 'CONFIG');
});

test('incomplete registration response is an uncertain outcome', async () => {
  const users = createUsersApi(createApiClient({ baseUrl: 'https://api.example', fetchImpl: async () => new Response('{"userId":"new-user"}', { status: 201 }) }));
  await assert.rejects(users.registerUser(''), (error) => error.code === 'INVALID_RESPONSE');
});

test('a response that arrives after logout cannot escape cancellation', async () => {
  let releaseBody;
  const body = new Promise((resolve) => { releaseBody = resolve; });
  const controller = new AbortController();
  const client = createApiClient({ baseUrl: 'https://api.example', fetchImpl: async () => ({ ok: true, text: () => body }) });
  const request = client.request('/users/me', { sessionSignal: controller.signal });
  controller.abort();
  releaseBody(JSON.stringify(profile));
  await assert.rejects(request, (error) => error.name === 'AbortError');
});
