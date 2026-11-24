# Run every recipe through the pinned dev shell: `nix develop -c just <recipe>`.

default:
    @just --list

# check the manifests, build and test the plugins, and test the InfraStack handler and reconciler
ci:
    test -z "$(gofmt -l .)" || { gofmt -l .; exit 1; }
    go vet ./...
    go test -race ./...
    just test-infrastack

# test the InfraStack add-on's OpenTofu handler, with a stand-in for OpenTofu, and its reconciler Function
test-infrastack:
    python3 -m unittest infrastack/runtime/handler_test.py
    node --test 'infrastack/**/*.test.mjs'

# print the folder of the funcd-python module whose shim the OpenTofu runtime carries, at the version pins.json names
opentofu-shim:
    @go mod download -json "$(python3 -c 'import json; s = json.load(open("infrastack/runtime/pins.json"))["shim"]; print(s["module"] + "@" + s["version"])')" | python3 -c 'import json, sys; print(json.load(sys.stdin)["Dir"])'

# build the OpenTofu runtime image for this machine's Docker; shim is a funcd-python checkout's folder, else pins.json's
opentofu-image shim="":
    docker build --provenance=false --sbom=false --build-context shim="$(if [ -n '{{shim}}' ]; then echo '{{shim}}'; else echo "$(just opentofu-shim)/shim"; fi)" -f infrastack/runtime/Dockerfile -t funcd-addons/opentofu:dev infrastack/runtime

# run the OpenTofu runtime image's check: plan, apply and destroy the example module against versitygw on Docker
opentofu-image-check image="funcd-addons/opentofu:dev":
    infrastack/runtime/image_test.sh {{image}}

# build each add-on's funcdctl plugin for one platform into dist/<goos>-<goarch>/
plugins goos="" goarch="":
    #!/usr/bin/env bash
    set -euo pipefail
    os={{goos}}; arch={{goarch}}; os=${os:-$(go env GOOS)}; arch=${arch:-$(go env GOARCH)}
    for d in */plugin/*/; do
        name=$(basename "$d")
        GOOS=$os GOARCH=$arch CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o "dist/$os-$arch/$name" "./$d"
    done

# run each add-on under funcdctl dev on the local Docker, at the funcd of e2e/funcd.ref
e2e:
    python3 e2e/run.py
