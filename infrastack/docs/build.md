# Build and check InfraStack

How the InfraStack add-on is built, checked and released.

## Files

```text
infrastack/
├── README.md
├── app/
│   └── addon.yaml
├── examples/
│   ├── random-stack/
│   │   ├── main.tf
│   │   └── random-stack.contract.yaml
│   ├── random-stack-reader.contract.yaml
│   ├── random-stack-reader.mjs
│   ├── random-stack-reader.test.mjs
│   ├── random-stack-reader.yaml
│   ├── random-stack.yaml
│   ├── token-stack/
│   │   └── main.tf
│   └── token-stack.yaml
├── plugin/
│   ├── funcdctl-infra
│   ├── plugin_test.py
│   ├── push_check.py
│   └── stamp.py
├── reconciler/
│   ├── infrastack-reconciler.contract.yaml
│   ├── infrastack-reconciler.mjs
│   └── infrastack-reconciler.test.mjs
└── runtime/
    ├── Dockerfile
    ├── entry.py
    ├── fetch.py
    ├── handler.py
    ├── handler_test.py
    ├── image_check.py
    ├── image_test.sh
    └── pins.json
```

## The checks

`opentofu-image-check` runs the handler with OpenTofu itself, in the image, against versitygw, the S3 engine of
funcd's gateway, on an internal Docker network that reaches nothing else. It runs a preview that adds three
resources, its approved apply, a second plan that changes nothing, a refused plan whose digest is not the approved
one, a run past a planted lock, and a destroy, with the state's encryption off and on. It then runs a module that
carries its own packed providers, with the image's folder out of reach, one whose provider package matches no hash of
its lock file, and a plan run.

`infra-push-check` pushes a module with a local child through the plugin into an OCI layout, and reads back the
index, the contract and each platform's packages. CI runs both checks.

## Releases

The release workflow publishes `ghcr.io/pyvvo/funcd-opentofu` for linux/amd64 and linux/arm64, with the shim of the
funcd-python release that [`runtime/pins.json`](../runtime/pins.json) names, and the reconciler as
`ghcr.io/pyvvo/funcd-infrastack-reconciler`. `just kind-images` writes the digest of each kind's Function into its
Addon: `funcdctl push` gives the same files that digest anywhere.
