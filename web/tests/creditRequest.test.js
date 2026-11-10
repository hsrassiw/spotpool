import test from 'node:test';
import assert from 'node:assert/strict';
import { createCreditRequestStore } from '../src/api/creditRequest.js';

test('uncertain requests survive a new page instance and stay scoped to user and operation', () => {
  const entries = new Map();
  const storage = { getItem: (key) => entries.get(key), setItem: (key, value) => entries.set(key, value), removeItem: (key) => entries.delete(key) };
  const original = { requestId: '11111111-1111-4111-8111-111111111111', amount: 5000, jobId: 'job', busy: true };
  createCreditRequestStore('user-a', 'topup', storage).save(original);
  assert.deepEqual(createCreditRequestStore('user-a', 'topup', storage).load(), { requestId: original.requestId, amount: 5000, jobId: 'job' });
  assert.equal(createCreditRequestStore('user-b', 'topup', storage).load(), null);
  assert.equal(createCreditRequestStore('user-a', 'deposit', storage).load(), null);
  createCreditRequestStore('user-a', 'topup', storage).save(null);
  assert.equal(createCreditRequestStore('user-a', 'topup', storage).load(), null);
});

test('blocked or corrupted storage does not prevent using the finance page', () => {
  assert.equal(createCreditRequestStore('u', 'deposit', { getItem: () => '{invalid' }).load(), null);
  const blocked = createCreditRequestStore('u', 'deposit', {
    getItem() { throw new Error('disabled'); }, setItem() { throw new Error('disabled'); }, removeItem() { throw new Error('disabled'); },
  });
  assert.equal(blocked.load(), null);
  assert.doesNotThrow(() => blocked.save({ amount: 5000, jobId: '', requestId: 'id' }));
});
