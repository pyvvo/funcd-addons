// rauthy's settings Function with a stand-in for ctx.addon and ctx.secret (funcd ADR-0338), which records each request.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { handle } from './rauthy-settings.mjs';

const rauthy = (answer = () => new Response('{}', { status: 200 })) => {
  const requests = [];
  return {
    requests,
    secret: { get: async (key) => `${key}-value` },
    addon: {
      async fetch(alias, path, init) {
        requests.push({ alias, path, method: init.method, auth: init.headers.Authorization, body: JSON.parse(init.body) });
        return answer();
      },
    },
  };
};

const call = (ctx, objects) =>
  handle(ctx, {
    data: { kind: 'RauthyPasswordPolicy', cause: 'change', target: { kind: 'IdentityProvider', name: 'login', alias: 'self' }, objects },
  });

const strict = {
  name: 'strict',
  uid: '7bc5ed56',
  generation: 3,
  config: { provider: 'login', lengthMin: 16, lengthMax: 64, digits: 1 },
};

test('a policy is put into the target, with the API key settings', async () => {
  const ctx = rauthy();
  assert.deepEqual(await call(ctx, [strict]), { results: [{ name: 'strict', ok: true }] });
  assert.deepEqual(ctx.requests, [
    {
      alias: 'self',
      path: '/auth/v1/password_policy',
      method: 'PUT',
      auth: 'API-Key settings$BOOTSTRAP_API_KEY_SECRET-value',
      body: {
        length_min: 16,
        length_max: 64,
        include_lower_case: null,
        include_upper_case: null,
        include_digits: 1,
        include_special: null,
        valid_days: null,
        not_recently_used: null,
      },
    },
  ]);
});

test("an empty list puts rauthy's default back", async () => {
  const ctx = rauthy();
  assert.deepEqual(await call(ctx, []), { results: [] });
  assert.equal(ctx.requests[0].body.length_min, 14);
});

test('a policy that rauthy refuses, or that cannot be sent, is not ok', async () => {
  const refused = rauthy(() => new Response('too short', { status: 400 }));
  assert.deepEqual(await call(refused, [strict]), {
    results: [{ name: 'strict', ok: false, reason: 'EngineRefused', message: 'rauthy answered 400: too short' }],
  });
  const gone = rauthy(() => {
    throw new Error('503 EngineNotReady');
  });
  assert.deepEqual(await call(gone, [strict]), {
    results: [{ name: 'strict', ok: false, reason: 'EngineUnreachable', message: '503 EngineNotReady' }],
  });
});

test('a default that is not back fails the call, so funcd sends it again', async () => {
  const refused = rauthy(() => new Response('no', { status: 500 }));
  await assert.rejects(call(refused, []), /default password policy is not back: rauthy answered 500: no/);
  const gone = rauthy(() => {
    throw new Error('503 EngineNotReady');
  });
  await assert.rejects(call(gone, []), /503 EngineNotReady/);
});
