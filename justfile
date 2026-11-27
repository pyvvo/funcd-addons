# Run every recipe through the pinned dev shell: `nix develop -c just <recipe>`.

default:
    @just --list

# check the manifests, the READMEs and the conventions, build and test the plugins, and test the InfraStack handler and
# reconciler
ci:
    test -z "$(gofmt -l .)" || { gofmt -l .; exit 1; }
    python3 scripts/readme-check.py
    python3 -B -m unittest scripts/lint_test.py
    just lint
    go test -race ./...
    just test-infrastack
    node --test 'rauthy/**/*.test.mjs'

# check the conventions of CLAUDE.md's Rules on every tracked file, or on files: golangci-lint, yamllint, ruff and
# eslint, then scripts/lint.py's own checks of hygiene, quoting and the plugins' layout
lint *files:
    python3 scripts/lint.py {{files}}

# test the InfraStack add-on's OpenTofu handler, with a stand-in for OpenTofu, and its reconciler Function; `go test`
# tests its funcdctl plugin
test-infrastack:
    python3 -m unittest infrastack/runtime/handler_test.py
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

# write the digest of each kind's Function into the Addon that names its image (funcd ADR-0358)
kind-images funcdctl="work/bin/funcdctl":
    python3 e2e/kind_images.py "$(cd "$(dirname {{funcdctl}})" && pwd)/$(basename {{funcdctl}})" --write

# fail when an Addon pins another digest than its kind's Function has
kind-images-check funcdctl="work/bin/funcdctl":
    python3 e2e/kind_images.py "$(cd "$(dirname {{funcdctl}})" && pwd)/$(basename {{funcdctl}})"

# push a module with a local child through funcdctl-infra, with the image's OpenTofu, into an OCI layout (funcd ADR-0355)
infra-push-check image="funcd-addons/opentofu:dev" funcdctl="work/bin/funcdctl":
    INFRASTACK_PUSH_CHECK=1 INFRASTACK_OPENTOFU_IMAGE={{image}} FUNCDCTL="$(cd "$(dirname {{funcdctl}})" && pwd)/$(basename {{funcdctl}})" \
        go test -count=1 -run '^TestPushCheck$' ./infrastack/plugin/funcdctl-infra/

# build each add-on's funcdctl plugin for one platform into out, by default dist/<goos>-<goarch>/. A plugin's stamp.go
# prints the -X flags of its build, as funcdctl-infra's stamps the opentofu1 image of its Addon (funcd ADR-0355 Decision 1).
plugins goos="" goarch="" out="":
    #!/usr/bin/env bash
    set -euo pipefail
    os={{goos}}; arch={{goarch}}; os=${os:-$(go env GOOS)}; arch=${arch:-$(go env GOARCH)}
    out={{out}}; out=${out:-dist/$os-$arch}
    for d in */plugin/funcdctl-*/; do
        d=${d%/}
        stamp=""
        if [ -f "$d/stamp.go" ]; then stamp=$(go run "./$d/stamp.go"); fi
        GOOS=$os GOARCH=$arch CGO_ENABLED=0 go build -trimpath -ldflags "-s -w $stamp" -o "$out/$(basename "$d")" "./$d"
    done

# write each plugin's command reference into its add-on's docs/plugin.md, between the page's markers, from the
# plugin's commands; `just ci` fails while one is stale
plugin-docs:
    UPDATE_REFERENCE=1 go test -count=1 -run '^TestReference$' ./...

# run each add-on under funcdctl dev on the local Docker, at the funcd of e2e/funcd.ref
e2e:
    python3 e2e/run.py

# keep the funcd checkout at e2e/funcd.ref that the lane recipes run funcd's lane harness in, under $HOME, since colima
# shares only $HOME. A change in that checkout is lost when e2e/funcd.ref moves.
[private]
funcd-checkout:
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

# funcd's `just lima-example <name>`, through funcd's dev shell, in the funcd checkout at e2e/funcd.ref; needs colima
# run one lane of e2e/lanes.yaml on containerd in a Lima VM
lane name: funcd-checkout
    cd "$HOME/.cache/funcd-addons/funcd" && FUNCD_LANES='{{justfile_directory()}}/e2e/lanes.yaml' \
        FUNCD_LANES_ROOT='{{justfile_directory()}}' nix develop -c just lima-example '{{name}}'

# funcd's `just lima-example-all`, in the funcd checkout at e2e/funcd.ref, one VM reset and leak-checked between lanes
# run every lane of e2e/lanes.yaml but the paid live one (the only optional lane), in one shared VM; needs colima
lanes: funcd-checkout
    cd "$HOME/.cache/funcd-addons/funcd" && FUNCD_LANES='{{justfile_directory()}}/e2e/lanes.yaml' \
        FUNCD_LANES_ROOT='{{justfile_directory()}}' nix develop -c just lima-example-all
