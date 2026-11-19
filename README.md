# funcd-addons

Add-ons built outside [funcd](https://github.com/pyvvo/funcd): engines that an admin registers on a running funcd, so
that its users declare objects of the kind each one adds, as they declare a Lake. funcd runs each object's engine,
serves it at the edge, and copies its data into the namespace's backups.

| Add-on | Kind | What it is |
|---|---|---|
| [rauthy](rauthy/) | `IdentityProvider` | An OpenID Connect provider with funcd's look ([pyvvo/funcd-rauthy](https://github.com/pyvvo/funcd-rauthy)), with the funcdctl plugin `funcdctl-rauthy` |

## Register one

An admin applies the add-on's `addon.yaml` from a release, which pins its engine image by digest, piped to
`funcdctl apply -f -`:

```sh
curl -fsSL https://github.com/pyvvo/funcd-addons/releases/download/v0.1.0/rauthy.yaml | funcdctl apply -f -
```

funcd accepts an image only from the registries its `addons.registries` key lists. Each add-on's README shows its
objects, the Secret they bind and its plugin.

## Layout

| Path | What |
|---|---|
| `<addon>/addon.yaml` | The Addon: its kind, its engine and the schema of its objects' `spec.config` |
| `<addon>/examples/` | Objects of its kind, and what uses them |
| `<addon>/plugin/funcdctl-<addon>/` | Its funcdctl plugin, a Go command that each release builds |
| `e2e/` | The end-to-end run: each add-on under `funcdctl dev` on Docker |
| `go.mod` | This repository is a Go module, which funcd pins by tag |

## Releases

release-please keeps a release PR open on `main`. Merging it tags `v<version>`, and the release workflow runs the
end-to-end check on that tag, then attaches each `addon.yaml` (as `<addon>.yaml`) and each plugin for Linux and macOS
to the release.

## Develop

```sh
nix develop -c just ci     # check the manifests, build and test the plugins
nix develop -c just e2e    # run each add-on under funcdctl dev; needs Docker
```

Design decisions live in funcd's ADRs: ADR-0332 to ADR-0336 decide what an add-on is.
