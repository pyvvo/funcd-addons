// random-stack's outputs, read by a Function that binds the Secret the stack's Job owns: through ctx.secret, from funcd
// at each call, so a later apply's value reaches the next call (funcd ADR-0356 Decision 6). It answers the plain output
// name, and only the sha256 of the sensitive token.
import { createHash } from 'node:crypto';

export const handle = async (ctx) => {
  const [name, token] = await Promise.all([ctx.secret.get('name'), ctx.secret.get('token')]);
  return { name: name ?? '', tokenSha256: createHash('sha256').update(token ?? '').digest('hex') };
};
