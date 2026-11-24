// The Function of the InfraStack kind (funcd ADR-0345): funcd sends it every stack of the namespace that it reconciles,
// and it drives each stack's Job through its binding whose alias the stack's config.job names (funcd ADR-0349). A stack
// gets one run per generation, which applies it, and once it is deleting with deletion: delete, one run that destroys
// it; each run previews its plan and waits for a person's approval unless its Job says approval.bypass (funcd
// ADR-0348). A stack reports its run: Ready once the run of its generation succeeded, and a deleting stack goes once its
// destroy succeeded. A run's name comes from the stack's uid and generation, so a call sent again starts no new run,
// and a destroy starts only once the apply run of the stack's generation has ended. Every cause is handled alike.

const NAME_MAX = 63;

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

// drive starts the stack's run of action, again harmlessly when it exists, and reads it back.
const drive = async (ctx, stack, action) => {
  const name = runName(stack, action);
  const input = { action, variables: stack.config?.variables ?? {} };
  try {
    await ctx.job.start(stack.config.job, input, name);
    return { run: await ctx.job.get(stack.config.job, name) };
  } catch (err) {
    const reason = START_REFUSED[err?.status] ?? 'RunStartFailed';
    return { failed: { reason, message: `run ${name} of the Job bound as ${stack.config.job}: ${err?.message ?? err}` } };
  }
};

// applyRun reads the apply run of the stack's generation, or null when it was never started.
const applyRun = async (ctx, stack) => {
  try {
    return await ctx.job.get(stack.config.job, runName(stack, 'apply'));
  } catch (err) {
    if (err?.status === 404) return null;
    throw err;
  }
};

const reconcileOne = async (ctx, stack) => {
  if (!stack.config?.job) {
    return { ok: false, reason: 'ConfigInvalid', message: 'config.job names no Job binding' };
  }
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
  if (run.phase === 'Succeeded') return { ok: true };
  return { ok: false, ...(ENDED.has(run.phase) ? ended(run, action) : waiting(stack, run, action)) };
};

export const reconcile = async (ctx, event) => {
  const stacks = event.data?.objects ?? [];
  const results = [];
  for (const stack of stacks) {
    results.push({ name: stack.name, ...(await reconcileOne(ctx, stack)) });
  }
  return { results };
};
