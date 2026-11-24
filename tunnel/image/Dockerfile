# The tunnel add-on's engine: frpc, whose config reads the Addon's env through frp's {{ .Envs.X }} templates, since an
# Addon passes its engine no arguments or files (funcd ADR-0332, ADR-0344).
FROM fatedier/frpc:v0.72.0@sha256:TODO-pin-the-linux-multiarch-index-digest
COPY frpc.toml /etc/frp/frpc.toml
ENTRYPOINT ["/usr/bin/frpc"]
CMD ["-c", "/etc/frp/frpc.toml"]
