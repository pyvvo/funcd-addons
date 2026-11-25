# funcd-addons

Add-ons built outside [funcd](https://github.com/pyvvo/funcd), which an admin registers on a running funcd. Each one
ships runtimes: images that Functions and Jobs run on, or whose engine runs one per object of the kind the add-on adds,
which its users declare as they declare a Lake. funcd runs each object's engine, serves it at the edge, and copies its
data into the namespace's backups.

| Add-on | Kind | What it is |
|---|---|---|
| [rauthy](rauthy/) | `IdentityProvider`, and the settings kind `RauthyPasswordPolicy` | An OpenID Connect provider with funcd's look ([pyvvo/funcd-rauthy](https://github.com/pyvvo/funcd-rauthy)), with the funcdctl plugin `funcdctl-rauthy`, as an App template |
| [infrastack](infrastack/) | `InfraStack`, which runs no engine | OpenTofu stacks: a Function drives one Job per stack on the add-on's OpenTofu runtime, whose runs apply only the plan a person approved |
| [tunnel](tunnel/) | `Tunnel` | frpc as an engine: it dials frps outside funcd, such as on Bunny Magic Containers behind a pull zone, and hands each visitor's request to funcd's edge, so a namespace's host gets a public HTTPS name with no inbound port |

## Register one

An admin applies the add-on's `addon.yaml` from a release, which pins each runtime's image by digest, piped to
`funcdctl apply -f -`:

```sh
curl -fsSL https://github.com/pyvvo/funcd-addons/releases/download/v0.3.0/rauthy.yaml | funcdctl apply -f -
```

funcd accepts an image only from the registries its `addons.registries` key lists. Each add-on's README shows its
objects, the Secret they bind and its plugin.

## Layout

| Path | What |
|---|---|
| `<addon>/app/` | The add-on's App template: `addon.yaml`, the Addon (its runtimes: the one that runs its kind, with the engine, the schema of its objects' `spec.config` and its settings kinds), and the App that holds an object of its kind |
| `<addon>/settings/` | The code and the contract of its settings kinds' Function, which a release publishes as an image |
| `<addon>/reconciler/` | The code and the contract of an engineless kind's Function, which a release publishes as an image |
| `<addon>/runtime/` | An image the add-on ships as a runtime, which each release builds and pushes to GHCR |
| `<addon>/image/` | The image of the add-on's engine, which each release builds and pushes to GHCR |
| `<addon>/examples/` | Objects of its kind, and what uses them |
| `<addon>/plugin/funcdctl-<addon>/` | Its funcdctl plugin, a Go command that each release builds |
| `e2e/` | The end-to-end run: each add-on under `funcdctl dev` on Docker, at the funcd commit `e2e/funcd.ref` names |
| `e2e/lanes.yaml`, `e2e/lanes/` | The lanes: each add-on on a real funcd on containerd in a Lima VM, with their Venom suites and fixtures |
| `go.mod` | This repository is a Go module, which funcd pins by tag |

## Releases

release-please keeps a release PR open on `main`. Merging it tags `v<version>`, and the release workflow runs the
end-to-end check on that tag, then attaches each `app/addon.yaml` (as `<addon>.yaml`) and each plugin for Linux and macOS
to the release, and pushes each add-on's images to GHCR for linux/amd64 and linux/arm64, and its kinds' Functions, tagged
`<version>`. An Addon pins a Function's digest before its release: `just kind-images` writes it.

## Develop

```sh
nix develop -c just ci             # check the manifests, build and test the plugins
nix develop -c just e2e            # run each add-on under funcdctl dev; needs Docker
nix develop -c just lane <name>    # run one lane of e2e/lanes.yaml in a Lima VM; needs colima
nix develop -c just lanes          # run every lane but the paid live one, in one shared VM
```

Design decisions live in funcd's ADRs: ADR-0332 to ADR-0336 decide what an add-on is.

## Lanes

The lanes moved here from funcd's `scripts/lanes.yaml` on 2026-11-24, so that the add-ons are tested in their own
repository. Each one runs add-ons on a real funcd on containerd in a Lima VM, with funcd's lane harness, and tests the
add-ons' files at the commit under test; the example Apps come from pyvvo/funcd-functions at funcd's go.mod pin.
`e2e/lanes.yaml` declares them in the keys of funcd's lane registry.

`just lane <name>` keeps a funcd checkout at `~/.cache/funcd-addons/funcd`, at the commit `e2e/funcd.ref` names, and
runs funcd's `just lima-example <name>` there through funcd's dev shell, with `FUNCD_LANES` naming `e2e/lanes.yaml`
and `FUNCD_LANES_ROOT` this checkout. funcd runs one lane VM per host, so a lane waits while another one runs.

| Lane | What it runs |
|---|---|
| `addon-rauthy` | rauthy registered by an admin, its IdentityProvider served at the edge with its private path refused, its password policy kind and its App template |
| `addon-rauthy-signin` | The edge signs a user in through rauthy: the client funcd registers, the principal headers and a rotation |
| `addon-rauthy-restore` | rauthy backed up from its own snapshots and restored in place, and a restore cut by funcd's kill |
| `addon-infrastack` | InfraStack modules pushed with the plugin; stacks previewed, approved, applied, read through their outputs and destroyed |
| `addon-frp-bunny` | funcd-functions' frp-bunny App with the fake module: its stack, its Tunnel to a local frps, and the edge |
| `addon-frp-bunny-live` | The same App on Bunny, which spends: its suite deploys only with `VENOM_VAR_bunnyname`, `VENOM_VAR_bunnyregistry` and `VENOM_VAR_bunnykeyfile` set |
