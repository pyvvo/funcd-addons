# funcdctl infra

The commands of `funcdctl-infra`, the add-on's
[funcdctl plugin](https://github.com/pyvvo/funcd/blob/main/docs/adr/0319-funcdctl-runs-kubectl-style-plugins.md).
It needs `docker` or `nerdctl`, the first on PATH. It runs OpenTofu in the image of the Addon's `opentofu1` runtime,
which each release stamps into it, or in the image that `INFRASTACK_OPENTOFU_IMAGE` names. It copies files in and out
with `cp`, with no bind mount, and reaches funcd through `$FUNCDCTL`.

## Install

Each release attaches the plugin as one archive per platform, `funcdctl-infra-<os>-<arch>.tar.gz`, for `linux` and
`darwin` on `amd64` and `arm64`. Unpack it into a folder on PATH:

```sh
curl -fsSL https://github.com/pyvvo/funcd-addons/releases/download/v<version>/funcdctl-infra-linux-amd64.tar.gz | tar -xz -C <folder>
```

Or build it, with the image of the Addon at the same tag:

```sh
go install github.com/pyvvo/funcd-addons/infrastack/plugin/funcdctl-infra@v<version>
```

## funcdctl infra push

`funcdctl infra push <module> <ref>`
[pushes a module folder](https://github.com/pyvvo/funcd/blob/main/docs/adr/0355-an-opentofu-module-is-pushed-with-its-providers-and-a-contract-from-its-variables.md).
`<ref>` is `<registry>/<repo>:<tag>` or `oci-layout://<dir>:<tag>`.

- It refuses a folder that holds `terraform.tfstate`, `terraform.tfstate.backup`, `terraform.tfvars`,
  `terraform.tfvars.json`, `*.auto.tfvars` or `*.auto.tfvars.json` outside `.terraform/`. It also refuses a module
  call whose source is not a relative path inside the folder, such as a registry module or `../shared`, naming it.
- It locks the providers for `linux_amd64` and `linux_arm64` into the module's `.terraform.lock.hcl`, to commit.
- It then pushes one bundle per platform, `<ref>-linux-amd64` and `<ref>-linux-arm64`, each with that platform's
  provider packages in `.funcd-providers/` and a [contract](runs.md#input-and-output) made from the module, and joins
  them with `funcdctl index`. It prints `<ref>@<digest>`.

## funcdctl infra outputs

`funcdctl infra outputs <stack> [-n <namespace>] [-o json]` prints each key of the stack's outputs Secret, with its
value when the Job's contract names it among the plain outputs, else `(sensitive)`, as
[ADR-0356 Decision 8](https://github.com/pyvvo/funcd/blob/main/docs/adr/0356-a-stacks-outputs-reach-its-readers-through-the-secret-its-job-owns.md)
has it:

```text
NAME           VALUE
pullzone_host  frp-lane.b-cdn.net
FRP_TOKEN      (sensitive)
```

With `-o json` it prints `{"outputs": {"pullzone_host": "frp-lane.b-cdn.net"}, "sensitive": ["FRP_TOKEN"]}`.

## funcdctl infra approve

`funcdctl infra approve <stack> [-n <namespace>]` approves the run that the stack waits on: the apply run of its
generation or, while the stack is being deleted, its destroy run. It finds the run that the stack's Ready condition
names, then runs `funcdctl job approve`, which shows the run's plan and asks to confirm. It refuses a stack that
awaits no approval, with the stack's status message.

## Reference

The usage and the flags of each command, as `funcdctl infra <command> --help` prints them:

<!-- funcdctl infra reference: just plugin-docs -->

### funcdctl infra push

Push an OpenTofu module with its providers and its contract; print &lt;ref&gt;@&lt;digest&gt;

```
funcdctl infra push <module> <ref>
```

### funcdctl infra outputs

Print an InfraStack's outputs, a sensitive one as (sensitive)

```
funcdctl infra outputs <stack> [flags]
```

#### Options

```
  -n, --namespace string   the namespace (default "default")
  -o, --output json        print JSON instead of a table
```

### funcdctl infra approve

Show the plan of the run that an InfraStack waits on, and approve it once confirmed

```
funcdctl infra approve <stack> [flags]
```

#### Options

```
  -n, --namespace string   the namespace (default "default")
```

<!-- end funcdctl infra reference -->
