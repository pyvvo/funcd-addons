# rauthy

[rauthy](https://github.com/sebadob/rauthy) as a funcd add-on: an OpenID Connect provider, run from the image that
[pyvvo/funcd-rauthy](https://github.com/pyvvo/funcd-rauthy) builds with funcd's look. The add-on adds the kind
`IdentityProvider`: funcd runs one rauthy per object, serves it at the object's host under `/auth/v1` and copies its
data into the namespace's backups. funcd signs an App's users in through it.

With the add-on [registered](#register-the-add-on), these commands declare an identity provider and print its URLs:

```sh
funcdctl rauthy keys --admin-email admin@example.com -n default
funcdctl apply -f examples/identityprovider.yaml
funcdctl rauthy urls login -n default
```

## Register the add-on

The server's `addons.registries` must list `ghcr.io`. An admin applies [`addon.yaml`](app/addon.yaml) from a release:

```sh
curl -fsSL https://github.com/pyvvo/funcd-addons/releases/download/v0.9.0/rauthy.yaml | funcdctl apply -f -
```

The Addon ships one runtime, `rauthy`, whose workload is `Engine`, and sets these rauthy settings:

| Setting | Why |
|---|---|
| `ENABLE_DYN_CLIENT_REG=true`, `DYN_CLIENT_SECRET_AUTO_ROTATE=false` | funcd registers clients (RFC 7591) and keeps their registration token valid across updates |
| `DYN_CLIENT_RATE_LIMIT_REG=1s` | funcd registers every client from one address; only funcd reaches the registration path |
| `DYN_CLIENT_CLEANUP=90d`, `DYN_CLIENT_CLEANUP_INACTIVE_THRESHOLD=90d` | rauthy deletes a client unused for 90 days; funcd registers it again when it is gone |
| `PROXY_MODE`, `TRUSTED_PROXIES` | With `https`, rauthy sits behind funcd's edge, the only caller that reaches its port |
| `HQL_BACKUP_CRON` | A copy every 15 minutes for the backups |

## Declare an identity provider

`funcdctl rauthy keys` applies the Secret that an IdentityProvider binds: rauthy's cluster and encryption keys, and a
first admin. It prints the admin's password once and stores only its argon2id hash.

| `spec.config` | What |
|---|---|
| `publicHost` | The host callers reach rauthy at, with its port when it is not the scheme's own |
| `rpId` | The passkey relying party: `publicHost` without its port |
| `https` | `true` when callers reach the edge over https: rauthy then issues https URLs, sets secure cookies, and reads the caller's address from the edge's `X-Forwarded-For` |

The edge serves the object at its `ingress.host`. `deletion: delete` removes its data with it, and the default,
`retain`, keeps the data for a new object of the same name.

## Sign users in

An [`OIDCProvider`](examples/oidcprovider.yaml) with `client.registration` has funcd register the edge's client at
rauthy. Its `addon` names the IdentityProvider, so funcd reaches rauthy at rauthy's own address: the edge never serves
the registration path, `/auth/v1/clients_dyn`. Its `issuer` is rauthy's, with the trailing slash that
`funcdctl rauthy urls` prints, such as `https://login.example.com/auth/v1/`.

A Route, a workload's address or a namespace with `auth.mode: oidc` and `auth.provider: <name>` then signs its callers
in. The code behind it reads who they are from the `X-Funcd-Principal` headers.

## Set the password policy

The settings kind `RauthyPasswordPolicy` holds the password policy of one IdentityProvider, which its `provider`
names. It runs no engine. funcd sends each IdentityProvider's policy to the settings Function, which puts it into
rauthy through its binding, and sends it again at each resync, so a policy changed at rauthy's own API is put back.
Deleting the object puts rauthy's default back. A provider holds one policy, and `lengthMin` may not exceed
`lengthMax`.

```sh
funcdctl apply -f examples/passwordpolicy.yaml
funcdctl get rauthypasswordpolicys -n default
```

The settings Function, [`settings/rauthy-settings.mjs`](settings/rauthy-settings.mjs), has the kind's schema as its
contract. A release publishes it as `ghcr.io/pyvvo/funcd-rauthy-settings`, which the Addon pins by digest.
funcd [creates the Function](https://github.com/pyvvo/funcd/blob/main/docs/adr/0358-an-add-on-kinds-function-comes-from-an-image-and-its-contract-is-the-kinds-schema.md)
`<identityprovider>-rauthypasswordpolicy` once a policy names the IdentityProvider, and an IdentityProvider may name
its own Function in `spec.functions`. The Function signs in to rauthy with the API key `settings`, which
`funcdctl rauthy keys` has rauthy create at its first start, so the IdentityProvider binds that Secret.

## Back up and restore

rauthy writes a copy of its data every 15 minutes, and a BackupSchedule with `scope.addons` copies
the newest one. A Restore of an IdentityProvider is in place only: funcd restarts rauthy with the copy, then again
without it, and holds the namespace meanwhile.

## Run it under funcdctl dev

`funcdctl dev` runs rauthy on the local Docker or containerd. Set `https: false`, and sign users in only on `localhost`
or `127.0.0.1` hosts: rauthy accepts an http callback on a loopback host only.

## The plugin

`funcdctl-rauthy` is a [funcdctl plugin](https://github.com/pyvvo/funcd/tree/main/docs/guide/develop/tools/funcdctl/plugins.md).
Each release attaches it for Linux and macOS. Put it on PATH, or build it:

```sh
go install github.com/pyvvo/funcd-addons/rauthy/plugin/funcdctl-rauthy@latest
```

## In an App

[`app/`](app/) is an App template that carries the Addon. An admin's `funcdctl app deploy` registers or updates the
Addon, then deploys an App that holds an IdentityProvider `<app>-login`. A developer's deploy needs the kind
registered already. The values are `host`, `https`, `keys`, the Secret that `funcdctl rauthy keys` writes, and
`publicHost`, the host that callers reach rauthy at, which defaults to empty, meaning `host`:

```sh
funcdctl push --template app <registry>/rauthy:0.2.0
funcdctl app deploy <registry>/rauthy:0.2.0 -f values.yaml --name web-auth
```
