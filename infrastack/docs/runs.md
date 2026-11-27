# A stack's runs

What a run of a stack's Job does on the add-on's OpenTofu runtime, from its input to its state.

## Input and output

As [ADR-0355 Decision 3](https://github.com/pyvvo/funcd/blob/main/docs/adr/0355-an-opentofu-module-is-pushed-with-its-providers-and-a-contract-from-its-variables.md)
has it, a run's input is `{"action": "apply" | "destroy" | "plan", "variables": {...}}`. Its output is
`{"outputs": {...}}` for an apply, `{}` for a destroy and `{"plan": {...}}` for a plan run. A module pushed by hand,
`funcdctl push <module> <ref> --entry main.tf` beside a `<module>.contract.yaml` written in that shape, runs as well,
with the image's providers.

The contract that `funcdctl infra push` makes from the module has the input `action`, one of `apply`, `destroy` and
`plan`, and `variables`, a closed record of the module's variables. The variables with no default are required, and
`variables` itself is required only when one is.

- A sensitive variable is left out, so no run's input holds it: its value comes from a bound Secret's
  `TF_VAR_<name>`, as in [`examples/token-stack.yaml`](../examples/token-stack.yaml).
- A module with no plain variable takes `variables: {"type": "null"}`, a side that takes nothing, as
  [ADR-0090](https://github.com/pyvvo/funcd/blob/main/docs/adr/0090-mandatory-single-io-schema.md) has a
  Function's, so a run's input sets none.
- The output holds `outputs`, the plain outputs, none required, and `plan`, a plan run's finding.
- A wrong variable fails the run's creation with 422, naming its path, and the stack shows `InvalidVariables`.

## The preview and the apply

- **The preview** copies the module into a work folder, without the variables and state files that the push
  refuses, adds a backend override, and runs `tofu init` against the state, then `tofu plan -out`. It keeps the saved
  plan beside the state, at `plans/<run>.tfplan`, and answers what the plan adds, changes, replaces and destroys, by
  address, with `planDigest`, the plan's sha256.
- **The apply**, once approved, reads that saved plan, refuses it when its sha256 is not the approved document's
  `planDigest`, and runs `tofu apply` on it. OpenTofu refuses a plan made before the state last changed. A Job with
  `approval.bypass` plans and applies in one call.
- **A plan run** (`action: plan`) previews as any run does but keeps no saved plan. The run plans its variables
  against the state again, applies nothing, writes no output and answers
  `{"plan": {"add", "change", "destroy", "replace", "resources"}}`: the counts and the first 100 addresses that it
  would change. A Job without `approval.bypass` asks a person to approve such a run too.

## The outputs

Once the apply is done, the runtime writes every output, plain or sensitive, into
[the Secret](https://github.com/pyvvo/funcd/blob/main/docs/adr/0356-a-stacks-outputs-reach-its-readers-through-the-secret-its-job-owns.md)
that `INFRASTACK_OUTPUTS_SECRET` names: one `ctx.secret.put` each, a string as it is and any other value as its
JSON. It answers only after the last write, with `{"outputs": {...}}`, the plain ones.

- A write that funcd refuses, such as for a Secret that the Job does not own, fails the run, naming the output and
  the Secret but no value. The change stays in the state, and the stack's next apply writes every output again.
- With no `INFRASTACK_OUTPUTS_SECRET`, the plain outputs are in the result alone, and each sensitive one is kept
  nowhere, which the run logs by its name.
- The Job declares each key that it writes, so the Secret holds each key, empty, before the first apply.
- An output's name is its key in the Secret: a letter or `_`, then letters, digits or `_`, starting with neither
  `FUNCD_`, which no reader loads, nor `INFRASTACK_`, the runtime's own settings. Another name fails a run's first
  call before it plans, naming the output: rename it in the module. A destroy skips the check.
- A destroy empties each output key that it read before the destroy, and keeps the key, so no reader waits for it
  again. It answers `{}`. An emptying that funcd refuses is logged by the output's name, and the run goes on.

## The environment

The Job's bound Secrets give OpenTofu its environment: each key in upper case, such as `BUNNYNET_API_KEY`, and each
`TF_VAR_<variable>`, as [`examples/token-stack.yaml`](../examples/token-stack.yaml) passes its sensitive variable
`token` as `TF_VAR_token`. There are two exceptions:

- A key that starts with `AWS_` never reaches OpenTofu, since the state's S3 key pair takes that name.
- Neither does a key named as one of the module's outputs, which the runtime reads with `tofu show -json -module`, so
  an output never sets a later run's variable or a provider's setting.

## The state

The state is the s3 backend at `<prefix>/terraform.tfstate` in the Bucket, through funcd's S3 gateway, with the Job's
[own key pair](https://github.com/pyvvo/funcd/blob/main/docs/adr/0351-a-jobs-worker-reaches-the-platform-as-the-job-its-own-principal.md)
in a credentials file of the run's own. The lock is OpenTofu's lock file. A Job with `concurrency` unset
[runs one run at a time](https://github.com/pyvvo/funcd/blob/main/docs/adr/0347-a-job-runs-its-handler-once-per-jobrun-in-a-worker-of-its-own.md),
so a lock found at a run's start was left by an interrupted run: the runtime removes it with `tofu force-unlock` and
goes on. A stack's Job keeps `concurrency` unset.

Encryption is off unless a bound Secret holds `INFRASTACK_STATE_PASSPHRASE`, at least 16 characters. OpenTofu then
encrypts the state and the saved plans with a key derived from it.

## The providers

A run downloads nothing. `tofu init` installs the providers from the module's `.funcd-providers/`, which the plugin
packs, each only when it matches a hash of the module's lock file, else from the image's plugin folder. The image
carries OpenTofu 1.13.9 and the providers bunnyway/bunnynet 0.20.2, hashicorp/random 3.6.3 and hashicorp/null 3.2.4,
each MPL-2.0, unmodified, and checked against its pinned sha256 ([`runtime/pins.json`](../runtime/pins.json)).
`/opt/tofu/NOTICE` names their sources. A module pushed by hand that needs another provider or version fails at
`tofu init`, and so does a package that matches no hash of the lock file, naming the provider.
