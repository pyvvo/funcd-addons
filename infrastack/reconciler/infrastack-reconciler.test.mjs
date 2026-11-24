// The InfraStack reconciler with a stand-in for ctx.job (funcd ADR-0349), whose runs a test moves through their phases.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { reconcile, runName } from './infrastack-reconciler.mjs';

class JobError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const jobs = (opts = {}) => {
  const runs = new Map();
  const started = [];
  return {
    runs,
    started,
    job: {
      async start(alias, input, name) {
        if (opts.refuse) throw new JobError(opts.refuse, `refused ${opts.refuse}`);
        started.push({ alias, input, name });
        if (!runs.has(name)) runs.set(name, { name, phase: 'Pending', reason: '' });
        return name;
      },
      async get(alias, name) {
        const run = runs.get(name);
        if (!run) throw new JobError(404, `no run ${name}`);
        return { ...run };
      },
    },
  };
};

const stack = (extra = {}) => ({
  name: 'bunny-edge',
  uid: '9c2e41aa-5b1d-4c6e-9f00-0123456789ab',
  generation: 3,
  deleting: false,
  config: { job: 'bunny-edge', variables: { zone: 'funcd-tunnel' } },
  ...extra,
});

const call = (ctx, objects, cause = 'change') => reconcile(ctx, { data: { kind: 'InfraStack', cause, objects } });

test('a stack starts the run of its generation, named after it, with its variables', async () => {
  const ctx = jobs();
  const { results } = await call(ctx, [stack()]);
  assert.deepEqual(ctx.started, [
    { alias: 'bunny-edge', input: { action: 'apply', variables: { zone: 'funcd-tunnel' } }, name: 'bunny-edge-g3-9c2e41aa' },
  ]);
  assert.deepEqual(results, [{ name: 'bunny-edge', ok: false, reason: 'Queued', message: 'run bunny-edge-g3-9c2e41aa is Pending' }]);
});

test('a stack reports its run: planning, awaiting approval, applying, then Ready', async () => {
  const ctx = jobs();
  await call(ctx, [stack()]);
  const run = ctx.runs.get('bunny-edge-g3-9c2e41aa');
  for (const [phase, reason] of [['Previewing', 'Planning'], ['AwaitingApproval', 'AwaitingApproval'], ['Running', 'Applying']]) {
    run.phase = phase;
    const { results } = await call(ctx, [stack()], 'resync');
    assert.equal(results[0].reason, reason, phase);
  }
  run.phase = 'AwaitingApproval';
  const { results: waiting } = await call(ctx, [stack()]);
  assert.match(waiting[0].message, /funcdctl job approve bunny-edge-g3-9c2e41aa/);
  run.phase = 'Succeeded';
  assert.deepEqual((await call(ctx, [stack()], 'run')).results, [{ name: 'bunny-edge', ok: true }]);
  assert.equal(ctx.runs.size, 1, 'every call starts the same run');
});

test('a run that ended without success names how', async () => {
  for (const [phase, reason] of [['Failed', 'ApplyFailed'], ['Rejected', 'ApplyRejected'], ['Cancelled', 'ApplyCancelled']]) {
    const ctx = jobs();
    await call(ctx, [stack()]);
    Object.assign(ctx.runs.get('bunny-edge-g3-9c2e41aa'), { phase, reason: 'HandlerFailed' });
    const { results } = await call(ctx, [stack()]);
    assert.equal(results[0].reason, reason);
    assert.match(results[0].message, /HandlerFailed/);
  }
});

test('a new generation starts its own run', async () => {
  const ctx = jobs();
  await call(ctx, [stack()]);
  await call(ctx, [stack({ generation: 4 })]);
  assert.deepEqual([...ctx.runs.keys()], ['bunny-edge-g3-9c2e41aa', 'bunny-edge-g4-9c2e41aa']);
});

test('a deleting stack waits for its apply run, then destroys, and goes once the destroy succeeded', async () => {
  const ctx = jobs();
  await call(ctx, [stack()]);
  ctx.runs.get('bunny-edge-g3-9c2e41aa').phase = 'AwaitingApproval';
  const { results: held } = await call(ctx, [stack({ deleting: true })]);
  assert.equal(held[0].reason, 'AwaitingApproval');
  assert.match(held[0].message, /its destroy starts once that run ends/);
  assert.equal(ctx.runs.size, 1, 'no destroy while the apply run waits');

  ctx.runs.get('bunny-edge-g3-9c2e41aa').phase = 'Rejected';
  const { results: destroying } = await call(ctx, [stack({ deleting: true })]);
  assert.deepEqual(ctx.started.at(-1), {
    alias: 'bunny-edge',
    input: { action: 'destroy', variables: { zone: 'funcd-tunnel' } },
    name: 'bunny-edge-d3-9c2e41aa',
  });
  assert.equal(destroying[0].reason, 'Destroying');
  ctx.runs.get('bunny-edge-d3-9c2e41aa').phase = 'AwaitingApproval';
  assert.match((await call(ctx, [stack({ deleting: true })])).results[0].message, /destroys bunny-edge, awaits approval/);
  ctx.runs.get('bunny-edge-d3-9c2e41aa').phase = 'Failed';
  assert.equal((await call(ctx, [stack({ deleting: true })])).results[0].reason, 'DestroyFailed');
  ctx.runs.get('bunny-edge-d3-9c2e41aa').phase = 'Succeeded';
  assert.deepEqual((await call(ctx, [stack({ deleting: true })])).results, [{ name: 'bunny-edge', ok: true }]);
});

test('a deleting stack that never ran destroys at once', async () => {
  const ctx = jobs();
  await call(ctx, [stack({ deleting: true })]);
  assert.deepEqual(ctx.started.map((s) => s.name), ['bunny-edge-d3-9c2e41aa']);
});

test('a refused start names its cause', async () => {
  for (const [status, reason] of [[403, 'JobNotBound'], [404, 'JobNotFound'], [422, 'InvalidVariables'], [500, 'RunStartFailed']]) {
    const { results } = await call(jobs({ refuse: status }), [stack()]);
    assert.equal(results[0].reason, reason, String(status));
    assert.match(results[0].message, /bunny-edge-g3-9c2e41aa/);
  }
});

test('a stack whose config names no Job is refused alone', async () => {
  const ctx = jobs();
  const { results } = await call(ctx, [stack({ name: 'a', config: {} }), stack()]);
  assert.equal(results[0].reason, 'ConfigInvalid');
  assert.equal(results[1].reason, 'Queued');
});

test('a run name is a DNS label of at most 63 characters', () => {
  const long = stack({ name: `${'a'.repeat(50)}-${'b'.repeat(12)}`, generation: 12345 });
  for (const action of ['apply', 'destroy']) {
    const name = runName(long, action);
    assert.ok(name.length <= 63, name);
    assert.match(name, /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/);
  }
});
