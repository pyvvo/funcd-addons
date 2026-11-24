# funcd-addons

Add-ons built outside [funcd](https://github.com/pyvvo/funcd), which an admin registers on a running funcd. Each one
ships runtimes: images that Functions and Jobs run on, or whose engine runs one per object of the kind the add-on adds,
which its users declare as they declare a Lake. funcd runs each object's engine, serves it at the edge, and copies its
data into the namespace's backups.

| Add-on | Kind | What it is |
|---|---|---|
| [rauthy](rauthy/) | `IdentityProvider`, and the settings kind `RauthyPasswordPolicy` | An OpenID Connect provider with funcd's look ([pyvvo/funcd-rauthy](https://github.com/pyvvo/funcd-rauthy)), with the funcdctl plugin `funcdctl-rauthy`, as an App template |

## Register one

An admin applies the add-on's `addon.yaml` from a release, which pins each runtime's image by digest, piped to
`funcdctl apply -f -`:

```sh
curl -fsSL https://github.com/pyvvo/funcd-addons/releases/download/v0.1.0/rauthy.yaml | funcdctl apply -f -
```

funcd accepts an image only from the registries its `addons.registries` key lists. Each add-on's README shows its
objects, the Secret they bind and its plugin.

## Layout

| Path | What |
|---|---|
| `<addon>/app/` | The add-on's App template: `addon.yaml`, the Addon (its runtimes: the one that runs its kind, with the engine, the schema of its objects' `spec.config` and its settings kinds), and the App that holds an object of its kind and the Functions beside it |
| `<addon>/settings/` | The code of its settings kinds' Function, pushed as any Function |
| `<addon>/examples/` | Objects of its kind, and what uses them |
| `<addon>/plugin/funcdctl-<addon>/` | Its funcdctl plugin, a Go command that each release builds |
| `e2e/` | The end-to-end run: each add-on under `funcdctl dev` on Docker |
| `go.mod` | This repository is a Go module, which funcd pins by tag |

## Releases

release-please keeps a release PR open on `main`. Merging it tags `v<version>`, and the release workflow runs the
end-to-end check on that tag, then attaches each `app/addon.yaml` (as `<addon>.yaml`) and each plugin for Linux and macOS
to the release.

## Develop

```sh
nix develop -c just ci     # check the manifests, build and test the plugins
nix develop -c just e2e    # run each add-on under funcdctl dev; needs Docker
```

Design decisions live in funcd's ADRs: ADR-0332 to ADR-0336 decide what an add-on is.
