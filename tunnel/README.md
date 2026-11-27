# tunnel

The add-on's kind, `Tunnel`, runs frpc as an engine. The engine dials frps outside funcd and hands each visitor's
request to funcd's edge, so a namespace's host gets a public HTTPS name with no inbound port. The tested path is frpc
over wss through a Bunny pull zone, with frp's inner TLS off: path C of funcd's experiment 0027.

The smallest Tunnel is in [`examples/tunnel.yaml`](examples/tunnel.yaml). pyvvo/funcd-functions'
[`apps/frp-bunny`](https://github.com/pyvvo/funcd-functions/tree/main/apps/frp-bunny) deploys frps and its pull zone
with the InfraStack add-on, and connects a Tunnel to it.

## Register the add-on

An admin registers the add-on from a release's `tunnel.yaml`, as the [root README](../README.md#register-an-add-on)
shows. [`app/addon.yaml`](app/addon.yaml) ships one runtime, `tunnel`, which runs the kind `Tunnel`. Its image is
`fatedier/frpc` v0.72.0 with [`image/frpc.toml`](image/frpc.toml), which reads the engine's environment through frp's
`{{ .Envs.X }}` templates, since an Addon
[passes its engine no arguments or files](https://github.com/pyvvo/funcd/blob/main/docs/adr/0344-an-add-on-ships-runtimes-and-its-kind-runs-on-one-of-them.md).

A Tunnel needs these from funcd:

- containerd with `server.network.egress` on, and a fixed `server.dataPlaneAddr` port, since `edgePort` names it.
- An [EgressPolicy](https://github.com/pyvvo/funcd/blob/main/docs/adr/0352-an-egresspolicy-names-an-add-on-object-whose-engine-it-lets-out.md)
  whose `appliesTo` names the Tunnel, which lets its engine dial frps (`examples/tunnel.yaml`).
- The [edge serves the engine](https://github.com/pyvvo/funcd/blob/main/docs/adr/0353-the-edge-serves-a-workload-only-its-own-namespaces-hosts.md)
  on the bridge address, and only its namespace's hosts.
- An entry of `server.trustedProxies` whose `addonKinds` lists `Tunnel` and whose `header` is `X-Real-IP`, which
  gives the edge [each visitor's address](https://github.com/pyvvo/funcd/blob/main/docs/adr/0354-the-edge-takes-the-client-address-from-a-proxy-it-trusts.md):

  ```yaml
  server:
    trustedProxies:
      - addonKinds:
          - Tunnel
        header: X-Real-IP
  ```

## Declare a tunnel

A Tunnel's `spec.config` holds:

| Key | Holds |
|---|---|
| `server` | frps's address, such as the pull zone's host `<name>.b-cdn.net` |
| `serverPort` | frps's port, 443 by default |
| `protocol` | `wss`, the default, or `websocket` for an frps with no TLS hop in front, such as a local test |
| `host` | the public name that frps routes to this tunnel, `server` by default: a pull zone forwards its own host |
| `hostRewrite` | the host of the namespace that the edge serves for it, which replaces the visitor's Host header |
| `edgeAddress`, `edgePort` | funcd's edge on the workers' bridge: the bridge address, such as `10.63.0.1`, and the data plane's port |

A bound Secret holds `FRP_TOKEN`, frps's token, and the Secret's other keys do not reach the engine. frpc keeps
retrying while frps is not up or the token is wrong, and a changed token replaces the engine. The engine is Ready once
frpc's admin server answers `/healthz` on port 7400.

## Keep frps behind the pull zone

frps must accept connections only from Bunny's pull zone. A visitor that reaches frps directly could set `X-Real-IP`
itself, and the edge trusts a Tunnel engine's header, so it would take that value as the client address. A Magic
Containers endpoint of type CDN is reached only through Bunny.

## Build and check

The add-on's files:

```text
tunnel/
├── README.md
├── app/
│   └── addon.yaml
├── examples/
│   └── tunnel.yaml
└── image/
    ├── Dockerfile
    └── frpc.toml
```

v0.4.0's release published `ghcr.io/pyvvo/funcd-tunnel` for linux/amd64 and linux/arm64, and `app/addon.yaml` pins
its index digest. The tunnel was checked on 2026-11-24:

- On Docker, a call to a local frps with the tunnel's `host` reached a stand-in edge with the Host header rewritten to
  `hostRewrite`. `/healthz` answered 200, and frpc, started before frps, connected 2 s after frps came up.
- On containerd with egress on, in funcd's `addon-frp-bunny` lane, a Tunnel whose token came from an InfraStack's
  outputs Secret dialed an frps in the VM. A Function of the Tunnel's namespace answered a call to frps with the
  tunnel's host, through funcd's edge on the bridge address.
