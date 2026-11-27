# The reconciler

What the reconciler, the Function of the kind `InfraStack`, starts for a stack and reports on it. Its contract,
[`reconciler/infrastack-reconciler.contract.yaml`](../reconciler/infrastack-reconciler.contract.yaml), is the kind's
schema.

## Runs

For the stack that funcd sends, the reconciler starts the run of the stack's generation,
`<stack>-g<generation>-<uid>`, and reads it back. The start is idempotent, so a call sent again starts nothing new.
The run's input carries the stack's `config.variables` when it sets some, and no `variables` otherwise. A deleting
stack with `deletion: delete` first waits for the run of its generation to end, then starts
`<stack>-d<generation>-<uid>`, which destroys it, and goes once that run succeeded.

## Status

A stack is `Ready` once the run of its generation succeeded, and while its last ended drift check found nothing to
change. An answer's `ok` carries no reason, as
[ADR-0341 Decision 4](https://github.com/pyvvo/funcd/blob/main/docs/adr/0341-a-function-applies-a-targets-settings.md) has it,
so each reason below is a `Ready False`, drift included.

| Reason | When |
|---|---|
| `Queued`, `Planning`, `Applying` | The run of the stack's generation has not ended |
| `Destroying` | The destroy run of a deleting stack has not ended, and awaits no approval |
| `AwaitingApproval` | The run, the destroy run or a drift check waits for a person's approval; the message holds its `funcdctl job approve` line |
| `ApplyFailed`, `ApplyRejected`, `ApplyCancelled` | The run ended without success |
| `DestroyFailed`, `DestroyRejected`, `DestroyCancelled` | The destroy run ended without success |
| `RunReadFailed` | A deleting stack could not read the run of its generation, so its destroy has not started; the message holds the error |
| `JobNotBound`, `JobNotFound`, `InvalidVariables`, `RunNameTaken`, `InputTooLarge`, `RunStartFailed` | funcd refused the run's start |
| `ConfigInvalid` | `config.drift` holds no cron of the grammar below, and the message names why |
| `Drifted` | The last ended drift check found a change; the message names the run, its counts and its first five addresses |
| `DriftCheckFailed` | A drift check ended without success |
| `DriftCheckRefused` | The Job's contract refused a check's start, as for a module pushed before [ADR-0355](https://github.com/pyvvo/funcd/blob/main/docs/adr/0355-an-opentofu-module-is-pushed-with-its-providers-and-a-contract-from-its-variables.md), which takes no `action: plan`: push it again with `funcdctl infra push` |

## The cron

`config.drift` takes funcd's [cron grammar](https://github.com/pyvvo/funcd/blob/main/docs/adr/0211-cron-schedules.md):
five fields, minute, hour, day of month, month and day of week, each a comma list of `*`, `n` or `a-b`, `*` and `a-b`
with an optional `/step`, or a macro such as `@daily`. It runs in UTC. Unset, a stack is never checked; another value
shows `ConfigInvalid`, naming why.

## Drift checks

- **The check**: at the latest due time since the apply ended, the reconciler starts a run with `action: plan`,
  named `<stack>-c<generation>-<YYYYMMDDHHMM>-<uid>` after that time, so a call sent again starts nothing new. The
  run applies nothing and answers what would change.
- **One at a time**: a due time that comes while the latest check has not ended starts none, and a due time missed
  while funcd was down starts one check, late. A call finds the latest check by reading back over at most the 100 due
  times before the one it is at. So a check that has not ended 100 due times after its own holds the next one no
  more, which then starts beside it.
- **The approval**: a plan run waits for a person's approval as every run does unless its Job says `approval.bypass`,
  as [ADR-0348 Decisions 1 and 2](https://github.com/pyvvo/funcd/blob/main/docs/adr/0348-a-run-waits-for-a-persons-approval-of-its-preview-unless-its-job-says-bypass.md)
  have it. A check that waits shows `AwaitingApproval`, with its `funcdctl job approve` line.
- **The report**: while a check runs, the stack keeps what the latest ended check found.

## In an App

A drift check holds an App as a run does: a stack that shows `Drifted`, `DriftCheckFailed`, `DriftCheckRefused` or a
check's `AwaitingApproval` holds the App's post-hooks and prune until it is `Ready` again. A Job with
`approval.bypass` keeps a check from waiting for a person.
