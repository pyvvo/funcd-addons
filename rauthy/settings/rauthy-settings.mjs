// The Function of rauthy's settings kind RauthyPasswordPolicy (funcd ADR-0341, ADR-0358): funcd creates it from its image
// for each IdentityProvider that a policy names and sends it that policy, and it puts the policy into rauthy through its
// binding of the IdentityProvider (funcd ADR-0338), or rauthy's default when the list is empty. It throws when it cannot
// put the default back, so funcd calls it again instead of removing it. rauthy holds one policy per instance, so the kind
// allows one per IdentityProvider. It signs in with the API key settings, which funcdctl rauthy keys has rauthy create.

const DEFAULT = {
  length_min: 14,
  length_max: 128,
  include_lower_case: 1,
  include_upper_case: 1,
  include_digits: 1,
  include_special: null,
  valid_days: 180,
  not_recently_used: 3,
};

const request = (c) => ({
  length_min: c.lengthMin,
  length_max: c.lengthMax,
  include_lower_case: c.lowerCase ?? null,
  include_upper_case: c.upperCase ?? null,
  include_digits: c.digits ?? null,
  include_special: c.special ?? null,
  valid_days: c.validDays ?? null,
  not_recently_used: c.notRecentlyUsed ?? null,
});

// put puts policy into the rauthy bound as alias, and answers why it could not, or null.
const put = async (ctx, alias, policy) => {
  const secret = await ctx.secret.get('BOOTSTRAP_API_KEY_SECRET');
  let res;
  try {
    res = await ctx.addon.fetch(alias, '/auth/v1/password_policy', {
      method: 'PUT',
      headers: { Authorization: `API-Key settings$${secret}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(policy),
    });
  } catch (err) {
    return { reason: 'EngineUnreachable', message: String(err?.message ?? err) };
  }
  if (res.ok) return null;
  return { reason: 'EngineRefused', message: `rauthy answered ${res.status}: ${(await res.text()).slice(0, 300)}` };
};

export const handle = async (ctx, event) => {
  const { target, objects } = event.data;
  const failed = await put(ctx, target.alias, objects.length > 0 ? request(objects[0].config) : DEFAULT);
  if (failed && objects.length === 0) throw new Error(`rauthy's default password policy is not back: ${failed.message}`);
  return { results: objects.map((o) => ({ name: o.name, ok: !failed, ...failed })) };
};
