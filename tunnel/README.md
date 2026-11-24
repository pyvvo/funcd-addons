# tunnel

An add-on whose kind `Tunnel` runs frpc as an engine. The engine dials frps outside funcd and hands each visitor's
request to funcd's edge, so a namespace's host gets a public HTTPS name with no inbound port. The tested path is
frpc over wss through a Bunny pull zone, with frp's inner TLS off (funcd's experiment 0027, path C).

## What funcd must provide

- funcd ADR-0352: an EgressPolicy whose `appliesTo` names the Tunnel object lets its engine dial the pull zone.
- funcd ADR-0353: the edge accepts the engine on the bridge address and serves it only its namespace's hosts.
- funcd ADR-0354: `server.trustedProxies` with `addonKinds: [Tunnel]` and `header: X-Real-IP` gives the edge each
  visitor's address.

## Security

frps must accept connections only from Bunny's pull zone. A visitor that reaches frps directly could set
`X-Real-IP` itself, and the edge, which trusts a Tunnel engine's header, would take it as the client address.

## Status

Draft, 2026-11-24. Left before a release: pin `fatedier/frpc`'s digest in `image/Dockerfile`, build and publish the
image, pin its digest in `app/addon.yaml`, and add an e2e case.
