// The Function of rauthy's settings kind RauthyPasswordPolicy (funcd ADR-0341): funcd sends it every policy that names
// one IdentityProvider, and it puts that policy into rauthy through its binding of the IdentityProvider (funcd
// ADR-0338), or rauthy's default when the list is empty. rauthy holds one policy per instance, so the kind allows one
// per IdentityProvider. It signs in with the API key settings, which funcdctl rauthy keys has rauthy create.

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

const all = (objects, result) => ({ results: objects.map((o) => ({ name: o.name, ...result })) });

export const reconcile = async (ctx, event) => {
  const call = event.data;
  const objects = call.objects ?? [];
  const secret = await ctx.secret.get('BOOTSTRAP_API_KEY_SECRET');
  const policy = objects.length > 0 ? request(objects[0].config) : DEFAULT;
  let res;
  try {
    res = await ctx.addon.fetch(call.target.alias, '/auth/v1/password_policy', {
      method: 'PUT',
      headers: { Authorization: `API-Key settings$${secret}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(policy),
    });
  } catch (err) {
    return all(objects, { ok: false, reason: 'EngineUnreachable', message: String(err?.message ?? err) });
  }
  if (!res.ok) {
    const text = (await res.text()).slice(0, 300);
    return all(objects, { ok: false, reason: 'EngineRefused', message: `rauthy answered ${res.status}: ${text}` });
  }
  return all(objects, { ok: true });
};
