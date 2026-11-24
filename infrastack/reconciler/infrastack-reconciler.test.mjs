// The InfraStack reconciler with a stand-in for ctx.job (funcd ADR-0349), whose runs a test moves through their phases.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { driftRunName, GRAMMAR, latestDue, parseCron, reconcile, reconcileAt, runName } from './infrastack-reconciler.mjs';

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

// Drift checks (F141): a stack whose config.drift holds a cron, its apply run done at APPLIED.
const APPLIED = '2026-11-24T08:30:00Z';
const at = (iso) => new Date(iso);
const drifting = (extra = {}) => stack({ config: { job: 'bunny-edge', variables: { zone: 'funcd-tunnel' }, drift: '0 * * * *' }, ...extra });
const callAt = (ctx, objects, iso) => reconcileAt(ctx, { data: { kind: 'InfraStack', cause: 'resync', objects } }, at(iso));

// applied is a ctx whose stack's generation run succeeded at APPLIED.
const applied = () => {
  const ctx = jobs();
  ctx.runs.set('bunny-edge-g3-9c2e41aa', { name: 'bunny-edge-g3-9c2e41aa', phase: 'Succeeded', reason: '', endedAt: APPLIED });
  return ctx;
};

const check = (ctx, iso) => ctx.runs.get(driftRunName(drifting(), at(iso)));

test('the cron grammar is funcd ADR-0211\'s', () => {
  for (const text of ['*/15 9-17 * * 1-5', '0 3 * * 7', '0 0 1,15 * *', '5 4 * 2-6/2 *', '@daily', '@weekly', '0 0 29 2 *']) {
    assert.doesNotThrow(() => parseCron(text), text);
  }
  for (const text of ['0 0 * *', '0 0 * * * 2026', '5/10 * * * *', '*/0 * * * *', '60 * * * *', '0 24 * * *', '0 0 0 * *',
    '0 0 * 13 *', '0 0 * * 8', '* * * * MON', '0 0 L * *', '@every 1h', '0 0 31 2 *', '5-1 * * * *']) {
    assert.throws(() => parseCron(text), Error, text);
  }
  assert.doesNotThrow(() => parseCron('0\t3 *\t* *'), 'tabs separate fields too');
  assert.throws(() => parseCron('@DAILY'), Error, 'a macro is lowercase');
  assert.deepEqual([...parseCron('5 4 * 2-6/2 *').months], [2, 4, 6], 'a step on a range');
  assert.deepEqual([...parseCron('10-40/15 * * * *').minutes], [10, 25, 40]);
  assert.match(GRAMMAR, /ADR-0211/);
});

test('each macro is its five fields', () => {
  const due = (text) => latestDue(parseCron(text), at('2026-11-24T10:07:30Z')).toISOString();
  assert.equal(due('@hourly'), '2026-11-24T10:00:00.000Z');
  assert.equal(due('@daily'), '2026-11-24T00:00:00.000Z');
  assert.equal(due('@midnight'), '2026-11-24T00:00:00.000Z');
  assert.equal(due('@weekly'), '2026-11-22T00:00:00.000Z');
  assert.equal(due('@monthly'), '2026-11-01T00:00:00.000Z');
  assert.equal(due('@yearly'), '2026-01-01T00:00:00.000Z');
  assert.equal(due('@annually'), '2026-01-01T00:00:00.000Z');
});

test('a due time is the latest minute at or before now at which the cron fires, in UTC', () => {
  const due = (text, now) => latestDue(parseCron(text), at(now))?.toISOString();
  assert.equal(due('*/15 9-17 * * 1-5', '2026-11-24T10:07:30Z'), '2026-11-24T10:00:00.000Z');
  assert.equal(due('*/15 9-17 * * 1-5', '2026-11-24T08:59:00Z'), '2026-11-23T17:45:00.000Z', 'the day before');
  assert.equal(due('0 * * * *', '2026-11-24T10:00:00Z'), '2026-11-24T10:00:00.000Z', 'a due time on the minute is due');
  assert.equal(due('0 0 29 2 *', '2026-11-24T10:00:00Z'), '2024-02-29T00:00:00.000Z', 'once in four years');
  assert.equal(due('0 0 13 * 5', '2026-11-24T10:00:00Z'), '2026-11-20T00:00:00.000Z', 'both day fields: either day fires');
  assert.equal(due('0 0 13 * *', '2026-11-24T10:00:00Z'), '2026-11-13T00:00:00.000Z');
  assert.equal(due('0 0 * * 7', '2026-11-24T10:00:00Z'), '2026-11-22T00:00:00.000Z', '7 is Sunday');
  assert.equal(due('10-40/15 9 * * *', '2026-11-24T09:30:00Z'), '2026-11-24T09:25:00.000Z', 'a step on a range');
  assert.equal(due('0\t3 * * *', '2026-11-24T10:00:00Z'), '2026-11-24T03:00:00.000Z', 'fields split at a tab');
});

test('a stack with no config.drift is checked for no drift', async () => {
  const ctx = applied();
  assert.deepEqual((await callAt(ctx, [stack()], '2026-11-24T12:00:00Z')).results, [{ name: 'bunny-edge', ok: true }]);
  assert.equal(ctx.runs.size, 1);
});

test('at its due time an applied stack starts one plan run, named after that time, again harmlessly', async () => {
  const ctx = applied();
  const { results } = await callAt(ctx, [drifting()], '2026-11-24T10:07:00Z');
  assert.deepEqual(ctx.started.at(-1), {
    alias: 'bunny-edge',
    input: { action: 'plan', variables: { zone: 'funcd-tunnel' } },
    name: 'bunny-edge-c3-202611241000-9c2e41aa',
  });
  assert.deepEqual(results, [{ name: 'bunny-edge', ok: true }], 'a check in flight keeps the stack Ready');
  await callAt(ctx, [drifting()], '2026-11-24T10:08:00Z');
  assert.equal(ctx.runs.size, 2, 'a call sent again starts nothing new');
  await callAt(ctx, [drifting()], '2026-11-24T08:59:00Z');
  assert.equal(ctx.runs.size, 2, 'a due time before the apply ended starts no check');
});

test('a finding that would change anything is Drifted, naming the run, the counts and the resources', async () => {
  const ctx = applied();
  await callAt(ctx, [drifting()], '2026-11-24T10:07:00Z');
  const resources = ['random_pet.name', 'null_resource.a', 'null_resource.b', 'null_resource.c', 'null_resource.d', 'null_resource.e'];
  Object.assign(check(ctx, '2026-11-24T10:00:00Z'), {
    phase: 'Succeeded', result: { plan: { add: 1, change: 0, destroy: 0, replace: 5, resources } },
  });
  const { results } = await callAt(ctx, [drifting()], '2026-11-24T10:09:00Z');
  assert.deepEqual(results, [{
    name: 'bunny-edge', ok: false, reason: 'Drifted',
    message: 'drift check bunny-edge-c3-202611241000-9c2e41aa finds 1 to add, 5 to replace: random_pet.name, ' +
      'null_resource.a, null_resource.b, null_resource.c, null_resource.d and 1 more',
  }]);
  Object.assign(check(ctx, '2026-11-24T10:00:00Z'), { result: { plan: { add: 0, change: 0, destroy: 0, replace: 0, resources: [] } } });
  assert.deepEqual((await callAt(ctx, [drifting()], '2026-11-24T10:10:00Z')).results, [{ name: 'bunny-edge', ok: true }]);
});

test('the next check keeps the last finding while it runs', async () => {
  const ctx = applied();
  await callAt(ctx, [drifting()], '2026-11-24T10:07:00Z');
  Object.assign(check(ctx, '2026-11-24T10:00:00Z'), { phase: 'Succeeded', result: { plan: { add: 0, change: 1, destroy: 0, replace: 0, resources: ['random_pet.name'] } } });
  const { results } = await callAt(ctx, [drifting()], '2026-11-24T11:01:00Z');
  assert.ok(check(ctx, '2026-11-24T11:00:00Z'), 'the next due time starts its check');
  assert.equal(results[0].reason, 'Drifted', 'the last finding stands while the next check runs');
  check(ctx, '2026-11-24T11:00:00Z').phase = 'Running';
  assert.equal((await callAt(ctx, [drifting()], '2026-11-24T11:02:00Z')).results[0].reason, 'Drifted');
});

test('a check that awaits approval says so, and the next due time waits for it', async () => {
  const ctx = applied();
  await callAt(ctx, [drifting()], '2026-11-24T10:07:00Z');
  check(ctx, '2026-11-24T10:00:00Z').phase = 'AwaitingApproval';
  const { results } = await callAt(ctx, [drifting()], '2026-11-24T10:30:00Z');
  assert.deepEqual(results, [{
    name: 'bunny-edge', ok: false, reason: 'AwaitingApproval',
    message: 'drift check bunny-edge-c3-202611241000-9c2e41aa awaits approval: funcdctl job approve bunny-edge-c3-202611241000-9c2e41aa',
  }]);
  const { results: later } = await callAt(ctx, [drifting()], '2026-11-24T11:05:00Z');
  assert.equal(check(ctx, '2026-11-24T11:00:00Z'), undefined, 'one check at a time');
  assert.equal(later[0].reason, 'AwaitingApproval');
  check(ctx, '2026-11-24T10:00:00Z').phase = 'Running';
  assert.deepEqual((await callAt(ctx, [drifting()], '2026-11-24T11:06:00Z')).results, [{ name: 'bunny-edge', ok: true }]);
  assert.equal(check(ctx, '2026-11-24T11:00:00Z'), undefined);
});

test('a due time skipped while a check runs holds the next one too, and its finding is reported once it ends', async () => {
  const ctx = applied();
  await callAt(ctx, [drifting()], '2026-11-24T10:07:00Z');
  check(ctx, '2026-11-24T10:00:00Z').phase = 'Running';
  for (const iso of ['2026-11-24T11:05:00Z', '2026-11-24T12:05:00Z', '2026-11-24T13:05:00Z']) {
    assert.deepEqual((await callAt(ctx, [drifting()], iso)).results, [{ name: 'bunny-edge', ok: true }], iso);
  }
  assert.equal(ctx.runs.size, 2, 'one check at a time, over every skipped due time');
  Object.assign(check(ctx, '2026-11-24T10:00:00Z'), {
    phase: 'Succeeded', result: { plan: { add: 0, change: 1, destroy: 0, replace: 0, resources: ['random_pet.name'] } },
  });
  const { results } = await callAt(ctx, [drifting()], '2026-11-24T13:10:00Z');
  assert.ok(check(ctx, '2026-11-24T13:00:00Z'), 'the latest due time starts its check once the one before has ended');
  assert.deepEqual(results, [{
    name: 'bunny-edge', ok: false, reason: 'Drifted',
    message: 'drift check bunny-edge-c3-202611241000-9c2e41aa finds 1 to change: random_pet.name',
  }], 'the ended check stands while the next one runs');
  check(ctx, '2026-11-24T13:00:00Z').phase = 'Running';
  assert.equal((await callAt(ctx, [drifting()], '2026-11-24T14:05:00Z')).results[0].reason, 'Drifted');
  assert.equal(ctx.runs.size, 3);
});

test('a check that awaits approval holds every later due time, and says so', async () => {
  const ctx = applied();
  await callAt(ctx, [drifting()], '2026-11-24T10:07:00Z');
  check(ctx, '2026-11-24T10:00:00Z').phase = 'AwaitingApproval';
  const { results } = await callAt(ctx, [drifting()], '2026-11-24T16:05:00Z');
  assert.deepEqual(results, [{
    name: 'bunny-edge', ok: false, reason: 'AwaitingApproval',
    message: 'drift check bunny-edge-c3-202611241000-9c2e41aa awaits approval: funcdctl job approve bunny-edge-c3-202611241000-9c2e41aa',
  }]);
  assert.equal(ctx.runs.size, 2);
});

test('a check that has not ended 100 due times on holds the next one no more', async () => {
  const ctx = applied();
  const minutely = drifting({ config: { job: 'bunny-edge', variables: { zone: 'funcd-tunnel' }, drift: '* * * * *' } });
  await callAt(ctx, [minutely], '2026-11-24T09:00:00Z');
  ctx.runs.get(driftRunName(minutely, at('2026-11-24T09:00:00Z'))).phase = 'Running';
  await callAt(ctx, [minutely], '2026-11-24T10:40:00Z');
  assert.equal(ctx.runs.size, 2, 'the check 100 due times back still holds the next one');
  await callAt(ctx, [minutely], '2026-11-24T10:41:00Z');
  assert.ok(ctx.runs.get(driftRunName(minutely, at('2026-11-24T10:41:00Z'))), '101 due times back, it holds no more');
});

test('a check that ended without success is DriftCheckFailed, naming how', async () => {
  for (const phase of ['Failed', 'Rejected', 'Cancelled']) {
    const ctx = applied();
    await callAt(ctx, [drifting()], '2026-11-24T10:07:00Z');
    Object.assign(check(ctx, '2026-11-24T10:00:00Z'), { phase, reason: 'PreviewFailed' });
    const { results } = await callAt(ctx, [drifting()], '2026-11-24T10:09:00Z');
    assert.deepEqual(results, [{
      name: 'bunny-edge', ok: false, reason: 'DriftCheckFailed',
      message: `drift check bunny-edge-c3-202611241000-9c2e41aa ${phase.toLowerCase()}: PreviewFailed`,
    }], phase);
  }
});

test('a stack not yet applied, or deleting, starts no check', async () => {
  const ctx = jobs();
  await callAt(ctx, [drifting()], '2026-11-24T10:07:00Z');
  assert.deepEqual(ctx.started.map((s) => s.name), ['bunny-edge-g3-9c2e41aa'], 'only the apply run');
  const done = applied();
  await callAt(done, [drifting({ deleting: true })], '2026-11-24T10:07:00Z');
  assert.deepEqual(done.started.map((s) => s.input.action), ['destroy']);
  Object.assign(done.runs.get('bunny-edge-d3-9c2e41aa'), { phase: 'Succeeded', endedAt: '2026-11-24T10:08:00Z' });
  const { results } = await callAt(done, [drifting({ deleting: true })], '2026-11-24T11:01:00Z');
  assert.deepEqual(results, [{ name: 'bunny-edge', ok: true }], 'a destroyed stack goes');
  assert.deepEqual(done.started.map((s) => s.input.action), ['destroy', 'destroy'], 'and is checked for no drift');
});

test('a refused check names its cause, a contract that refuses it is DriftCheckRefused, and a config.drift off the grammar is ConfigInvalid', async () => {
  const ctx = applied();
  const start = ctx.job.start;
  ctx.job.start = async (alias, input, name) => {
    if (input.action === 'plan') throw new JobError(403, 'refused 403');
    return start(alias, input, name);
  };
  const { results } = await callAt(ctx, [drifting()], '2026-11-24T10:07:00Z');
  assert.equal(results[0].reason, 'JobNotBound');
  assert.match(results[0].message, /drift check bunny-edge-c3-202611241000-9c2e41aa/);
  const old = applied();
  const oldStart = old.job.start;
  old.job.start = async (alias, input, name) => {
    if (input.action === 'plan') throw new JobError(422, 'the input does not match the contract: action must be apply or destroy');
    return oldStart(alias, input, name);
  };
  const { results: older } = await callAt(old, [drifting()], '2026-11-24T10:07:00Z');
  assert.deepEqual(older, [{
    name: 'bunny-edge', ok: false, reason: 'DriftCheckRefused',
    message: 'drift check bunny-edge-c3-202611241000-9c2e41aa of the Job bound as bunny-edge: the input does not match ' +
      'the contract: action must be apply or destroy; a module pushed before funcd ADR-0355 takes no action plan: push ' +
      'it again with funcdctl infra push',
  }]);
  const bad = drifting({ config: { job: 'bunny-edge', drift: '@every 1h' } });
  const { results: refused } = await callAt(applied(), [bad], '2026-11-24T10:07:00Z');
  assert.equal(refused[0].reason, 'ConfigInvalid');
  assert.match(refused[0].message, /^config\.drift: it has 2 fields; a cron is five fields/);
});

test("a drift check's name is a DNS label of at most 63 characters, of the generation and the due time", () => {
  const long = stack({ name: `${'a'.repeat(50)}-${'b'.repeat(12)}`, generation: 12345 });
  const name = driftRunName(long, at('2026-11-24T10:00:00Z'));
  assert.ok(name.length <= 63, name);
  assert.match(name, /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/);
  assert.match(name, /-c12345-202611241000-9c2e41aa$/);
  assert.notEqual(driftRunName(stack(), at('2026-11-24T10:00:00Z')), driftRunName(stack({ generation: 4 }), at('2026-11-24T10:00:00Z')));
});
