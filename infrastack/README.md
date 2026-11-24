# InfraStack

An add-on that manages infrastructure outside funcd with OpenTofu (funcd FEAT-0012 F141). Its kind, `InfraStack`, runs
no engine (funcd ADR-0345): a Function of the namespace, the reconciler, drives one Job per stack, and the Job runs on
the add-on's OpenTofu runtime (funcd ADR-0344). Each run previews its plan and waits for a person's approval unless the
Job says `approval.bypass` (funcd ADR-0348), and it applies only the plan that the person approved.

```text
infrastack/
├── README.md
├── app/
│   └── addon.yaml
├── examples/
│   ├── random-stack/
│   │   ├── main.tf
│   │   └── random-stack.contract.yaml
│   └── random-stack.yaml
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
handler of `runtime/`. `infrastack` serves `Engineless`: it adds the kind `InfraStack`, whose Function is
`infrastack-reconciler`, with a resync of one minute, so a run that turns `AwaitingApproval` shows on its stack within
a minute. A stack's `spec.config` holds `job`, the alias under which the reconciler binds the stack's Job, the same as
the Job's name, and `variables`, the module's variables.

## A stack

A stack is six objects of one namespace; `examples/random-stack.yaml` holds them all:

| Object | Holds |
|---|---|
| a Bucket prefix | the stack's state, its lock and its saved plans; its owner is the stack's Job |
| a ConfigMap | `INFRASTACK_STATE_BUCKET`, `INFRASTACK_STATE_PREFIX` and, optionally, `INFRASTACK_OUTPUTS_SECRET` |
| a Secret | the stack's outputs, plain and sensitive, one key each, written by its Job, which owns it |
| the Job | the module as its image, on `opentofu1`, with the blob binding `state`, the ConfigMap and the Secrets |
| the reconciler Function | `bindings.job`: one entry per stack, its alias the Job's name |
| the InfraStack | `config.job`, `config.variables` and `deletion` |

The Job's image is the module, pushed as a bundle with its contract: `funcdctl push <module> <ref> --entry main.tf`,
beside `<module>.contract.yaml`, whose runtime is `opentofu1`. A run's input is `{"action": "apply" | "destroy",
"variables": {...}}`, so the contract's input holds both. Its output holds `outputs`, a closed record of the module's
plain outputs, none required, since a destroy answers `{}` (funcd ADR-0355 Decision 3).

The Job's bound Secrets give OpenTofu its environment: each key in upper case, such as `BUNNYNET_API_KEY`, and each
`TF_VAR_<variable>`. A key that starts with `AWS_` never reaches OpenTofu, since the state's S3 key pair takes that
name, and neither does a key named as one of the module's outputs, which the runtime reads with `tofu show -json
-module`, so an output never sets a later run's variable or a provider's setting.

With `server.network.egress` on, the Job reaches its cloud's API only under an EgressPolicy that names it
(`appliesTo` kind `Job`, funcd ADR-0351), and the S3 gateway only when `server.network.internalAllow` lists the
gateway's bridge address and port, such as `10.63.0.1:9000`.

## A run

- **The preview** copies the module into a work folder, adds a backend override, and runs `tofu init` against the
  state, then `tofu plan -out`. It keeps the saved plan beside the state, at `plans/<run>.tfplan`, and answers what the
  plan adds, changes, replaces and destroys, by address, with `planDigest`, the plan's sha256.
- **The apply**, once approved, reads that saved plan, refuses it when its sha256 is not the approved document's
  `planDigest`, and runs `tofu apply` on it. OpenTofu refuses a plan made before the state last changed. A Job with
  `approval.bypass` plans and applies in one call.
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
  its next read, with no new revision; an add-on object that binds it gets in its engine's environment the keys its
  Addon lists in `secretKeys`, and a changed value replaces the engine.
- **The state** is the s3 backend at `<prefix>/terraform.tfstate` in the Bucket, through funcd's S3 gateway, with the
  Job's own key pair (funcd ADR-0351) in a credentials file of the run's own. The lock is OpenTofu's lock file. A Job
  with `concurrency` unset runs one run at a time (funcd ADR-0347), so a lock found at a run's start was left by an
  interrupted run: the runtime removes it with `tofu force-unlock` and goes on. A stack's Job keeps `concurrency`
  unset.
- **Encryption** is off unless a bound Secret holds `INFRASTACK_STATE_PASSPHRASE`, at least 16 characters: OpenTofu
  then encrypts the state and the saved plans with a key derived from it.
- **A run downloads nothing**: the providers come from the image's plugin folder. The image carries OpenTofu 1.13.9 and
  the providers bunnyway/bunnynet 0.20.2, hashicorp/random 3.6.3 and hashicorp/null 3.2.4, each MPL-2.0, unmodified, and
  checked against its pinned sha256 (`runtime/pins.json`); `/opt/tofu/NOTICE` names their sources. A module that needs
  another provider or version fails at `tofu init`.

## The reconciler

For each stack funcd sends, the reconciler starts the run of the stack's generation, `<stack>-g<generation>-<uid>`, and
reads it back; the start is idempotent, so a call sent again starts nothing new. A stack is `Ready` once that run
succeeded, and shows `Queued`, `Planning`, `AwaitingApproval` (with the `funcdctl job approve` line), `Applying`,
`ApplyFailed`, `ApplyRejected` or `ApplyCancelled` meanwhile. A deleting stack with `deletion: delete` first waits for
the run of its generation to end, then starts `<stack>-d<generation>-<uid>`, which destroys it, and goes once that run
succeeded. A refused start shows `JobNotBound`, `JobNotFound`, `InvalidVariables`, `RunNameTaken`, `InputTooLarge` or
`RunStartFailed`.

## In an App

An App holds a stack as it holds the six objects above (funcd ADR-0350 for its Job): its template's `images` name the
module's bundle and the reconciler, since a template's Job and Function take only its own images, so `app/` holds the
Addon alone. funcd-functions' frp-bunny App is the example.

## Build and check

```sh
nix develop -c just test-infrastack
nix develop -c just opentofu-image <funcd-python>/shim
nix develop -c just opentofu-image-check
```

`opentofu-image-check` runs the handler with OpenTofu itself, in the image, against versitygw, the S3 engine of funcd's
gateway: a preview that adds three resources, its approved apply, a second plan that changes nothing, a refused plan
whose digest is not the approved one, a run past a planted lock, and a destroy, with the state's encryption off and on.
The release workflow publishes `ghcr.io/pyvvo/funcd-opentofu` for linux/amd64 and linux/arm64, with the shim of the
funcd-python release that `pins.json` names.

## Not yet

- A drift schedule: a stack plans only on a change or a deletion. A drift check's finding has nowhere to go: a run's
  result must match the module's output contract (funcd ADR-0347), a preview is no result (ADR-0348 Decision 2), and
  `ctx.job.get` reads a run's phase, reason and result only (ADR-0349 Decision 3). A contract that reserves a drift
  field, as F143's contract from the module could, would carry it.
- The module from the stack itself, its contract from its variables, and `funcdctl infra` (F143), with `funcdctl infra
  outputs` (F144); stacks in an App's rollout (F145).
- A waiting apply run of a deleted stack that never applied stays approvable: reject it with `funcdctl job reject`.
