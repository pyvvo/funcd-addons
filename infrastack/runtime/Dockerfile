# The InfraStack add-on's OpenTofu runtime (funcd ADR-0344): an Addon runtime for Jobs, funcd's Python shim with a
# handler of its own, which plans a Job's module in the run's preview and applies only that saved plan (funcd ADR-0348).
# It is funcd's python314 image (images/runtime/python314 in pyvvo/funcd), the same pinned bases, plus OpenTofu and the
# providers that pins.json names, unmodified and checked against their pinned sha256 (MPL-2.0, NOTICE).
#
# Build it with the shim of pyvvo/funcd-python as the named context `shim` (its shim/ folder):
#   docker build --build-context shim=<funcd-python>/shim -f infrastack/runtime/Dockerfile infrastack/runtime

FROM python:3.14-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b AS py
ARG TARGETARCH
COPY --from=shim src/funcd_shim /opt/funcd/funcd_shim
# The shared-library closure of the interpreter and its stdlib C-extensions, which distroless/cc lacks, as funcd's
# python314 image gathers it.
RUN set -eux; mkdir -p /opt/funcd-libs; \
    { ldd /usr/local/lib/libpython3.14.so* || true; \
      for f in /usr/local/lib/python3.14/lib-dynload/*.so; do ldd "$f" || true; done; } 2>/dev/null \
      | awk '/=>/ {print $3}' \
      | grep -E '^/(usr/)?lib' \
      | grep -vE 'libc\.so|libm\.so|libpython|ld-linux|libstdc|libgcc|libdl|libpthread|librt' \
      | sort -u \
      | while read -r lib; do cp -L "$lib" /opt/funcd-libs/; done
COPY pins.json fetch.py /build/
RUN python3 /build/fetch.py /build/pins.json "${TARGETARCH}" /opt/tofu && /opt/tofu/bin/tofu version
COPY entry.py handler.py /opt/infrastack/

FROM gcr.io/distroless/cc-debian12@sha256:e5d81ddde149641e2a9ba55be4545bc125c67de07508b03ba4c22e6eb0ded5aa
COPY --from=py /usr/local/bin/python3.14 /usr/local/bin/python3.14
COPY --from=py /usr/local/lib/libpython3.14.so* /usr/local/lib/
COPY --from=py /usr/local/lib/python3.14 /usr/local/lib/python3.14
COPY --from=py /opt/funcd-libs/ /usr/local/lib/
COPY --from=py /opt/funcd /opt/funcd
COPY --from=py /opt/tofu /opt/tofu
COPY --from=py /opt/infrastack /opt/infrastack

ENV LD_LIBRARY_PATH=/usr/local/lib \
    PYTHONHOME=/usr/local \
    PYTHONPATH=/opt/funcd \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["/usr/local/bin/python3.14", "/opt/infrastack/entry.py"]
