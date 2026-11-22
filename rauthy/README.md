# rauthy

[rauthy](https://github.com/sebadob/rauthy) as a funcd add-on: an OpenID Connect provider, from the image
[pyvvo/funcd-rauthy](https://github.com/pyvvo/funcd-rauthy) builds with funcd's look. It adds the kind
`IdentityProvider`. funcd runs one rauthy per object, serves it at the object's host under `/auth/v1`, copies its
data into the namespace's backups, and signs an App's users in through it with a client it registers itself.

## Register it

The server's `addons.registries` lists `ghcr.io`, and an admin applies [`addon.yaml`](app/addon.yaml) from a release:

```sh
curl -fsSL https://github.com/pyvvo/funcd-addons/releases/download/v0.1.0/rauthy.yaml | funcdctl apply -f -
```

## Declare an identity provider

`funcdctl rauthy keys` applies the Secret an IdentityProvider binds: rauthy's cluster and encryption keys, and a first
admin, whose password it prints once and stores only as an argon2id hash.

```sh
funcdctl rauthy keys --admin-email admin@example.com -n default
funcdctl apply -f examples/identityprovider.yaml
funcdctl rauthy urls login -n default
```

| `spec.config` | What |
|---|---|
| `publicHost` | The host callers reach rauthy at, with its port when it is not the scheme's own |
| `rpId` | The passkey relying party: `publicHost` without its port |
| `https` | `true` when callers reach the edge over https: rauthy then issues https URLs, sets secure cookies, and reads the caller's address from the edge's `X-Forwarded-For` |

The object's `ingress.host` is the host the edge serves it at, and `deletion: delete` removes its data with it (the
default, `retain`, keeps it for a new object of the same name).

## Sign users in with it

An [`OIDCProvider`](examples/oidcprovider.yaml) with `client.registration` has funcd register the edge's client at
rauthy, and its `addon`, naming the IdentityProvider, has funcd reach rauthy at its own address: the registration path,
`/auth/v1/clients_dyn`, is never served at the edge. Its `issuer` is rauthy's own, with the trailing slash that
`funcdctl rauthy urls` prints, such as `https://login.example.com/auth/v1/`. A
Route, a workload's address or a namespace with `auth.mode: oidc` and `auth.provider: <name>` then signs its callers
in, and the code behind it reads who they are from the `X-Funcd-Principal` headers.

## Set its password policy

The Addon adds the settings kind `RauthyPasswordPolicy`: an object of it holds the password policy of one
IdentityProvider, which its `provider` names. It runs no engine: funcd sends each IdentityProvider's policy to the
settings Function, which puts it into rauthy through its binding of the IdentityProvider, and again at each resync, so a
policy changed at rauthy's own API is put back. Deleting the object puts rauthy's default back. A provider holds one
policy, and `lengthMin` may not exceed `lengthMax`.

The settings Function is [`settings/rauthy-settings.mjs`](settings/rauthy-settings.mjs), which signs in to rauthy with
the API key `settings` that `funcdctl rauthy keys` has rauthy create at its first start. Push it as any Function, then
apply a policy such as [`examples/passwordpolicy.yaml`](examples/passwordpolicy.yaml):

```sh
funcdctl push settings/rauthy-settings.mjs <registry>/rauthy-settings:0.2.0
funcdctl apply -f examples/passwordpolicy.yaml
funcdctl get rauthypasswordpolicys -n default
```

The kind's Function is `rauthy-settings` of the IdentityProvider's namespace, which binds the IdentityProvider as an
add-on object; an IdentityProvider may name its own in `spec.functions`.

## Deploy it as an App

[`app/`](app/) is an App template that carries the Addon: `funcdctl app deploy` by an admin registers or updates the
Addon, then deploys an App that holds an IdentityProvider `<app>-login` and its settings Function `<app>-settings`, which
the IdentityProvider names for its policies. A developer's deploy needs the kind registered already. The values are
the registry the settings Function was pushed to, `host`, and `https`:

```sh
funcdctl app lock app -f values.yaml
funcdctl push --template app <registry>/rauthy:0.2.0
funcdctl app deploy <registry>/rauthy:0.2.0 -f values.yaml --name web-auth
```

## Backups

rauthy writes a copy of its data every 15 minutes (`HQL_BACKUP_CRON`), and a BackupSchedule with `scope.addons` copies
the newest one. A Restore of an IdentityProvider is in place only: funcd restarts rauthy with the copy, then again
without it, and holds the namespace meanwhile.

## Under funcdctl dev

dev runs rauthy on the local Docker or containerd. Use `https: false`, and sign users in only on `localhost` or
`127.0.0.1` hosts: rauthy accepts an http callback on a loopback host only.

## What the Addon sets

| Setting | Why |
|---|---|
| `ENABLE_DYN_CLIENT_REG=true`, `DYN_CLIENT_SECRET_AUTO_ROTATE=false` | funcd registers clients (RFC 7591) and keeps their registration token valid across updates |
| `DYN_CLIENT_RATE_LIMIT_REG=1s` | funcd registers every client from one address; only funcd reaches the registration path |
| `DYN_CLIENT_CLEANUP=90d`, `DYN_CLIENT_CLEANUP_INACTIVE_THRESHOLD=90d` | rauthy deletes a client unused for 90 days; funcd registers it again when it is gone |
| `PROXY_MODE`, `TRUSTED_PROXIES` | With `https`, rauthy sits behind funcd's edge, the only caller that reaches its port |
| `HQL_BACKUP_CRON` | A copy every 15 minutes for the backups |

## The plugin

`funcdctl-rauthy` is a [funcdctl plugin](https://github.com/pyvvo/funcd/tree/main/docs/guide/develop/tools/funcdctl/plugins.md).
Each release attaches it for Linux and macOS; put it on PATH, or build it:

```sh
go install github.com/pyvvo/funcd-addons/rauthy/plugin/funcdctl-rauthy@latest
```
