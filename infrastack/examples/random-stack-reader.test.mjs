import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';

import { handle } from './random-stack-reader.mjs';

const sha256 = (text) => createHash('sha256').update(text).digest('hex');

// ctx.secret as a Function's bound Secrets answer it: a key no bound Secret holds reads as undefined.
const contextOf = (data) => ({ secret: { get: async (key) => data[key] } });

test('it answers the plain output and the sha256 of the sensitive one', async () => {
  const got = await handle(contextOf({ name: 'example-ant', token: 's3cret' }), { data: null });
  assert.deepEqual(got, { name: 'example-ant', tokenSha256: sha256('s3cret') });
});

test('each call reads the Secret again, so a later apply reaches the next call', async () => {
  const data = { name: 'example-ant', token: 's3cret' };
  const ctx = contextOf(data);
  await handle(ctx, { data: null });
  data.name = 'changed-bee';
  assert.equal((await handle(ctx, { data: null })).name, 'changed-bee');
});

test('before the first apply, an output reads as empty', async () => {
  assert.deepEqual(await handle(contextOf({}), { data: null }), { name: '', tokenSha256: sha256('') });
});
