# Run every recipe through the pinned dev shell: `nix develop -c just <recipe>`.

default:
    @just --list

# check the manifests and build and test the plugins
ci:
    test -z "$(gofmt -l .)" || { gofmt -l .; exit 1; }
    go vet ./...
    go test -race ./...

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
