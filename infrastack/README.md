# InfraStack

An add-on that manages infrastructure outside funcd with OpenTofu (funcd FEAT-0012 F141). Its kind, `InfraStack`, runs
no engine (funcd ADR-0345): a Function, the reconciler, which funcd creates for each stack from the Addon's image (funcd
ADR-0358), drives the stack's Job, and the Job runs on the add-on's OpenTofu runtime (funcd ADR-0344). Each run previews
its plan and waits for a person's approval unless the Job says `approval.bypass` (funcd ADR-0348), and it applies only
the plan that the person approved.

```text
infrastack/
├── README.md
├── app/
│   └── addon.yaml
├── examples/
│   ├── random-stack/
│   │   ├── main.tf
│   │   └── random-stack.contract.yaml
│   ├── random-stack-reader.contract.yaml
│   ├── random-stack-reader.mjs
│   ├── random-stack-reader.test.mjs
│   ├── random-stack-reader.yaml
│   ├── random-stack.yaml
│   ├── token-stack/
│   │   └── main.tf
│   └── token-stack.yaml
├── plugin/
│   ├── funcdctl-infra
│   ├── plugin_test.py
│   ├── push_check.py
│   └── stamp.py
├── reconciler/
│   ├── infrastack-reconciler.contract.yaml
│   ├── infrastack-reconciler.mjs
│   └── infrastack-reconciler.test.mjs
└── runtime/
    ├── Dockerfile
    ├── entry.py
    ├── fetch.py
    ├── handler.py
    ├── handler_test.py
    ├── image_check.py
    ├── image_test.sh
    └── pins.json
```

## The Addon

`app/addon.yaml` ships two runtimes. `opentofu1` serves Jobs: funcd's Python shim, OpenTofu and its providers, and the
handler of `runtime/`. `infrastack` serves `Engineless`: it adds the kind `InfraStack`, whose Function is the image of
`reconciler/`, with a resync of one minute, so a run that turns `AwaitingApproval` shows on its stack within a minute.
A stack binds its Job in `spec.bindings.job`, under the alias `stack`, and its `spec.config` holds `variables`, the
module's variables, and `drift`, an optional schedule of drift checks; the reconciler's contract is its schema.

## A stack

A stack is five objects of one namespace; `examples/random-stack.yaml` holds them all:

| Object | Holds |
|---|---|
| a Bucket prefix | the stack's state, its lock and its saved plans; its owner is the stack's Job |
| a ConfigMap | `INFRASTACK_STATE_BUCKET`, `INFRASTACK_STATE_PREFIX` and, optionally, `INFRASTACK_OUTPUTS_SECRET` |
| a Secret | the stack's outputs, plain and sensitive, one key each, written by its Job, which owns it |
| the Job | the module as its image, on `opentofu1`, with the blob binding `state`, the ConfigMap and the Secrets |
| the InfraStack | `bindings.job`, the Job as `stack`, `config.variables` and `deletion` |

The Job's image is the module, pushed with `funcdctl infra push <module> <ref>` (below). A run's input is
`{"action": "apply" | "destroy" | "plan", "variables": {...}}`; its output is `{"outputs": {...}}` for an apply, `{}`
for a destroy and `{"plan": {...}}` for a plan run (funcd ADR-0355 Decision 3). A module pushed by hand, `funcdctl push
<module> <ref> --entry main.tf` beside a `<module>.contract.yaml` written in that shape, runs as well, with the image's
providers.

`examples/token-stack.yaml` is a stack whose module takes its sensitive variable `token` from the Secret its Job binds,
as `TF_VAR_token`, and `examples/random-stack-reader.yaml` is a Function that reads random-stack's outputs through
`ctx.secret`.

The Job's bound Secrets give OpenTofu its environment: each key in upper case, such as `BUNNYNET_API_KEY`, and each
`TF_VAR_<variable>`. A key that starts with `AWS_` never reaches OpenTofu, since the state's S3 key pair takes that
name, and neither does a key named as one of the module's outputs, which the runtime reads with `tofu show -json
-module`, so an output never sets a later run's variable or a provider's setting.

With `server.network.egress` on, the Job reaches its cloud's API only under an EgressPolicy that names it
(`appliesTo` kind `Job`, funcd ADR-0351), and the S3 gateway only when `server.network.internalAllow` lists the
gateway's bridge address and port, such as `10.63.0.1:9000`.

## The plugin

`funcdctl-infra`, attached to each release, is the add-on's funcdctl plugin (funcd ADR-0319, ADR-0355): on PATH,
`funcdctl infra` runs it. It needs Python 3.9 or later, and `docker` or `nerdctl`, the first on PATH; it runs OpenTofu
in the image of the Addon's `opentofu1` runtime, which each release stamps into it, or in the image that
`INFRASTACK_OPENTOFU_IMAGE` names, and copies files in and out with `cp`, with no bind mount. It reaches funcd through
`$FUNCDCTL`.

- **`funcdctl infra push <module> <ref>`** pushes a module folder. `<ref>` is `<registry>/<repo>:<tag>` or
  `oci-layout://<dir>:<tag>`. It refuses a folder that holds `terraform.tfstate`, `terraform.tfstate.backup`,
  `terraform.tfvars`, `terraform.tfvars.json`, `*.auto.tfvars` or `*.auto.tfvars.json` outside `.terraform/`, and a
  module call whose source is not a relative path inside the folder, such as a registry module or `../shared`, naming
  it. It locks the providers for `linux_amd64` and `linux_arm64` into the module's `.terraform.lock.hcl`, to commit,
  then pushes one bundle per platform, `<ref>-linux-amd64` and `<ref>-linux-arm64`, each with that platform's
  provider packages in `.funcd-providers/` and a contract made from the module, and joins them with `funcdctl index`.
  It prints `<ref>@<digest>`.
- **The contract** has the input `action`, one of `apply`, `destroy` and `plan`, and `variables`, a closed record of
  the module's variables, the ones with no default required, and `variables` itself required only when one is. A
  sensitive variable is left out, so no run's input holds it: its value comes from a bound Secret's `TF_VAR_<name>`, as
  in `examples/token-stack.yaml`. A module with no plain variable takes `variables: {"type": "null"}`, a side that
  takes nothing as funcd ADR-0090 has a Function's, so a run's input sets none. The output holds
  `outputs`, the plain outputs, none required, and `plan`, a plan run's finding. A wrong variable fails the run's
  creation with 422, naming its path, and the stack shows `InvalidVariables`.
- **`funcdctl infra outputs <stack> [-n <namespace>] [-o json]`** prints each key of the stack's outputs Secret, with
  its value when the Job's contract names it among the plain outputs, else `(sensitive)` (funcd ADR-0356 Decision 8):

  ```text
  NAME           VALUE
  pullzone_host  frp-lane.b-cdn.net
  FRP_TOKEN      (sensitive)
  ```

  With `-o json` it prints `{"outputs": {"pullzone_host": "frp-lane.b-cdn.net"}, "sensitive": ["FRP_TOKEN"]}`.
- **`funcdctl infra approve <stack> [-n <namespace>]`** approves the run that the stack waits on: the apply run of its
  generation or, while the stack is being deleted, its destroy run. It finds the run that the stack's Ready condition
  names, then runs `funcdctl job approve`, which shows the run's plan and asks to confirm. It refuses a stack that
  awaits no approval, with the stack's status message.

## A run

- **The preview** copies the module into a work folder, without the variables and state files the push refuses, adds
  a backend override, and runs `tofu init` against the state, then `tofu plan -out`. It keeps the saved plan beside the
  state, at `plans/<run>.tfplan`, and answers what the plan adds, changes, replaces and destroys, by address, with
  `planDigest`, the plan's sha256.
- **The apply**, once approved, reads that saved plan, refuses it when its sha256 is not the approved document's
  `planDigest`, and runs `tofu apply` on it. OpenTofu refuses a plan made before the state last changed. A Job with
  `approval.bypass` plans and applies in one call.
- **A plan run** (`action: plan`) previews as any run does but keeps no saved plan; the run plans its variables
  against the state again, applies nothing, writes no output and answers `{"plan": {"add", "change", "destroy",
  "replace", "resources"}}`, the counts and the first 100 addresses that it would change. A Job without
  `approval.bypass` asks a person to approve such a run too.
- **The outputs** (funcd ADR-0356): once the apply is done, the runtime writes every output, plain or sensitive, into
  the Secret that `INFRASTACK_OUTPUTS_SECRET` names, one `ctx.secret.put` each, a string as it is and any other value
  as its JSON. It answers only after the last write, with `{"outputs": {...}}`, the plain ones. A sensitive output is
  in no result, preview, status or log line. A write that funcd refuses, such as for a Secret the Job does not own,
  fails the run, naming the output and the Secret but no value; the change stays in the state, and the stack's next
  apply writes every output again. With no `INFRASTACK_OUTPUTS_SECRET`, the plain outputs are in the result alone, and
  each sensitive one is kept nowhere, which the run logs by its name. The Job declares each key it writes, so the
  Secret holds each key, empty, before the first apply.
- **An output's name** is its key in the Secret: a letter or `_`, then letters, digits or `_`, starting with neither
  `FUNCD_`, which no reader loads, nor `INFRASTACK_`, the runtime's own settings. Another name fails a run's first
  call before it plans, naming the output: rename it in the module. A destroy skips the check.
- **A destroy** empties each output key it read before the destroy, keeps the key, so no reader waits for it again,
  and answers `{}`. An emptying that funcd refuses is logged by the output's name, and the run goes on.
- **The readers**: a Function that binds the outputs Secret reads each output through `ctx.secret`, a changed one at
  its next read, with no new revision, as `examples/random-stack-reader.yaml` does; an add-on object that binds it gets
  in its engine's environment the keys its Addon lists in `secretKeys`, and a changed value replaces the engine.
- **The state** is the s3 backend at `<prefix>/terraform.tfstate` in the Bucket, through funcd's S3 gateway, with the
  Job's own key pair (funcd ADR-0351) in a credentials file of the run's own. The lock is OpenTofu's lock file. A Job
  with `concurrency` unset runs one run at a time (funcd ADR-0347), so a lock found at a run's start was left by an
  interrupted run: the runtime removes it with `tofu force-unlock` and goes on. A stack's Job keeps `concurrency`
  unset.
- **Encryption** is off unless a bound Secret holds `INFRASTACK_STATE_PASSPHRASE`, at least 16 characters: OpenTofu
  then encrypts the state and the saved plans with a key derived from it.
- **A run downloads nothing**: `tofu init` installs the providers from the module's `.funcd-providers/`, which the
  plugin packs, each only when it matches a hash of the module's lock file, else from the image's plugin folder. The
  image carries OpenTofu 1.13.9 and the providers bunnyway/bunnynet 0.20.2, hashicorp/random 3.6.3 and hashicorp/null
  3.2.4, each MPL-2.0, unmodified, and checked against its pinned sha256 (`runtime/pins.json`); `/opt/tofu/NOTICE`
  names their sources. A module pushed by hand that needs another provider or version fails at `tofu init`, and so
  does a package that matches no hash of the lock file, naming the provider.

## The reconciler

For the stack funcd sends, the reconciler starts the run of the stack's generation, `<stack>-g<generation>-<uid>`, and
reads it back; the start is idempotent, so a call sent again starts nothing new. The run's input carries the stack's
`config.variables` when it sets some, and no `variables` otherwise. A stack is `Ready` once that run
succeeded, and shows `Queued`, `Planning`, `AwaitingApproval` (with the `funcdctl job approve` line), `Applying`,
`ApplyFailed`, `ApplyRejected` or `ApplyCancelled` meanwhile. A deleting stack with `deletion: delete` first waits for
the run of its generation to end, then starts `<stack>-d<generation>-<uid>`, which destroys it, and goes once that run
succeeded. A refused start shows `JobNotBound`, `JobNotFound`, `InvalidVariables`, `RunNameTaken`, `InputTooLarge` or
`RunStartFailed`.

## Drift checks

A stack whose `config.drift` holds a cron is checked for drift once the run of its generation has applied it (F141).
The cron takes funcd's grammar (funcd ADR-0211): five fields, minute, hour, day of month, month and day of week, each a
comma list of `*`, `n` or `a-b`, `*` and `a-b` with an optional `/step`, or a macro such as `@daily`; it runs in UTC.
Unset, a stack is never checked; another value shows `ConfigInvalid`, naming why.

- **The check**: at the latest due time since the apply ended, the reconciler starts a run with `action: plan`, named
  `<stack>-c<generation>-<YYYYMMDDHHMM>-<uid>` after that time, so a call sent again starts nothing new. It applies
  nothing and answers what would change (funcd ADR-0355 Decision 5). One check runs at a time: a due time that comes
  while the latest check has not ended starts none, and a due time missed while funcd was down starts one check, late.
  A call finds the latest check by reading back over at most the 100 due times before the one it is at, so a check
  that has not ended 100 due times after its own holds the next one no more, which then starts beside it.
- **The approval**: a plan run waits for a person's approval as every run does unless its Job says `approval.bypass`
  (funcd ADR-0348 Decisions 1 and 2), so a stack checked on a schedule has a Job with `bypass`, or someone who
  approves each check. A check that waits shows `AwaitingApproval`, with its `funcdctl job approve` line.
- **The report**: a stack is `Ready` while its last ended check found nothing to change, and shows `Drifted` once one
  finds any, naming the run, its counts and its first five addresses, until a later check finds none or a new
  generation applies. A check that ends without success shows `DriftCheckFailed`, and one whose start the Job's
  contract refuses shows `DriftCheckRefused`, as for a module pushed before funcd ADR-0355, which takes no `action:
  plan`: push it again with `funcdctl infra push`. While a check runs, the stack keeps what the latest ended check
  found. An answer's `ok` carries no reason (funcd ADR-0341 Decision 4), so drift is a
  stack's `Ready False`.

## In an App

An App holds a stack as it holds the five objects above (funcd ADR-0350 for its Job): its template's `images` name the
module's bundle, since a template's Job takes only its own images, so `app/` holds the Addon alone. funcd-functions'
frp-bunny App is the example.

A stack that is not `Ready` is a `Lagging` part of its App (funcd ADR-0357 Decision 1), and the App's post-hooks and
prune wait while it lags (Decision 4). A drift check counts the same: a stack that shows `Drifted`,
`DriftCheckFailed`, `DriftCheckRefused` or a check's `AwaitingApproval` holds them until it is `Ready` again. A Job
with `approval.bypass` keeps a check from waiting for a person.

## Build and check

```sh
nix develop -c just test-infrastack
nix develop -c just opentofu-image <funcd-python>/shim
nix develop -c just opentofu-image-check
nix develop -c just funcdctl
nix develop -c just infra-push-check
```

`opentofu-image-check` runs the handler with OpenTofu itself, in the image, against versitygw, the S3 engine of funcd's
gateway, on an internal Docker network that reaches nothing else: a preview that adds three resources, its approved
apply, a second plan that changes nothing, a refused plan whose digest is not the approved one, a run past a planted
lock, and a destroy, with the state's encryption off and on; then a module that carries its own packed providers, with
the image's folder out of reach, one whose provider package matches no hash of its lock file, and a plan run.
`infra-push-check` pushes a module with a local child through the plugin into an OCI layout and reads back the index,
the contract and each platform's packages. CI runs both.
The release workflow publishes `ghcr.io/pyvvo/funcd-opentofu` for linux/amd64 and linux/arm64, with the shim of the
funcd-python release that `pins.json` names, and the reconciler as `ghcr.io/pyvvo/funcd-infrastack-reconciler`. `just
kind-images` writes the digest of each kind's Function, which `funcdctl push` gives the same files anywhere, into its
Addon.

## Not yet

- A waiting apply run of a deleted stack that never applied stays approvable: reject it with `funcdctl job reject`.
