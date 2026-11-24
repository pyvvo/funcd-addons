---
paths:
  - "**/*.yaml"
  - "**/*.mjs"
  - "**/*.py"
  - "**/*.go"
---

# An add-on's Functions

How the add-ons of this repository are written. It is guidance for what ships here: a user may configure an add-on
otherwise.

1. **Private, when only funcd calls it.** A Function answers at its own address, `/function/<name>`, unless its spec, or
   its namespace's `defaultExposure: explicit`, turns that address off (funcd ADR-0110, ADR-0241). One that funcd alone
   calls turns it off in its spec: an Addon's `function.ref`, an object's `spec.functions`, an App hook, a Sensor's
   target. funcd's own calls need no address. One that a person or a page calls keeps its address, and names its stance
   in `ingress.auth`.

   ```yaml
   ingress:
     enabled: false
   ```

2. **It uses what funcd has.** A handler, a plugin or an entrypoint builds no second schedule, retry, check or store:
   two mechanisms for one job drift (funcd's `.claude/rules/reuse-before-design.md`). What funcd lacks is a funcd ADR,
   as `CLAUDE.md` says. InfraStack's cron parser
   ([#15](https://github.com/pyvvo/funcd-addons/pull/15)) is the example to avoid: funcd ADR-0211's grammar in a second
   language, kept in step by hand.

   | The job | What already does it |
   |---|---|
   | Run again later | the kind's `resync`; an EventSource timer and a Sensor for a schedule (funcd ADR-0108, ADR-0109) |
   | Try again | a call that fails is sent again after a backoff, and `ok: false` at the resync (funcd ADR-0341) |
   | Check an object's fields | the kind's schema and constraints (funcd ADR-0340) |
   | Check a call's data | the Function's contract (funcd ADR-0303) |
   | Learn that a run ended | the cause `run`, then `ctx.job.get` (funcd ADR-0345, ADR-0349) |
   | Reach an engine, a Job or a Secret | its binding, which Cedar governs (funcd ADR-0338, ADR-0349, ADR-0321) |
   | Copy an engine's data | the Addon's `snapshot` and `restore`, and a BackupSchedule (funcd ADR-0333) |
3. **A kind's handler keeps funcd's terms**, in the guide's
   [The kind's Function](https://github.com/pyvvo/funcd/blob/main/docs/guide/develop/services/addons/build.md#the-kinds-function):
   it applies the whole list, the same way each time it is sent, answers each object by its name, and takes a cause it
   does not know. A settings kind's puts the engine's default back for an empty list. An engineless kind's destroys an
   object sent with `deleting: true`, and forgets one that left the list.
