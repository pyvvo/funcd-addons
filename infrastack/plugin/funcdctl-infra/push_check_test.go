package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
)

// pushCheckEnv names the variable that `just infra-push-check` sets to run TestPushCheck, which needs docker, the
// OpenTofu image that INFRASTACK_OPENTOFU_IMAGE names and the funcdctl that FUNCDCTL names.
const pushCheckEnv = "INFRASTACK_PUSH_CHECK"

const (
	provider = "terraform {\n  required_providers {\n    random = {\n      source  = \"hashicorp/random\"\n" +
		"      version = \"3.6.3\"\n    }\n  }\n}\n"
	checkMain = provider + "\nvariable \"prefix\" {\n  type = string\n}\n\nvariable \"token\" {\n  type      = string\n" +
		"  sensitive = true\n  default   = \"unset\"\n}\n\nmodule \"sub\" {\n  source = \"./sub\"\n  prefix = var.prefix\n}\n\n" +
		"output \"name\" {\n  value = module.sub.name\n}\n"
	checkSub = provider + "\nvariable \"prefix\" {\n  type = string\n}\n\nresource \"random_pet\" \"name\" {\n  prefix = var.prefix\n" +
		"}\n\noutput \"name\" {\n  value = random_pet.name.id\n}\n"
	// A module whose only variable is sensitive (funcd ADR-0355 Decision 3, amended 2026-11-24).
	checkSecret = provider + "\nvariable \"token\" {\n  type      = string\n  sensitive = true\n}\n\n" +
		"resource \"random_pet\" \"name\" {\n  keepers = {\n    token = sha256(var.token)\n  }\n}\n"
)

// scenario: infra-push-builds-index, with real OpenTofu (funcd ADR-0355)
//
// The plugin pushes a module whose main.tf calls the local child module ./sub into an OCI layout. The check reads the
// index, the contract that funcdctl inspect prints, and each platform's bundle, which holds only its own provider
// packages. Then a module whose only variable is sensitive pushes, with void variables (the scenario
// infra-sensitive-only-module).
func TestPushCheck(t *testing.T) {
	if os.Getenv(pushCheckEnv) == "" {
		t.Skip("needs docker, the OpenTofu image and funcdctl: `just infra-push-check` runs it")
	}
	funcdctl := os.Getenv("FUNCDCTL")
	tmp := t.TempDir()
	module := filepath.Join(tmp, "stack")
	write(t, filepath.Join(module, "main.tf"), checkMain)
	write(t, filepath.Join(module, "sub", "main.tf"), checkSub)
	layout := filepath.Join(tmp, "layout")
	ref := "oci-layout://" + layout + ":stack-1"

	code, out, stderr := run("push", module, ref)
	if code != 0 {
		t.Fatalf("funcdctl infra push fails: %s", stderr)
	}
	got := strings.TrimSpace(out)
	digest, ok := strings.CutPrefix(got, ref+"@")
	if !ok || !strings.HasPrefix(digest, "sha256:") {
		t.Fatalf("it prints no <ref>@<digest>: %s", got)
	}
	if lock, err := os.ReadFile(filepath.Join(module, lockFile)); err != nil || !strings.Contains(string(lock), "hashicorp/random") {
		t.Fatalf("the lock file is not written into the module's folder, to commit: %v", err)
	}

	var index struct {
		Manifests []struct {
			Platform struct {
				OS           string `json:"os"`
				Architecture string `json:"architecture"`
			} `json:"platform"`
		} `json:"manifests"`
	}
	if err := readJSON(filepath.Join(layout, "blobs", "sha256", strings.TrimPrefix(digest, "sha256:")), &index); err != nil {
		t.Fatal(err)
	}
	var platformsOf []string
	for _, m := range index.Manifests {
		platformsOf = append(platformsOf, m.Platform.OS+"/"+m.Platform.Architecture)
	}
	slices.Sort(platformsOf)
	if !reflect.DeepEqual(platformsOf, platforms) {
		t.Fatalf("no image index of linux/amd64 and linux/arm64: %v", platformsOf)
	}

	type contractDoc struct {
		Input struct {
			Properties struct {
				Variables map[string]any `json:"variables"`
			} `json:"properties"`
			Required []string `json:"required"`
		} `json:"input"`
		Output struct {
			Properties struct {
				Outputs struct {
					Properties map[string]any `json:"properties"`
				} `json:"outputs"`
			} `json:"properties"`
		} `json:"output"`
	}
	inspect := func(pushed string) contractDoc {
		t.Helper()
		raw, err := exec.Command(funcdctl, "inspect", pushed).Output()
		if err != nil {
			t.Fatalf("funcdctl inspect %s: %v", pushed, err)
		}
		var doc contractDoc
		if err := json.Unmarshal(raw, &doc); err != nil {
			t.Fatal(err)
		}
		return doc
	}
	shown := inspect(got)
	wantVariables := map[string]any{
		"type":                 "object",
		"additionalProperties": false,
		"properties":           map[string]any{"prefix": map[string]any{"type": "string"}},
		"required":             []any{"prefix"},
	}
	if !reflect.DeepEqual(shown.Input.Properties.Variables, wantVariables) {
		t.Errorf("the contract's input is not the module's variables, the sensitive one left out: %v", shown.Input.Properties.Variables)
	}
	if outputs := shown.Output.Properties.Outputs.Properties; !reflect.DeepEqual(outputs, map[string]any{"name": map[string]any{}}) {
		t.Errorf("its output is not the module's plain outputs: %v", outputs)
	}

	for _, platform := range platforms {
		into := filepath.Join(tmp, strings.ReplaceAll(platform, "/", "-"))
		if out, err := exec.Command(funcdctl, "pull", ref, digest, into, "--platform", platform).CombinedOutput(); err != nil {
			t.Fatalf("funcdctl pull %s: %v: %s", platform, err, out)
		}
		var packages []string
		err := filepath.WalkDir(into, func(path string, d os.DirEntry, err error) error {
			if err == nil && strings.HasSuffix(path, ".zip") {
				packages = append(packages, d.Name())
			}
			return err
		})
		if err != nil {
			t.Fatal(err)
		}
		arch := strings.TrimPrefix(platform, "linux/")
		if !reflect.DeepEqual(packages, []string{"terraform-provider-random_3.6.3_linux_" + arch + ".zip"}) {
			t.Errorf("the %s bundle holds other packages than its own: %v", platform, packages)
		}
		for _, file := range []string{filepath.Join("sub", "main.tf"), lockFile} {
			if _, err := os.Stat(filepath.Join(into, file)); err != nil {
				t.Errorf("the %s bundle holds no %s", platform, file)
			}
		}
	}

	// scenario: infra-sensitive-only-module
	secret := filepath.Join(tmp, "secret")
	write(t, filepath.Join(secret, "main.tf"), checkSecret)
	code, out, stderr = run("push", secret, "oci-layout://"+layout+":secret-1")
	if code != 0 {
		t.Fatalf("a module whose only variable is sensitive does not push: %s", stderr)
	}
	input := inspect(strings.TrimSpace(out)).Input
	if !reflect.DeepEqual(input.Properties.Variables, map[string]any{"type": "null"}) || !reflect.DeepEqual(input.Required, []string{"action"}) {
		t.Errorf("its variables are not void, so a run's input sets some: %+v", input)
	}
}
