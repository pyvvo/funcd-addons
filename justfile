# Run every recipe through the pinned dev shell: `nix develop -c just <recipe>`.

default:
    @just --list

# check the manifests, build and test the plugins, and test the InfraStack handler and reconciler
ci:
    test -z "$(gofmt -l .)" || { gofmt -l .; exit 1; }
    go vet ./...
    go test -race ./...
    just test-infrastack

# test the InfraStack add-on's OpenTofu handler, with a stand-in for OpenTofu, its funcdctl plugin, with a stand-in for
# the container engine and funcdctl, and its reconciler Function
test-infrastack:
    python3 -m unittest infrastack/runtime/handler_test.py
    python3 -B -m unittest infrastack/plugin/plugin_test.py
    node --test 'infrastack/**/*.test.mjs'

# print the folder of the funcd-python module whose shim the OpenTofu runtime carries, at the version pins.json names
opentofu-shim:
    @go mod download -json "$(python3 -c 'import json; s = json.load(open("infrastack/runtime/pins.json"))["shim"]; print(s["module"] + "@" + s["version"])')" | python3 -c 'import json, sys; print(json.load(sys.stdin)["Dir"])'

# build the OpenTofu runtime image for this machine's Docker, tagged tag; shim is a funcd-python checkout's folder, else
# pins.json's
opentofu-image shim="" tag="funcd-addons/opentofu:dev":
    docker build --provenance=false --sbom=false --build-context shim="$(if [ -n '{{shim}}' ]; then echo '{{shim}}'; else echo "$(just opentofu-shim)/shim"; fi)" -f infrastack/runtime/Dockerfile -t {{tag}} infrastack/runtime

# run the OpenTofu runtime image's check: plan, apply and destroy the example module against versitygw on Docker
opentofu-image-check image="funcd-addons/opentofu:dev":
    infrastack/runtime/image_test.sh {{image}}

# build funcdctl at e2e/funcd.ref into work/bin
funcdctl:
    GOBIN="$PWD/work/bin" CGO_ENABLED=0 go install -tags dev "github.com/pyvvo/funcd/cmd/funcdctl@$(cat e2e/funcd.ref)"

# push a module with a local child through funcdctl-infra, with the image's OpenTofu, into an OCI layout (funcd ADR-0355)
infra-push-check image="funcd-addons/opentofu:dev" funcdctl="work/bin/funcdctl":
    INFRASTACK_OPENTOFU_IMAGE={{image}} FUNCDCTL="$(cd "$(dirname {{funcdctl}})" && pwd)/$(basename {{funcdctl}})" python3 infrastack/plugin/push_check.py

# write funcdctl-infra into out, its IMAGE the opentofu1 image of the Addon it ships beside (funcd ADR-0355 Decision 1)
infra-plugin out="dist/funcdctl-infra":
    mkdir -p "$(dirname {{out}})"
    python3 infrastack/plugin/stamp.py infrastack/app/addon.yaml infrastack/plugin/funcdctl-infra {{out}}

# build each add-on's funcdctl plugin for one platform into dist/<goos>-<goarch>/
plugins goos="" goarch="":
    #!/usr/bin/env bash
    set -euo pipefail
    os={{goos}}; arch={{goarch}}; os=${os:-$(go env GOOS)}; arch=${arch:-$(go env GOARCH)}
    for d in */plugin/*/; do
        ls "$d"*.go >/dev/null 2>&1 || continue
        name=$(basename "$d")
        GOOS=$os GOARCH=$arch CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o "dist/$os-$arch/$name" "./$d"
    done

# run each add-on under funcdctl dev on the local Docker, at the funcd of e2e/funcd.ref
e2e:
    python3 e2e/run.py

# run one lane of e2e/lanes.yaml on containerd in a Lima VM: funcd's `just lima-example <name>`, through funcd's dev
# shell, in a funcd checkout at e2e/funcd.ref that this recipe keeps under $HOME, since colima shares only $HOME; needs
# colima running. A change in that checkout is lost when e2e/funcd.ref moves.
lane name:
    #!/usr/bin/env bash
    set -euo pipefail
    ref=$(cat e2e/funcd.ref)
    src="$HOME/.cache/funcd-addons/funcd"
    [ -d "$src/.git" ] || git clone --quiet https://github.com/pyvvo/funcd.git "$src"
    if [ "$(git -C "$src" rev-parse HEAD)" != "$ref" ]; then
        git -C "$src" cat-file -e "$ref^{commit}" 2>/dev/null || git -C "$src" fetch --quiet origin "$ref"
        # funcd's image build marks its placeholder image tarballs skip-worktree, which no checkout replaces
        git -C "$src" ls-files -v | sed -n 's/^S //p' | git -C "$src" update-index --no-skip-worktree --stdin
        git -C "$src" checkout --quiet --force --detach "$ref"
    fi
    cd "$src"
    FUNCD_LANES='{{justfile_directory()}}/e2e/lanes.yaml' FUNCD_LANES_ROOT='{{justfile_directory()}}' \
        nix develop -c just lima-example '{{name}}'
