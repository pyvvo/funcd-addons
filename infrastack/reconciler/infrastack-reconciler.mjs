// The Function of the InfraStack kind (funcd ADR-0345, ADR-0358): funcd creates it from its image for each stack and sends
// it that stack, and it drives the Job that the stack binds as stack (funcd ADR-0349). A stack gets one run per generation, which applies it, and once it is deleting with deletion: delete, one run that destroys
// it; each run previews its plan and waits for a person's approval unless its Job says approval.bypass (funcd
// ADR-0348). A stack reports its run: Ready once the run of its generation succeeded, and a deleting stack goes once its
// destroy succeeded. A run's name comes from the stack's uid and generation, so a call sent again starts no new run,
// and a destroy starts only once the apply run of the stack's generation has ended. Every cause is handled alike.
//
// A stack whose config.drift holds a cron (funcd ADR-0211's grammar, in UTC) is checked for drift once it is applied:
// at its latest due time since its apply ended, a run with action plan (funcd ADR-0355 Decision 5), named after that
// time, one at a time; a finding that would change anything is the stack's Drifted.

// The alias under which a stack binds its Job.
const JOB = 'stack';
const NAME_MAX = 63;
const MINUTE = 60_000;
// How far back a due time is looked for: an accepted cron fires within four years, 29 February's included.
const MAX_DAYS = 4 * 366 + 1;
// How many of a finding's addresses a Drifted message names.
const SHOWN = 5;
// How many due times a look for the stack's latest check reads back over, one read each. The look is bounded because
// ctx.job has no list (funcd ADR-0349) and a check that nobody approves never ends (funcd ADR-0348 checks expiry only
// at an approval), so an unbounded look would grow with the time a check waits.
const LOOKBACK = 100;

const ENDED = new Set(['Succeeded', 'Failed', 'Cancelled', 'Rejected']);

const START_REFUSED = {
  403: 'JobNotBound',
  404: 'JobNotFound',
  409: 'RunNameTaken',
  413: 'InputTooLarge',
  422: 'InvalidVariables',
};

// runName is the stack's run of action at its generation: <name>-g<generation>-<uid> or <name>-d<generation>-<uid>, a
// DNS label of at most 63 characters.
export const runName = (stack, action) => {
  const uid = String(stack.uid).toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 8);
  const tag = `${action === 'destroy' ? 'd' : 'g'}${stack.generation}-${uid}`;
  const base = stack.name.slice(0, NAME_MAX - tag.length - 1).replace(/-+$/, '');
  return `${base}-${tag}`;
};

const waiting = (stack, run, action) => {
  const verb = action === 'destroy' ? 'destroys' : 'applies';
  switch (run.phase) {
    case 'AwaitingApproval':
      return {
        reason: 'AwaitingApproval',
        message: `run ${run.name}, which ${verb} ${stack.name}, awaits approval: funcdctl job approve ${run.name}`,
      };
    case 'Running':
      return { reason: action === 'destroy' ? 'Destroying' : 'Applying', message: `run ${run.name} ${verb} it` };
    case 'Previewing':
      return { reason: action === 'destroy' ? 'Destroying' : 'Planning', message: `run ${run.name} plans its change` };
    default:
      return { reason: action === 'destroy' ? 'Destroying' : 'Queued', message: `run ${run.name} is ${run.phase}` };
  }
};

const ended = (run, action) => {
  const word = action === 'destroy' ? 'Destroy' : 'Apply';
  const reason = { Failed: `${word}Failed`, Cancelled: `${word}Cancelled`, Rejected: `${word}Rejected` }[run.phase];
  return { reason, message: `run ${run.name} ${run.phase.toLowerCase()}${run.reason ? `: ${run.reason}` : ''}` };
};

// variablesOf is a run's variables: none when the stack sets none (absent, null or {}), since a module with no plain
// variable takes void variables (funcd ADR-0355 Decision 3, amended 2026-11-24); anything else as it is, so the run's
// creation refuses a malformed one.
const variablesOf = (stack) => {
  const v = stack.config?.variables;
  const none = v === undefined || v === null || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0);
  return none ? {} : { variables: v };
};

// drive starts the stack's run of action, again harmlessly when it exists, and reads it back.
const drive = async (ctx, stack, action) => {
  const name = runName(stack, action);
  const input = { action, ...variablesOf(stack) };
  try {
    await ctx.job.start(JOB, input, name);
    return { run: await ctx.job.get(JOB, name) };
  } catch (err) {
    const reason = START_REFUSED[err?.status] ?? 'RunStartFailed';
    return { failed: { reason, message: `run ${name} of the Job bound as ${JOB}: ${err?.message ?? err}` } };
  }
};

// applyRun reads the apply run of the stack's generation, or null when it was never started.
const applyRun = async (ctx, stack) => {
  try {
    return await ctx.job.get(JOB, runName(stack, 'apply'));
  } catch (err) {
    if (err?.status === 404) return null;
    throw err;
  }
};

const MACROS = {
  '@yearly': '0 0 1 1 *',
  '@annually': '0 0 1 1 *',
  '@monthly': '0 0 1 * *',
  '@weekly': '0 0 * * 0',
  '@daily': '0 0 * * *',
  '@midnight': '0 0 * * *',
  '@hourly': '0 * * * *',
};

const FIELDS = [
  ['minute', 0, 59],
  ['hour', 0, 23],
  ['day of month', 1, 31],
  ['month', 1, 12],
  ['day of week', 0, 7],
];

export const GRAMMAR =
  'five fields, minute hour day-of-month month day-of-week, each a comma list of *, n or a-b, the * and a-b with an ' +
  'optional /step, or one of @yearly, @annually, @monthly, @weekly, @daily, @midnight and @hourly (funcd ADR-0211)';

const field = (text, [name, lo, hi]) => {
  const values = new Set();
  for (const item of text.split(',')) {
    const m = /^(?:(\*)|(\d+)(?:-(\d+))?)(?:\/(\d+))?$/.exec(item);
    if (!m || (m[4] !== undefined && m[2] !== undefined && m[3] === undefined)) {
      throw new Error(`the ${name} field holds ${JSON.stringify(item)}`);
    }
    const from = m[1] ? lo : Number(m[2]);
    const to = m[1] ? hi : Number(m[3] ?? m[2]);
    const step = Number(m[4] ?? 1);
    if (from < lo || to > hi || from > to || step < 1) {
      throw new Error(`the ${name} field holds ${JSON.stringify(item)}, outside ${lo}-${hi}`);
    }
    for (let v = from; v <= to; v += step) values.add(v);
  }
  return values;
};

const DAYS_IN = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

// parseCron parses a cron of funcd ADR-0211's grammar, refusing what it refuses, an expression that never fires
// included, and throws an Error that names why.
export const parseCron = (text) => {
  const expr = MACROS[String(text).trim()] ?? String(text).trim();
  const parts = expr.split(/[ \t]+/);
  if (parts.length !== 5) throw new Error(`it has ${parts.length} fields`);
  const [minutes, hours, doms, months, dows] = parts.map((p, i) => field(p, FIELDS[i]));
  if (dows.delete(7)) dows.add(0);
  const or = !parts[2].startsWith('*') && !parts[4].startsWith('*');
  if (!or && ![...months].some((month) => [...doms].some((dom) => dom <= DAYS_IN[month - 1]))) {
    throw new Error('its days of month and months share no date');
  }
  return { minutes, hours, doms, months, dows, or };
};

const firesOn = (cron, day) => {
  if (!cron.months.has(day.getUTCMonth() + 1)) return false;
  const dom = cron.doms.has(day.getUTCDate());
  const dow = cron.dows.has(day.getUTCDay());
  return cron.or ? dom || dow : dom && dow;
};

// latestDue is the latest time at or before at, to the minute, at which cron fires in UTC, or null.
export const latestDue = (cron, at) => {
  const now = new Date(Math.floor(at.getTime() / MINUTE) * MINUTE);
  for (let back = 0; back <= MAX_DAYS; back++) {
    const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - back));
    if (!firesOn(cron, day)) continue;
    for (let hour = back === 0 ? now.getUTCHours() : 23; hour >= 0; hour--) {
      if (!cron.hours.has(hour)) continue;
      const last = back === 0 && hour === now.getUTCHours() ? now.getUTCMinutes() : 59;
      for (let minute = last; minute >= 0; minute--) {
        if (cron.minutes.has(minute)) {
          return new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), hour, minute));
        }
      }
    }
  }
  return null;
};

// driftRunName is the stack's drift check due at due: <name>-c<generation>-<YYYYMMDDHHMM>-<uid>, a DNS label of at
// most 63 characters.
export const driftRunName = (stack, due) => {
  const uid = String(stack.uid).toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 8);
  const stamp = due.toISOString().slice(0, 16).replace(/[-T:]/g, '');
  const tag = `c${stack.generation}-${stamp}-${uid}`;
  const base = stack.name.slice(0, NAME_MAX - tag.length - 1).replace(/-+$/, '');
  return `${base}-${tag}`;
};

const getRun = async (ctx, name) => {
  try {
    return await ctx.job.get(JOB, name);
  } catch (err) {
    if (err?.status === 404) return null;
    throw err;
  }
};

// finding is a drift check's report: Drifted when its plan would change anything, else Ready.
const finding = (run) => {
  if (run.phase !== 'Succeeded') {
    return { ok: false, reason: 'DriftCheckFailed', message: `drift check ${run.name} ${run.phase.toLowerCase()}${run.reason ? `: ${run.reason}` : ''}` };
  }
  const plan = run.result?.plan ?? {};
  const counts = ['add', 'change', 'destroy', 'replace'].filter((k) => plan[k] > 0).map((k) => `${plan[k]} to ${k}`);
  if (counts.length === 0) return { ok: true };
  const resources = plan.resources ?? [];
  const more = resources.length > SHOWN ? ` and ${resources.length - SHOWN} more` : '';
  return {
    ok: false,
    reason: 'Drifted',
    message: `drift check ${run.name} finds ${counts.join(', ')}: ${resources.slice(0, SHOWN).join(', ')}${more}`,
  };
};

const awaiting = (run) => ({
  ok: false,
  reason: 'AwaitingApproval',
  message: `drift check ${run.name} awaits approval: funcdctl job approve ${run.name}`,
});

// latestCheck is the stack's latest check due before from and at or after applied, with its due time, or null when
// none of the LOOKBACK due times before from has one.
const latestCheck = async (ctx, stack, cron, from, applied) => {
  let at = from;
  for (let left = LOOKBACK; left > 0; left--) {
    at = latestDue(cron, new Date(at.getTime() - MINUTE));
    if (!at || at < applied) return null;
    const run = await getRun(ctx, driftRunName(stack, at));
    if (run) return { run, at };
  }
  return null;
};

// drift checks an applied stack against its config.drift: at its latest due time since the apply ended, it starts a
// plan run named after that time unless the latest check before it has not ended, and reports the latest check that
// has ended, or one that awaits approval.
const drift = async (ctx, stack, apply, now) => {
  let cron;
  try {
    cron = parseCron(stack.config.drift);
  } catch (err) {
    return { ok: false, reason: 'ConfigInvalid', message: `config.drift: ${err.message}; a cron is ${GRAMMAR}` };
  }
  const applied = apply.endedAt ? new Date(apply.endedAt) : new Date(0);
  const due = latestDue(cron, now);
  if (!due || due < applied) return { ok: true };
  const name = driftRunName(stack, due);
  // lastFound is the finding of the latest check before from, when it has ended.
  const lastFound = async (from) => {
    const last = await latestCheck(ctx, stack, cron, from, applied);
    return last && ENDED.has(last.run.phase) ? finding(last.run) : { ok: true };
  };
  try {
    const run = await getRun(ctx, name);
    if (run) {
      if (ENDED.has(run.phase)) return finding(run);
      return run.phase === 'AwaitingApproval' ? awaiting(run) : lastFound(due);
    }
    const last = await latestCheck(ctx, stack, cron, due, applied);
    if (last && !ENDED.has(last.run.phase)) {
      return last.run.phase === 'AwaitingApproval' ? awaiting(last.run) : lastFound(last.at);
    }
    await ctx.job.start(JOB, { action: 'plan', ...variablesOf(stack) }, name);
    const started = await ctx.job.get(JOB, name);
    if (ENDED.has(started.phase)) return finding(started);
    if (started.phase === 'AwaitingApproval') return awaiting(started);
    return last ? finding(last.run) : { ok: true };
  } catch (err) {
    if (err?.status === 422) {
      return {
        ok: false,
        reason: 'DriftCheckRefused',
        message: `drift check ${name} of the Job bound as ${JOB}: ${err?.message ?? err}; a module pushed ` +
          'before funcd ADR-0355 takes no action plan: push it again with funcdctl infra push',
      };
    }
    const reason = START_REFUSED[err?.status] ?? 'RunStartFailed';
    return { ok: false, reason, message: `drift check ${name} of the Job bound as ${JOB}: ${err?.message ?? err}` };
  }
};

const reconcileOne = async (ctx, stack, now) => {
  if (stack.deleting) {
    let before;
    try {
      before = await applyRun(ctx, stack);
    } catch (err) {
      return { ok: false, reason: 'RunReadFailed', message: String(err?.message ?? err) };
    }
    if (before && !ENDED.has(before.phase)) {
      const w = waiting(stack, before, 'apply');
      return { ok: false, reason: w.reason, message: `${w.message}; its destroy starts once that run ends` };
    }
  }
  const action = stack.deleting ? 'destroy' : 'apply';
  const { run, failed } = await drive(ctx, stack, action);
  if (failed) return { ok: false, ...failed };
  if (run.phase === 'Succeeded') return action === 'apply' && stack.config.drift ? drift(ctx, stack, run, now) : { ok: true };
  return { ok: false, ...(ENDED.has(run.phase) ? ended(run, action) : waiting(stack, run, action)) };
};

// reconcileAt answers a call at the time now.
export const reconcileAt = async (ctx, event, now) => {
  const stacks = event.data?.objects ?? [];
  const results = [];
  for (const stack of stacks) {
    results.push({ name: stack.name, ...(await reconcileOne(ctx, stack, now)) });
  }
  return { results };
};

export const handle = (ctx, event) => reconcileAt(ctx, event, new Date());
