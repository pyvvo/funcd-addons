# Develop the add-ons

The repository's layout, and the lanes that run the add-ons on a real funcd.

## Layout

| Path | What |
|---|---|
| `<addon>/app/` | The add-on's App template: `addon.yaml`, the Addon, and the App that holds an object of its kind. The Addon holds its runtimes, among them the one that runs its kind, with the engine, the schema of its objects' `spec.config` and its settings kinds |
| `<addon>/settings/` | The code and the contract of its settings kinds' Function, which a release publishes as an image |
| `<addon>/reconciler/` | The code and the contract of an engineless kind's Function, which a release publishes as an image |
| `<addon>/runtime/` | An image that the add-on ships as a runtime, which each release builds and pushes to GHCR |
| `<addon>/image/` | The image of the add-on's engine, which each release builds and pushes to GHCR |
| `<addon>/examples/` | Objects of its kind, and what uses them |
| `<addon>/plugin/funcdctl-<addon>/` | Its funcdctl plugin, a Go command that each release builds |
| `<addon>/docs/plugin.md` | Its plugin's commands, with the reference that `just plugin-docs` writes from them |
| `internal/pluginkit/` | The kit that every plugin is built on, with the fakes and the reference check of its tests |
| `e2e/` | The end-to-end run: each add-on under `funcdctl dev` on Docker, at the funcd commit that `e2e/funcd.ref` names |
| `e2e/lanes.yaml`, `e2e/lanes/` | The lanes: each add-on on a real funcd on containerd in a Lima VM, with their Venom suites and fixtures |
| `go.mod` | This repository is a Go module, which funcd pins by tag |

## Lanes

A lane runs add-ons on a real funcd on containerd in a Lima VM, with funcd's lane harness, and tests the add-ons' files
at the commit under test. The example Apps come from pyvvo/funcd-functions at funcd's `go.mod` pin.
[`e2e/lanes.yaml`](../e2e/lanes.yaml) declares the lanes in the keys of funcd's lane registry. The lanes moved here from
funcd's `scripts/lanes.yaml` on 2026-11-24, so that the add-ons are tested in their own repository.

`just lane <name>` keeps a funcd checkout at `~/.cache/funcd-addons/funcd`, at the commit that `e2e/funcd.ref` names.
It runs funcd's `just lima-example <name>` there through funcd's dev shell, with `FUNCD_LANES` naming `e2e/lanes.yaml`
and `FUNCD_LANES_ROOT` naming this checkout. funcd runs one lane VM per host, so a lane waits while another one runs.

| Lane | What it runs |
|---|---|
| `addon-rauthy` | rauthy registered by an admin, its IdentityProvider served at the edge with its private path refused, its password policy kind and its App template |
| `addon-rauthy-signin` | The edge signs a user in through rauthy: the client funcd registers, the principal headers and a rotation |
| `addon-rauthy-restore` | rauthy backed up from its own snapshots and restored in place, and a restore cut by funcd's kill |
| `addon-infrastack` | InfraStack modules pushed with the plugin; stacks previewed, approved, applied, read through their outputs and destroyed |
| `addon-frp-bunny` | funcd-functions' frp-bunny App with the fake module: its stack, its Tunnel to a local frps, and the edge |
| `addon-frp-bunny-live` | The same App on Bunny, which spends: its suite deploys only with `VENOM_VAR_bunnyname`, `VENOM_VAR_bunnyregistry` and `VENOM_VAR_bunnykeyfile` set |
