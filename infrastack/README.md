# InfraStack

An add-on that [manages infrastructure outside funcd](https://github.com/pyvvo/funcd/blob/main/docs/feat/0012-feat-infrastack.md)
with OpenTofu. Its kind, `InfraStack`, runs no engine: for each stack, funcd creates a Function from the Addon's
image, the reconciler, which drives the stack's Job on the add-on's OpenTofu runtime. Each run previews its plan and
waits for a person's
[approval](https://github.com/pyvvo/funcd/blob/main/docs/adr/0348-a-run-waits-for-a-persons-approval-of-its-preview-unless-its-job-says-bypass.md)
unless the Job says `approval.bypass`, then applies only the
[plan that the person approved](docs/runs.md#the-preview-and-the-apply).

The smallest stack is [`examples/random-stack.yaml`](examples/random-stack.yaml). Once a person approves its run, the
stack turns `Ready`, and [`funcdctl infra outputs`](docs/plugin.md#funcdctl-infra-outputs) prints its outputs.

## Register the add-on

An admin registers it from a release's `infrastack.yaml`, as the [root README](../README.md#register-an-add-on)
shows. [`app/addon.yaml`](app/addon.yaml) ships two runtimes:

- `opentofu1` serves Jobs: funcd's Python shim, OpenTofu and its providers, and the handler of `runtime/`.
- `infrastack` serves `Engineless`. It adds the kind `InfraStack`, whose Function is the image of `reconciler/`, with
  a resync of one minute, so a run that turns `AwaitingApproval` shows on its stack within a minute.

A stack's [state](docs/runs.md#the-state) goes through funcd's S3 gateway. With `server.network.egress` on, the Job
reaches its cloud's API only under an EgressPolicy that names it (`appliesTo` kind `Job`), and the S3 gateway only
when `server.network.internalAllow` lists the gateway's bridge address and port, such as `10.63.0.1:9000`.

## Declare a stack

A stack is five objects of one namespace, as in `examples/random-stack.yaml`:

| Object | Holds |
|---|---|
| a Bucket prefix | the stack's state, its lock and its saved plans; its owner is the stack's Job |
| a ConfigMap | `INFRASTACK_STATE_BUCKET`, `INFRASTACK_STATE_PREFIX` and, optionally, `INFRASTACK_OUTPUTS_SECRET` |
| a Secret | the stack's outputs, plain and sensitive, one key each, written by its Job, which owns it |
| the Job | the module as its image, on `opentofu1`, with the blob binding `state`, the ConfigMap and the Secrets |
| the InfraStack | `bindings.job`, the Job as `stack`, `config.variables` and `deletion` |

The InfraStack's `spec.config` holds `variables`, the module's variables, and `drift`, an optional schedule of
[drift checks](#check-a-stack-for-drift). The Job's image is the module, pushed with
[`funcdctl infra push`](docs/plugin.md#funcdctl-infra-push), whose contract gives a run its
[input and output](docs/runs.md#input-and-output). The Job's bound Secrets give OpenTofu its environment, such as
`BUNNYNET_API_KEY` or `TF_VAR_token` in [`examples/token-stack.yaml`](examples/token-stack.yaml), with
[two exceptions](docs/runs.md#the-environment).

## Approve and destroy

The reconciler starts one run per generation of the stack. While the run awaits approval, the stack shows
[`AwaitingApproval`](docs/reconciler.md#status):
[`funcdctl infra approve <stack>`](docs/plugin.md#funcdctl-infra-approve) shows the run's plan and asks to confirm.

With `deletion: delete`, a deleting stack waits for the run of its generation to end. The reconciler then starts the
destroy run, which waits for approval too, and the stack goes once that run succeeded.

## Read the outputs

After the apply, the runtime writes every [output](docs/runs.md#the-outputs), plain or sensitive, into the Secret
that `INFRASTACK_OUTPUTS_SECRET` names. A sensitive output is in no result, preview, status or log line.

- A Function that binds the outputs Secret reads each output through `ctx.secret`, a changed one at its next read,
  with no new revision, as [`examples/random-stack-reader.yaml`](examples/random-stack-reader.yaml) does.
- An add-on object that binds it gets in its engine's environment the keys that its Addon lists in `secretKeys`, and
  a changed value replaces the engine.

## Check a stack for drift

A stack whose `config.drift` holds a cron of [funcd's grammar](docs/reconciler.md#the-cron), in UTC, is checked for
drift once the run of its generation has applied it.

A [check](docs/reconciler.md#drift-checks) is a run with `action: plan`, which applies nothing. It waits for a
person's approval unless its Job says `approval.bypass`, so a stack checked on a schedule has a Job with `bypass`, or
someone who approves each check. The stack shows `Drifted` once a check finds a change, until a later check finds
none or a new generation applies.

## The plugin

`funcdctl-infra`, [attached to each release](docs/plugin.md#install), is the add-on's
[funcdctl plugin](https://github.com/pyvvo/funcd/tree/main/docs/guide/develop/tools/funcdctl/plugins.md): on PATH,
`funcdctl infra` runs it. Its [commands](docs/plugin.md) push a module, print a stack's outputs and approve a stack's
run.

## In an App

An App holds a stack as it holds the five objects above, its
[Job](https://github.com/pyvvo/funcd/blob/main/docs/adr/0350-an-app-declares-jobs-as-it-declares-functions.md) included.
Its template's `images` name the module's bundle, since a template's Job takes only its own images, so `app/` holds
the Addon alone. funcd-functions' [frp-bunny App](https://github.com/pyvvo/funcd-functions/tree/main/apps/frp-bunny)
is one.

A stack that is not `Ready` is a
[`Lagging` part](https://github.com/pyvvo/funcd/blob/main/docs/adr/0357-an-app-never-waits-for-its-stacks-and-a-part-waits-for-their-outputs.md)
of its App, and the App's post-hooks and prune wait while it lags. A [drift check](docs/reconciler.md#in-an-app)
counts the same.

## Build and check

Build and check the add-on's [files](docs/build.md#files):

```sh
nix develop -c just test-infrastack
nix develop -c just opentofu-image <funcd-python>/shim
nix develop -c just opentofu-image-check
nix develop -c just funcdctl
nix develop -c just infra-push-check
```

CI runs [`opentofu-image-check` and `infra-push-check`](docs/build.md#the-checks). A [release](docs/build.md#releases)
publishes the OpenTofu runtime and the reconciler as images.

## Not yet

- A waiting apply run of a deleted stack that never applied stays approvable: reject it with `funcdctl job reject`.
