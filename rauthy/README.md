# rauthy

[rauthy](https://github.com/sebadob/rauthy) as a funcd add-on: an OpenID Connect provider, from the image
[pyvvo/funcd-rauthy](https://github.com/pyvvo/funcd-rauthy) builds with funcd's look. It adds the kind
`IdentityProvider`. funcd runs one rauthy per object, serves it at the object's host under `/auth/v1`, copies its
data into the namespace's backups, and signs an App's users in through it with a client it registers itself.

## Register it

An admin applies [`addon.yaml`](addon.yaml), from a release:

```sh
funcdctl apply -f https://github.com/pyvvo/funcd-addons/releases/download/v0.1.0/rauthy.yaml
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

An [`OIDCProvider`](examples/oidcprovider.yaml) whose `addon` names the IdentityProvider has funcd register its client
there, through rauthy's own address: the registration path, `/auth/v1/clients_dyn`, is never served at the edge. A
Route, a workload's address or a namespace with `auth.mode: oidc` and `auth.provider: <name>` then signs its callers
in, and the code behind it reads who they are from the `X-Funcd-Principal` headers.

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
