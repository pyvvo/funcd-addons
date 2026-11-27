# funcd-addons

Add-ons built outside [funcd](https://github.com/pyvvo/funcd), which an admin registers on a running funcd. An add-on
ships runtimes: images that Functions and Jobs run on, or whose engine runs one per object of the add-on's kind. Users
declare such an object as they declare a Lake, and funcd runs its engine, serves it at the edge and copies its data
into the namespace's backups.

| Add-on | Kind | What it is |
|---|---|---|
| [rauthy](rauthy/) | `IdentityProvider`, and the settings kind `RauthyPasswordPolicy` | An OpenID Connect provider with funcd's look ([pyvvo/funcd-rauthy](https://github.com/pyvvo/funcd-rauthy)), with the funcdctl plugin `funcdctl-rauthy`, as an App template |
| [infrastack](infrastack/) | `InfraStack`, which runs no engine | OpenTofu stacks: a Function drives one Job per stack on the add-on's OpenTofu runtime, and a run applies only the plan that a person approved |
| [tunnel](tunnel/) | `Tunnel` | frpc as an engine that dials frps outside funcd, such as behind a Bunny pull zone, so that a namespace's host gets a public HTTPS name with no inbound port |

## Register an add-on

An admin pipes the add-on's `addon.yaml`, which pins each runtime's image by digest, from a release to
`funcdctl apply -f -`:

```sh
curl -fsSL https://github.com/pyvvo/funcd-addons/releases/download/v0.3.0/rauthy.yaml | funcdctl apply -f -
```

funcd accepts an image only from the registries in its `addons.registries` key. Each add-on's README shows its
objects, the Secret they bind and its plugin.

## Develop

```sh
nix develop -c just ci             # check the manifests, build and test the plugins
nix develop -c just e2e            # run each add-on under funcdctl dev; needs Docker
nix develop -c just lane <name>    # run one lane of e2e/lanes.yaml in a Lima VM; needs colima
nix develop -c just lanes          # run every lane but the paid live one, in one shared VM
```

See the [layout](docs/develop.md#layout) and [lanes](docs/develop.md#lanes). `just ci` and the dev shell's
pre-commit hook check each README against its [template](scripts/readme-template.yaml). In funcd,
[ADR-0332](https://github.com/pyvvo/funcd/blob/main/docs/adr/0332-an-admin-registers-add-ons-built-outside-funcd.md) to
[ADR-0336](https://github.com/pyvvo/funcd/blob/main/docs/adr/0336-funcd-registers-the-edges-sign-in-client.md) decide
what an add-on is.

## Releases

release-please keeps a release PR open on `main`. Merging it tags `v<version>`, and the release workflow checks that
tag end to end. It then attaches each `app/addon.yaml`, as `<addon>.yaml`, and each plugin for Linux and macOS to the
release. It pushes each add-on's images and its kinds' Functions to GHCR for linux/amd64 and linux/arm64, tagged
`<version>`. `just kind-images` pins a Function's digest in its Addon before the release.
