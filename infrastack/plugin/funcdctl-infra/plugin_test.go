package main

import (
	"archive/tar"
	"bufio"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/pyvvo/funcd-addons/internal/pluginkit/plugintest"
)

// The contract of a module with the string variable prefix and the plain output name, as funcd ADR-0355's Contracts
// write it.
const exampleContract = `runtime: opentofu1
input:
  type: object
  additionalProperties: false
  properties:
    action:
      type: string
      enum:
        - apply
        - destroy
        - plan
    variables:
      type: object
      additionalProperties: false
      properties:
        prefix:
          type: string
      required:
        - prefix
  required:
    - action
    - variables
output:
  type: object
  additionalProperties: false
  properties:
    outputs:
      type: object
      additionalProperties: false
      properties:
        name: {}
    plan:
      type: object
      additionalProperties: false
      properties:
        add:
          type: integer
        change:
          type: integer
        destroy:
          type: integer
        replace:
          type: integer
        resources:
          type: array
          items:
            type: string
      required:
        - add
        - change
        - destroy
        - replace
        - resources
`

const (
	rootShown = `{"root_module": {
		"variables": {"prefix": {"type": "string", "required": true},
		              "token": {"type": "string", "required": true, "sensitive": true}},
		"outputs": {"name": {}, "secret": {"sensitive": true}},
		"module_calls": {"sub": {"source": "./sub"}}}}`
	subShown = `{"root_module": {"variables": {"x": {"type": "string", "required": true}}, "outputs": {"y": {}}}}`
)

// fixture is a test's fake engine and fake funcdctl, first on PATH, with their state folders, and the module
// random-stack, which calls its local child ./sub and holds the files a push leaves out.
type fixture struct {
	dir, bin, engine, funcd, module string
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	f := &fixture{dir: t.TempDir()}
	f.bin = plugintest.Install(t, "docker", "funcdctl")
	f.engine, f.funcd = filepath.Join(f.dir, "engine"), filepath.Join(f.dir, "funcd")
	f.module = filepath.Join(f.dir, "work", "random-stack")
	files := map[string]string{
		"main.tf":                      "module \"sub\" {\n  source = \"./sub\"\n}\n",
		"outputs.tf":                   "# outputs\n",
		"sub/main.tf":                  "# the child\n",
		"random-stack.contract.yaml":   "# written by hand\n",
		"random-stack.contract.json":   "{}",
		"__funcd_contract.json":        "{}",
		".funcd-providers/stale.zip":   "stale",
		".terraform/terraform.tfstate": "{}",
	}
	for name, text := range files {
		write(t, filepath.Join(f.module, name), text)
	}
	for _, dir := range []string{f.engine, f.funcd, filepath.Join(f.module, ".terraform", "providers")} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	f.shows(t, map[string]string{".": rootShown, "sub": subShown})
	t.Setenv("FAKE_ENGINE", f.engine)
	t.Setenv("FAKE_FUNCDCTL", f.funcd)
	t.Setenv("FUNCDCTL", filepath.Join(f.bin, "funcdctl"))
	t.Setenv(imageEnv, "")
	return f
}

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func (f *fixture) shows(t *testing.T, shown map[string]string) {
	t.Helper()
	raw := map[string]json.RawMessage{}
	for at, text := range shown {
		raw[at] = json.RawMessage(text)
	}
	b, err := json.Marshal(raw)
	if err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(f.engine, "shows.json"), string(b))
}

func (f *fixture) objects(t *testing.T, objects string) {
	t.Helper()
	write(t, filepath.Join(f.funcd, "objects.json"), objects)
}

func (f *fixture) awaiting(t *testing.T, names ...string) {
	t.Helper()
	var runs []map[string]map[string]string
	for _, n := range names {
		runs = append(runs, map[string]map[string]string{"metadata": {"name": n}})
	}
	b, err := json.Marshal(runs)
	if err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(f.funcd, "runs.json"), string(b))
}

func (f *fixture) stack(t *testing.T, reason, message string) {
	t.Helper()
	st := map[string]any{"infrastack/edge": map[string]any{
		"spec":   map[string]any{"bindings": map[string]any{"job": []any{map[string]string{"alias": "stack", "job": "edge"}}}},
		"status": map[string]any{"conditions": []any{map[string]string{"type": "Ready", "status": "False", "reason": reason, "message": message}}},
	}}
	b, err := json.Marshal(st)
	if err != nil {
		t.Fatal(err)
	}
	f.objects(t, string(b))
}

func (f *fixture) engineCalls(t *testing.T) [][]string {
	t.Helper()
	var calls [][]string
	readLines(t, filepath.Join(f.engine, "calls.jsonl"), func(line []byte) {
		var c []string
		if err := json.Unmarshal(line, &c); err != nil {
			t.Fatal(err)
		}
		calls = append(calls, c)
	})
	return calls
}

func (f *fixture) funcdCalls(t *testing.T) []funcdCall {
	t.Helper()
	var calls []funcdCall
	readLines(t, filepath.Join(f.funcd, "calls.jsonl"), func(line []byte) {
		var c funcdCall
		if err := json.Unmarshal(line, &c); err != nil {
			t.Fatal(err)
		}
		calls = append(calls, c)
	})
	return calls
}

func readLines(t *testing.T, path string, each func([]byte)) {
	t.Helper()
	file, err := os.Open(path)
	if os.IsNotExist(err) {
		return
	}
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = file.Close()
	}()
	s := bufio.NewScanner(file)
	s.Buffer(nil, 1<<20)
	for s.Scan() {
		each(s.Bytes())
	}
	if err := s.Err(); err != nil {
		t.Fatal(err)
	}
}

// run runs the plugin on args and returns its exit code, stdout and stderr.
func run(args ...string) (int, string, string) {
	var stdout, stderr bytes.Buffer
	code := plugin().Run(args, strings.NewReader(""), &stdout, &stderr)
	return code, stdout.String(), stderr.String()
}

// refused runs the plugin on args and fails the test unless it exits 1 with a message that matches pattern.
func refused(t *testing.T, pattern string, args ...string) {
	t.Helper()
	code, out, stderr := run(args...)
	if code != 1 || out != "" || !regexp.MustCompile(pattern).MatchString(stderr) ||
		!strings.HasPrefix(stderr, "funcdctl infra: ") {
		t.Fatalf("%v: exit %d, stdout %q, stderr %q: want 1 and %q", args, code, out, stderr, pattern)
	}
}

func commandsOf(t *testing.T, create []string) [][]string {
	t.Helper()
	var commands [][]string
	if err := json.Unmarshal([]byte(create[len(create)-1]), &commands); err != nil {
		t.Fatal(err)
	}
	return commands
}

// scenario: infra-push-builds-index
//
// The push reads the module and its local child, locks both platforms, and pushes one bundle per platform, each with
// its own provider packages and the contract made from the module, joined by an index.
func TestScenarioInfraPushBuildsIndex(t *testing.T) {
	f := newFixture(t)
	ref := "oci-layout:///tmp/layout:stack-1"
	code, out, stderr := run("push", f.module, ref)
	if code != 0 || !strings.HasPrefix(out, ref+"@sha256:") || strings.Count(out, "\n") != 1 {
		t.Fatalf("exit %d, stdout %q, stderr %q", code, out, stderr)
	}
	calls := f.funcdCalls(t)
	var pushes []funcdCall
	for _, c := range calls {
		if c.Args[0] == "push" {
			pushes = append(pushes, c)
		}
	}
	want := [][]string{
		{ref + "-linux-amd64", "--entry", "main.tf", "--platform", "linux/amd64"},
		{ref + "-linux-arm64", "--entry", "main.tf", "--platform", "linux/arm64"},
	}
	if len(pushes) != 2 || !reflect.DeepEqual(pushes[0].Args[2:], want[0]) || !reflect.DeepEqual(pushes[1].Args[2:], want[1]) {
		t.Fatalf("the pushes: %v", pushes)
	}
	if last := calls[len(calls)-1].Args; !reflect.DeepEqual(last, []string{"index", ref, ref + "-linux-amd64", ref + "-linux-arm64"}) {
		t.Fatalf("the index: %v", last)
	}
	for i, arch := range []string{"amd64", "arm64"} {
		p := pushes[i]
		if filepath.Base(p.Args[1]) != "random-stack" {
			t.Errorf("the build folder %s is not named as the module's", p.Args[1])
		}
		pkg := ".funcd-providers/registry.opentofu.org/hashicorp/random/terraform-provider-random_3.6.3_linux_" + arch + ".zip"
		if !slices.Contains(p.Files, pkg) {
			t.Errorf("%s: no %s in %v", arch, pkg, p.Files)
		}
		for _, file := range p.Files {
			if strings.HasSuffix(file, ".zip") && !strings.Contains(file, arch) {
				t.Errorf("%s: another platform's package %s", arch, file)
			}
			if strings.HasPrefix(file, ".terraform/") {
				t.Errorf("%s: %s is under .terraform/", arch, file)
			}
		}
		for _, kept := range []string{"main.tf", "outputs.tf", "sub/main.tf", ".terraform.lock.hcl", "random-stack.contract.yaml"} {
			if !slices.Contains(p.Files, kept) {
				t.Errorf("%s: no %s in %v", arch, kept, p.Files)
			}
		}
		if strings.Contains(p.Contract, "written by hand") {
			t.Errorf("%s: the module's own contract file is pushed", arch)
		}
		for _, left := range []string{"random-stack.contract.json", "__funcd_contract.json", ".funcd-providers/stale.zip"} {
			if slices.Contains(p.Files, left) {
				t.Errorf("%s: the module's own %s is pushed", arch, left)
			}
		}
		if !strings.Contains(p.Contract, "runtime: opentofu1") {
			t.Errorf("%s: the contract:\n%s", arch, p.Contract)
		}
	}
	lock, err := os.ReadFile(filepath.Join(f.module, lockFile))
	if err != nil || string(lock) != "# hashes for linux_amd64 linux_arm64\n" {
		t.Errorf("the lock file is not copied back into the module: %q, %v", lock, err)
	}
	var shows [][]string
	for _, c := range f.engineCalls(t) {
		if c[0] == "create" && strings.Contains(c[len(c)-1], "show") {
			var at []string
			for _, cmd := range commandsOf(t, c) {
				at = append(at, cmd[len(cmd)-1])
			}
			shows = append(shows, at)
		}
	}
	if !reflect.DeepEqual(shows, [][]string{{"-module=."}, {"-module=sub"}}) {
		t.Errorf("the child is not read too: %v", shows)
	}
}

func TestThePushRunsOpenTofuOnlyInTheAddonsImageWithNoBindMount(t *testing.T) {
	f := newFixture(t)
	if code, _, stderr := run("push", f.module, "registry.example.com/stacks/random:1"); code != 0 {
		t.Fatal(stderr)
	}
	var creates [][]string
	for _, c := range f.engineCalls(t) {
		if !slices.Contains([]string{"create", "cp", "start", "rm"}, c[0]) {
			t.Errorf("the engine ran %v", c)
		}
		if c[0] == "create" {
			creates = append(creates, c)
		}
	}
	if len(creates) != 5 {
		t.Fatalf("%d containers: want the module's and its child's configurations, the lock, a mirror per platform", len(creates))
	}
	for _, c := range creates {
		if !slices.Contains(c, image) {
			t.Errorf("not the Addon's image: %v", c)
		}
		for _, mount := range []string{"-v", "--volume", "--mount"} {
			if slices.Contains(c, mount) {
				t.Errorf("a bind mount: %v", c)
			}
		}
	}
	if got := commandsOf(t, creates[2]); !reflect.DeepEqual(got, [][]string{{"get"}, {"providers", "lock", "-platform=linux_amd64", "-platform=linux_arm64"}}) {
		t.Errorf("the lock: %v", got)
	}
	for i, arch := range []string{"amd64", "arm64"} {
		want := [][]string{{"get"}, {"providers", "mirror", "-platform=linux_" + arch, ".funcd-providers"}}
		if got := commandsOf(t, creates[3+i]); !reflect.DeepEqual(got, want) {
			t.Errorf("the %s mirror: %v", arch, got)
		}
	}
	if err := os.Remove(filepath.Join(f.engine, "calls.jsonl")); err != nil {
		t.Fatal(err)
	}
	t.Setenv(imageEnv, "funcd-addons/opentofu:check")
	if code, _, stderr := run("push", f.module, "registry.example.com/stacks/random:1"); code != 0 {
		t.Fatal(stderr)
	}
	for _, c := range f.engineCalls(t) {
		if c[0] == "create" && !slices.Contains(c, "funcd-addons/opentofu:check") {
			t.Errorf("not the image %s names: %v", imageEnv, c)
		}
	}
}

func TestACopyTheEngineRefusesFailsWithItsWords(t *testing.T) {
	f := newFixture(t)
	eng, err := newEngine()
	if err != nil {
		t.Fatal(err)
	}
	err = eng.copyOut("gone", "x", filepath.Join(f.module, "x"))
	if err == nil || err.Error() != "docker cp failed: unable to copy: source does not exist" {
		t.Fatalf("got %v", err)
	}
}

func TestUnpackTakesPlainFilesAndFoldersInsideItsTarget(t *testing.T) {
	f := newFixture(t)
	type member struct {
		name string
		kind byte
	}
	tarOf := func(members ...member) *tar.Reader {
		var b bytes.Buffer
		tw := tar.NewWriter(&b)
		for _, m := range members {
			h := &tar.Header{Name: m.name, Typeflag: m.kind, Mode: 0o640}
			if m.kind == tar.TypeReg {
				h.Size = 1
			}
			if m.kind == tar.TypeSymlink {
				h.Linkname = "/etc/passwd"
			}
			if err := tw.WriteHeader(h); err != nil {
				t.Fatal(err)
			}
			if m.kind == tar.TypeReg {
				if _, err := tw.Write([]byte("x")); err != nil {
					t.Fatal(err)
				}
			}
		}
		if err := tw.Close(); err != nil {
			t.Fatal(err)
		}
		return tar.NewReader(&b)
	}
	into := filepath.Join(f.module, "out")
	if err := unpack(tarOf(member{"p", tar.TypeDir}, member{"p/a/b.zip", tar.TypeReg}), into); err != nil {
		t.Fatal(err)
	}
	if b, err := os.ReadFile(filepath.Join(into, "a", "b.zip")); err != nil || string(b) != "x" {
		t.Fatalf("%q, %v", b, err)
	}
	if info, err := os.Stat(filepath.Join(into, "a", "b.zip")); err != nil || info.Mode().Perm() != 0o640 {
		t.Fatalf("the mode: %v, %v", info.Mode(), err)
	}
	for _, members := range [][]member{
		{{"p", tar.TypeDir}, {"p/../../x", tar.TypeReg}},
		{{"/p/x", tar.TypeReg}},
		{{"p", tar.TypeDir}, {"p/link", tar.TypeSymlink}},
	} {
		err := unpack(tarOf(members...), filepath.Join(f.module, "refused"))
		if err == nil || !strings.Contains(err.Error(), "no plain file or folder") {
			t.Errorf("%v: got %v", members, err)
		}
		if _, err := os.Lstat(filepath.Join(filepath.Dir(f.module), "x")); !os.IsNotExist(err) {
			t.Errorf("%v: a file outside the target", members)
		}
	}
}

func TestTheEngineIsDockerElseNerdctl(t *testing.T) {
	f := newFixture(t)
	if err := os.Rename(filepath.Join(f.bin, "docker"), filepath.Join(f.bin, "nerdctl")); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", f.bin)
	eng, err := newEngine()
	if err != nil || filepath.Base(eng.binary) != "nerdctl" {
		t.Fatalf("%v, %v", eng, err)
	}
	if err := os.Remove(filepath.Join(f.bin, "nerdctl")); err != nil {
		t.Fatal(err)
	}
	if _, err := newEngine(); err == nil || !strings.Contains(err.Error(), "neither docker nor nerdctl") {
		t.Fatalf("got %v", err)
	}
}

// scenario: infra-remote-module-refused
//
// A call of a registry module, or of a folder outside the module's, pushes nothing and names the call; so does one of
// a local child.
func TestScenarioInfraRemoteModuleRefused(t *testing.T) {
	f := newFixture(t)
	for source, named := range map[string]string{
		"hashicorp/consul/aws":           "consul (hashicorp/consul/aws)",
		"../shared":                      "consul (../shared)",
		"git::https://example.com/m.git": "consul (git::https://example.com/m.git)",
		"./sub/../..":                    "consul (./sub/../..)",
	} {
		f.shows(t, map[string]string{".": `{"root_module": {"module_calls": {"consul": {"source": "` + source + `"}}}}`})
		refused(t, regexp.QuoteMeta("the module calls "+named), "push", f.module, "registry.example.com/stacks/random:1")
		if calls := f.funcdCalls(t); len(calls) != 0 {
			t.Fatalf("%s: pushed %v", source, calls)
		}
	}
	f.shows(t, map[string]string{".": rootShown, "sub": `{"root_module": {"module_calls": {"vpc": {"source": "terraform-aws-modules/vpc/aws"}}}}`})
	refused(t, regexp.QuoteMeta("its child module sub calls vpc (terraform-aws-modules/vpc/aws)"), "push", f.module,
		"registry.example.com/stacks/random:1")
	if calls := f.funcdCalls(t); len(calls) != 0 {
		t.Fatalf("pushed %v", calls)
	}
}

func TestModuleCallsTakesRelativePathsInsideTheFolder(t *testing.T) {
	t.Parallel()
	shownOf := func(text string) shown {
		var m shown
		if err := json.Unmarshal([]byte(text), &m); err != nil {
			t.Fatal(err)
		}
		return m
	}
	all := shownOf(`{"root_module": {"module_calls": {"a": {"source": "./a"}, "b": {"source": "./x/../b"}, "up": {"source": "../up"},
		"abs": {"source": "/abs"}, "bare": {"source": "sub"}, "reg": {"source": "hashicorp/consul/aws"}}}}`)
	if got := moduleCalls(all, "."); !reflect.DeepEqual(got, []string{"abs (/abs)", "bare (sub)", "reg (hashicorp/consul/aws)", "up (../up)"}) {
		t.Errorf("got %v", got)
	}
	if got := moduleCalls(shownOf(`{"root_module": {"module_calls": {"s": {"source": "../sibling"}}}}`), "sub"); len(got) != 0 {
		t.Errorf("a child's call that stays inside the module's folder: %v", got)
	}
	if got := moduleCalls(shownOf(`{"root_module": {"module_calls": {"s": {"source": "../../out"}}}}`), "sub"); !reflect.DeepEqual(got, []string{"s (../../out)"}) {
		t.Errorf("got %v", got)
	}
}

// scenario: infra-local-files-refused (the push's half; the run's half is infrastack/runtime/handler_test.py's)
//
// A module folder that holds a state or a variables file OpenTofu would load pushes nothing and names the file; one
// under .terraform/ is OpenTofu's own.
func TestScenarioInfraLocalFilesRefused(t *testing.T) {
	f := newFixture(t)
	for _, name := range []string{"terraform.tfstate", "terraform.tfstate.backup", "terraform.tfvars", "terraform.tfvars.json",
		"prod.auto.tfvars", "prod.auto.tfvars.json", "sub/terraform.tfvars"} {
		path := filepath.Join(f.module, name)
		write(t, path, "prefix = \"x\"\n")
		refused(t, regexp.QuoteMeta("random-stack holds "+name), "push", f.module, "registry.example.com/stacks/random:1")
		if err := os.Remove(path); err != nil {
			t.Fatal(err)
		}
		if len(f.engineCalls(t)) != 0 || len(f.funcdCalls(t)) != 0 {
			t.Fatalf("%s: something ran or was pushed", name)
		}
	}
}

func TestThePushNeedsAFolderWithATFFileAndATaggedRef(t *testing.T) {
	f := newFixture(t)
	empty := filepath.Join(f.dir, "empty")
	if err := os.Mkdir(empty, 0o755); err != nil {
		t.Fatal(err)
	}
	refused(t, regexp.QuoteMeta("empty is no folder that holds a .tf file"), "push", empty, "registry.example.com/stacks/random:1")
	for _, ref := range []string{"registry.example.com/stacks/random", "registry.example.com/stacks/random@sha256:" + strings.Repeat("0", 64),
		"localhost:5000/random"} {
		refused(t, "names no tag", "push", f.module, ref)
	}
}

func TestARefusedPushEndsWithFuncdctlsMessage(t *testing.T) {
	f := newFixture(t)
	refusing := filepath.Join(f.dir, "funcdctl-refusing")
	write(t, refusing, "#!/bin/sh\necho 'funcdctl: the contract file is outside the funcd profile' >&2\nexit 1\n")
	if err := os.Chmod(refusing, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FUNCDCTL", refusing)
	refused(t, "outside the funcd profile", "push", f.module, "registry.example.com/stacks/random:1")
}

func TestSchemaFollowsTheTypeTableRowByRow(t *testing.T) {
	t.Parallel()
	for _, row := range []struct {
		tofuType string
		want     obj
	}{
		{`"string"`, obj{{"type", "string"}}},
		{`"number"`, obj{{"type", "number"}}},
		{`"bool"`, obj{{"type", "boolean"}}},
		{`["list", "string"]`, obj{{"type", "array"}, {"items", obj{{"type", "string"}}}}},
		{`["set", "number"]`, obj{{"type", "array"}, {"items", obj{{"type", "number"}}}}},
		{`["tuple", ["string", "number"]]`, obj{{"type", "array"}, {"items", obj{}}}},
		{`["map", "bool"]`, obj{{"type", "object"}, {"additionalProperties", obj{{"type", "boolean"}}}}},
		{`["object", {"a": "string", "b": "number"}, ["b"]]`, obj{{"type", "object"}, {"additionalProperties", false},
			{"properties", obj{{"a", obj{{"type", "string"}}}, {"b", obj{{"type", "number"}}}}}, {"required", []string{"a"}}}},
		{`["object", {"a": "string"}]`, obj{{"type", "object"}, {"additionalProperties", false},
			{"properties", obj{{"a", obj{{"type", "string"}}}}}, {"required", []string{"a"}}}},
		{`["object", {}]`, obj{}},
		{`["list", ["object", {"n": "dynamic"}, ["n"]]]`, obj{{"type", "array"}, {"items", obj{{"type", "object"},
			{"additionalProperties", false}, {"properties", obj{{"n", obj{}}}}}}}},
		{`"dynamic"`, obj{}},
		{`null`, obj{}},
	} {
		var tofuType any
		if err := json.Unmarshal([]byte(row.tofuType), &tofuType); err != nil {
			t.Fatal(err)
		}
		if got := schema(tofuType); !reflect.DeepEqual(got, row.want) {
			t.Errorf("%s: got %v, want %v", row.tofuType, got, row.want)
		}
	}
}

func contractOf(t *testing.T, text string) obj {
	t.Helper()
	var m shown
	if err := json.Unmarshal([]byte(text), &m); err != nil {
		t.Fatal(err)
	}
	return contract(m)
}

// at is the value at the keys' path in a contract.
func at(t *testing.T, o obj, keys ...string) any {
	t.Helper()
	var v any = o
	for _, k := range keys {
		cur, ok := v.(obj)
		if !ok {
			t.Fatalf("%v: %v is no mapping", keys, v)
		}
		v = nil
		for _, f := range cur {
			if f.key == k {
				v = f.value
			}
		}
	}
	return v
}

func keysOf(o any) []string {
	var keys []string
	for _, f := range o.(obj) {
		keys = append(keys, f.key)
	}
	return keys
}

func TestTheContractIsMadeFromTheVariablesAndOutputs(t *testing.T) {
	t.Parallel()
	example := contractOf(t, `{"root_module": {"variables": {"prefix": {"type": "string", "required": true}}, "outputs": {"name": {}}}}`)
	if got := toYAML(example, ""); got != exampleContract {
		t.Errorf("not the contract of funcd ADR-0355's Contracts:\n%s", got)
	}
	doc := contractOf(t, rootShown)
	if got := keysOf(at(t, doc, "input", "properties", "variables", "properties")); !reflect.DeepEqual(got, []string{"prefix"}) {
		t.Errorf("an input schema holds a sensitive variable: %v", got)
	}
	if got := keysOf(at(t, doc, "output", "properties", "outputs", "properties")); !reflect.DeepEqual(got, []string{"name"}) {
		t.Errorf("an output schema holds a sensitive output: %v", got)
	}
	defaults := contractOf(t, `{"root_module": {"variables": {"a": {"type": "string", "default": null}, "b": {"default": "x"},
		"c": {"required": true}}}}`)
	if got := at(t, defaults, "input", "properties", "variables", "required"); !reflect.DeepEqual(got, []string{"c"}) {
		t.Errorf("one with a default is not optional: %v", got)
	}
	bare := contractOf(t, `{"root_module": {}}`)
	if got := at(t, bare, "output", "properties", "outputs"); !reflect.DeepEqual(got, obj{}) {
		t.Errorf("a record that would have no member is not the empty schema: %v", got)
	}
	if got, req := at(t, bare, "input", "properties", "variables"), at(t, bare, "input", "required"); !reflect.DeepEqual(got, voidSchema) ||
		!reflect.DeepEqual(req, []string{"action"}) {
		t.Errorf("a module with no variable takes some: %v, %v", got, req)
	}
	optional := contractOf(t, `{"root_module": {"variables": {"region": {"type": "string", "default": "DE"}}}}`)
	if got := at(t, optional, "input", "required"); !reflect.DeepEqual(got, []string{"action"}) {
		t.Errorf("variables with defaults only are required: %v", got)
	}
	if got := at(t, optional, "input", "properties", "variables", "properties"); !reflect.DeepEqual(got, obj{{"region", obj{{"type", "string"}}}}) {
		t.Errorf("got %v", got)
	}
}

// scenario: infra-sensitive-only-module
//
// funcd ADR-0355 Decision 3, amended 2026-11-24: a module whose only variable is sensitive takes void variables, a
// side that takes nothing as funcd ADR-0090 has a Function's, so a run's input that sets it is refused.
func TestScenarioInfraSensitiveOnlyModule(t *testing.T) {
	t.Parallel()
	onlySecret := contractOf(t, `{"root_module": {"variables": {"token": {"type": "string", "sensitive": true}}}}`)
	if got := at(t, onlySecret, "input", "properties", "variables"); !reflect.DeepEqual(got, obj{{"type", "null"}}) {
		t.Errorf("got %v", got)
	}
	if got := at(t, onlySecret, "input", "required"); !reflect.DeepEqual(got, []string{"action"}) {
		t.Errorf("got %v", got)
	}
	if !strings.Contains(toYAML(onlySecret, ""), "variables:\n      type: \"null\"\n") {
		t.Error("not the string null, YAML's null")
	}
}

func TestToYAMLIsBlockStyle(t *testing.T) {
	t.Parallel()
	got := toYAML(obj{{"a", obj{{"b", []string{"x", "true", "a b"}}, {"c", obj{}}, {"d", 3}, {"e", false}}}}, "")
	if want := "a:\n  b:\n    - x\n    - \"true\"\n    - \"a b\"\n  c: {}\n  d: 3\n  e: false\n"; got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

// scenario: infra-outputs-lists-them
//
// funcd ADR-0356 Decision 8: the stack's Job, its contract, its ConfigMaps' INFRASTACK_OUTPUTS_SECRET, a later one
// winning, and that Secret, each value the contract does not name printed as (sensitive).
func TestOutputsPrintsEachKeyAndNeverASensitiveValue(t *testing.T) {
	f := newFixture(t)
	b64 := func(text string) string { return base64.StdEncoding.EncodeToString([]byte(text)) }
	f.objects(t, `{
		"infrastack/edge": {"spec": {"bindings": {"job": [{"alias": "stack", "job": "edge"}]}}},
		"job/edge": {"spec": {"bindings": {"config": ["edge-a", "edge-b"]}},
		             "status": {"contract": {"output": {"properties": {"outputs": {"properties": {"host": {}}}}}}}},
		"configmap/edge-a": {"spec": {"data": {"INFRASTACK_OUTPUTS_SECRET": "first"}}},
		"configmap/edge-b": {"spec": {"data": {"INFRASTACK_OUTPUTS_SECRET": "edge-outputs"}}},
		"secret/edge-outputs": {"spec": {"data": {"host": "`+b64("a.b-cdn.net")+`", "FRP_TOKEN": "`+b64("s3cret")+`",
		                                          "extra": "`+b64("unnamed")+`"}}}}`)
	code, out, stderr := run("outputs", "edge", "-n", "team-a")
	if code != 0 {
		t.Fatal(stderr)
	}
	if want := "NAME       VALUE\nhost       a.b-cdn.net\nFRP_TOKEN  (sensitive)\nextra      (sensitive)\n"; out != want {
		t.Errorf("got %q, want %q", out, want)
	}
	if first := f.funcdCalls(t)[0].Args; !reflect.DeepEqual(first, []string{"get", "infrastack", "edge", "-n", "team-a", "-o", "json"}) {
		t.Errorf("the first read: %v", first)
	}
	code, out, stderr = run("outputs", "edge", "-n", "team-a", "-o", "json")
	if code != 0 {
		t.Fatal(stderr)
	}
	var printed struct {
		Outputs   map[string]string `json:"outputs"`
		Sensitive []string          `json:"sensitive"`
	}
	if err := json.Unmarshal([]byte(out), &printed); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(printed.Outputs, map[string]string{"host": "a.b-cdn.net"}) || !reflect.DeepEqual(printed.Sensitive, []string{"FRP_TOKEN", "extra"}) {
		t.Errorf("got %s", out)
	}
	for _, args := range [][]string{{"outputs", "edge", "-n", "team-a"}, {"outputs", "edge", "-n", "team-a", "-o", "json"}} {
		if _, out, stderr := run(args...); strings.Contains(out+stderr, "s3cret") {
			t.Errorf("%v prints a sensitive value", args)
		}
	}
	refused(t, `"yaml" is no output format`, "outputs", "edge", "-o", "yaml")
}

// Of the Job's runs that await approval, approve hands funcdctl job approve the one the stack's Ready condition names,
// so an older generation's run that still waits is never approved by mistake.
func TestApproveApprovesTheRunThatTheStackNames(t *testing.T) {
	f := newFixture(t)
	f.stack(t, "AwaitingApproval", "run edge-g2-abcd1234, which applies edge, awaits approval: funcdctl job approve edge-g2-abcd1234")
	f.awaiting(t, "edge-g1-abcd1234", "edge-g2-abcd1234")
	if code, _, stderr := run("approve", "edge", "-n", "team-a"); code != 0 {
		t.Fatal(stderr)
	}
	var args [][]string
	for _, c := range f.funcdCalls(t) {
		args = append(args, c.Args)
	}
	want := [][]string{
		{"get", "infrastack", "edge", "-n", "team-a", "-o", "json"},
		{"job", "runs", "edge", "-n", "team-a", "--phase", "AwaitingApproval", "-o", "json"},
		{"job", "approve", "edge-g2-abcd1234", "-n", "team-a"},
	}
	if !reflect.DeepEqual(args, want) {
		t.Errorf("got %v", args)
	}
}

// approve hands the person's terminal to funcdctl job approve, and ends with its exit code and none of its own words.
func TestApproveEndsWithFuncdctlsExitCode(t *testing.T) {
	f := newFixture(t)
	f.stack(t, "AwaitingApproval", "run edge-g2-abcd1234 awaits approval")
	f.awaiting(t, "edge-g2-abcd1234")
	declining := filepath.Join(f.dir, "funcdctl-declining")
	write(t, declining, "#!/bin/sh\nif [ \"$1\" = job ] && [ \"$2\" = approve ]; then read answer; echo \"approve? $answer\"; "+
		"echo 'funcdctl: not approved' >&2; exit 3; fi\nexec \""+filepath.Join(f.bin, "funcdctl")+"\" \"$@\"\n")
	if err := os.Chmod(declining, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FUNCDCTL", declining)
	var stdout, stderr bytes.Buffer
	code := plugin().Run([]string{"approve", "edge"}, strings.NewReader("n\n"), &stdout, &stderr)
	if code != 3 || stdout.String() != "approve? n\n" || stderr.String() != "funcdctl: not approved\n" {
		t.Fatalf("exit %d, stdout %q, stderr %q", code, stdout.String(), stderr.String())
	}
}

func TestApproveRefusesAStackThatAwaitsNoApproval(t *testing.T) {
	f := newFixture(t)
	f.stack(t, "Planning", "run edge-g1-abcd1234 plans its change")
	refused(t, regexp.QuoteMeta("the InfraStack default/edge awaits no approval: run edge-g1-abcd1234 plans its change"), "approve", "edge")
	for _, c := range f.funcdCalls(t) {
		if c.Args[0] == "job" && c.Args[1] == "approve" {
			t.Fatal("a run was approved")
		}
	}
}

func TestApproveRefusesWhenTheNamedRunNoLongerAwaits(t *testing.T) {
	f := newFixture(t)
	f.stack(t, "AwaitingApproval", "run edge-d3-abcd1234, which destroys edge, awaits approval: funcdctl job approve edge-d3-abcd1234")
	f.awaiting(t, "edge-g2-abcd1234")
	refused(t, "names no run that awaits approval", "approve", "edge")
	for _, c := range f.funcdCalls(t) {
		if c.Args[0] == "job" && c.Args[1] == "approve" {
			t.Fatal("a run was approved")
		}
	}
}

func TestOutputsNeedsTheJobBoundAsStack(t *testing.T) {
	f := newFixture(t)
	f.objects(t, `{"infrastack/edge": {"spec": {"bindings": {"job": [{"alias": "other", "job": "edge"}]}}}}`)
	refused(t, regexp.QuoteMeta("the InfraStack default/edge binds no Job as stack"), "outputs", "edge")
}

func TestARefusedReadEndsOutputsWithFuncdctlsError(t *testing.T) {
	f := newFixture(t)
	f.objects(t, `{"infrastack/edge": {"spec": {"bindings": {"job": [{"alias": "stack", "job": "edge"}]}}}}`)
	refused(t, regexp.QuoteMeta("store.Get: job/edge not found"), "outputs", "edge")
	f.objects(t, `{"infrastack/edge": {"spec": {"bindings": {"job": [{"alias": "stack", "job": "edge"}]}}}, "job/edge": {"spec": {}}}`)
	refused(t, "binds no ConfigMap that names INFRASTACK_OUTPUTS_SECRET", "outputs", "edge")
}

// stamp runs stamp.go, as `just plugins` does, on the Addon at addon, and returns the image it stamps.
func stamp(t *testing.T, addon string) string {
	t.Helper()
	out, err := exec.Command("go", "run", "stamp.go", addon).Output()
	if err != nil {
		t.Fatalf("go run stamp.go %s: %v", addon, err)
	}
	flag, ok := strings.CutPrefix(strings.TrimSpace(string(out)), "-X main.image=")
	if !ok {
		t.Fatalf("stamp.go printed %q", out)
	}
	return flag
}

// funcd ADR-0355 Implementation plan step 4.
func TestThePluginsImageIsItsAddonsOpentofu1Image(t *testing.T) {
	t.Parallel()
	if got := stamp(t, filepath.Join("..", "..", "app", "addon.yaml")); got != image {
		t.Errorf("the plugin's image %s is not its Addon's, %s", image, got)
	}
	if !regexp.MustCompile(`^ghcr\.io/pyvvo/funcd-opentofu@sha256:[0-9a-f]{64}$`).MatchString(image) {
		t.Errorf("%s is not pinned by digest", image)
	}
}

// A release build takes the image of the Addon it ships beside: the plugin built with stamp.go's flags runs OpenTofu
// in that image.
func TestTheReleaseStampsTheAddonsImage(t *testing.T) {
	f := newFixture(t)
	addon := filepath.Join(f.dir, "addon.yaml")
	stamped := "ghcr.io/pyvvo/funcd-opentofu@sha256:" + strings.Repeat("a", 64)
	write(t, addon, "spec:\n  runtimes:\n    - name: opentofu1\n      image: "+stamped+"\n    - name: infrastack\n")
	if got := stamp(t, addon); got != stamped {
		t.Fatalf("stamp.go took %s", got)
	}
	binary := filepath.Join(f.dir, "funcdctl-infra")
	build := exec.Command("go", "build", "-ldflags", "-X main.image="+stamped, "-o", binary, ".")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("%v: %s", err, out)
	}
	if out, err := exec.Command(binary, "push", f.module, "registry.example.com/stacks/random:1").CombinedOutput(); err != nil {
		t.Fatalf("%v: %s", err, out)
	}
	for _, c := range f.engineCalls(t) {
		if c[0] == "create" && !slices.Contains(c, stamped) {
			t.Fatalf("not the stamped image: %v", c)
		}
	}
}

// The plugin page's command reference is the one the plugin's commands generate: `just plugin-docs` writes it.
func TestReference(t *testing.T) {
	plugintest.CheckReference(t, plugin(), filepath.Join("..", "..", "docs", "plugin.md"))
}
