package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/pyvvo/funcd-addons/internal/pluginkit/plugintest"
	yaml "go.yaml.in/yaml/v3"
	"golang.org/x/crypto/argon2"
)

func TestMain(m *testing.M) {
	plugintest.Main(m, map[string]plugintest.Fake{"funcdctl": fakeFuncdctl})
}

// fakeFuncdctl answers `get identityprovider <name>` from the JSON object FAKE_IDPS keys by name, and writes what
// `apply -f -` reads to FAKE_APPLIED.
func fakeFuncdctl(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	switch {
	case len(args) >= 3 && args[0] == "get" && args[1] == "identityprovider":
		var idps map[string]json.RawMessage
		if err := json.Unmarshal([]byte(os.Getenv("FAKE_IDPS")), &idps); err != nil {
			fmt.Fprintln(stderr, err)
			return 2
		}
		idp, ok := idps[args[2]]
		if !ok {
			fmt.Fprintf(stderr, "funcdctl: sdk: store.Get: identityprovider/%s not found\n", args[2])
			return 1
		}
		fmt.Fprintln(stdout, string(idp))
		return 0
	case strings.Join(args, " ") == "apply -f -":
		manifest, err := io.ReadAll(stdin)
		if err == nil {
			err = os.WriteFile(os.Getenv("FAKE_APPLIED"), manifest, 0o600)
		}
		if err != nil {
			fmt.Fprintln(stderr, err)
			return 2
		}
		fmt.Fprintln(stdout, "secret/rauthy-keys configured")
		return 0
	}
	fmt.Fprintf(stderr, "funcdctl: unknown command %q\n", strings.Join(args, " "))
	return 1
}

// fakes puts the fake funcdctl on PATH and in FUNCDCTL, as funcdctl sets it for its plugins, with the
// IdentityProviders idps, and returns the file that a fake apply writes.
func fakes(t *testing.T, idps string) string {
	t.Helper()
	bin := plugintest.Install(t, "funcdctl")
	applied := filepath.Join(t.TempDir(), "applied.yaml")
	t.Setenv("FUNCDCTL", filepath.Join(bin, "funcdctl"))
	t.Setenv("FAKE_IDPS", idps)
	t.Setenv("FAKE_APPLIED", applied)
	return applied
}

// run runs the plugin on args and returns its exit code, stdout and stderr.
func run(args ...string) (int, string, string) {
	var stdout, stderr bytes.Buffer
	code := plugin().Run(args, strings.NewReader(""), &stdout, &stderr)
	return code, stdout.String(), stderr.String()
}

func TestArgon2idIsAPHCStringOfThePassword(t *testing.T) {
	t.Parallel()
	hash, err := argon2id("correct horse")
	if err != nil {
		t.Fatal(err)
	}
	if !matchesPassword(hash, "correct horse") {
		t.Fatalf("not the PHC argon2id string of the password: %s", hash)
	}
}

// matchesPassword reports whether hash is the PHC argon2id string of password, at the plugin's cost.
func matchesPassword(hash, password string) bool {
	m := regexp.MustCompile(`^\$argon2id\$v=19\$m=65536,t=3,p=4\$([A-Za-z0-9+/]+)\$([A-Za-z0-9+/]+)$`).FindStringSubmatch(hash)
	if m == nil {
		return false
	}
	salt, err := base64.RawStdEncoding.DecodeString(m[1])
	if err != nil {
		return false
	}
	want := argon2.IDKey([]byte(password), salt, argonTime, argonMemoryKiB, argonThreads, argonKeyLen)
	return m[2] == base64.RawStdEncoding.EncodeToString(want)
}

func TestKeysPrintsEverySecretKey(t *testing.T) {
	t.Parallel()
	code, out, stderr := run("keys", "--admin-email", "admin@example.com", "--print", "--name", "k", "-n", "team",
		"--resource-group", "idp")
	if code != 0 {
		t.Fatalf("exit %d: %s", code, stderr)
	}
	for _, k := range []string{keyRaft, keyAPI, keyEncKeys, keyEncActive, keyAdminEmail, keyAdminPassword, keyAPIKey, keyAPIKeySecret, "name: k", "namespace: team", "resourceGroup: idp"} {
		if !strings.Contains(out, k) {
			t.Errorf("the Secret lacks %s:\n%s", k, out)
		}
	}
}

func TestKeysNeedsAnAdminEmail(t *testing.T) {
	t.Parallel()
	for _, args := range [][]string{{"keys", "--print"}, {"keys", "--print", "--admin-email", "admin"}} {
		code, out, stderr := run(args...)
		if code != 1 || out != "" || stderr != "funcdctl rauthy: --admin-email is required\n" {
			t.Errorf("%v: exit %d, stdout %q, stderr %q", args, code, out, stderr)
		}
	}
}

// keys applies the Secret through funcdctl apply -f -, whose own lines go to stderr, and prints the admin's password
// once, which the Secret holds only as its hash.
func TestKeysAppliesTheSecretAndPrintsThePasswordOnce(t *testing.T) {
	applied := fakes(t, "{}")
	code, out, stderr := run("keys", "--admin-email", "admin@example.com")
	if code != 0 {
		t.Fatalf("exit %d: %s", code, stderr)
	}
	m := regexp.MustCompile(`^admin: admin@example.com\npassword: ([a-zA-Z0-9]{24})\nStore the password now: funcd keeps only its hash.\n$`).FindStringSubmatch(out)
	if m == nil {
		t.Fatalf("stdout:\n%s", out)
	}
	if stderr != "secret/rauthy-keys configured\n" {
		t.Errorf("stderr %q: want funcdctl's own line", stderr)
	}
	raw, err := os.ReadFile(applied)
	if err != nil {
		t.Fatal(err)
	}
	var secret struct {
		Metadata struct {
			Name          string `yaml:"name"`
			Namespace     string `yaml:"namespace"`
			ResourceGroup string `yaml:"resourceGroup"`
		} `yaml:"metadata"`
		Spec struct {
			Data map[string]string `yaml:"data"`
		} `yaml:"spec"`
	}
	if err := yaml.Unmarshal(raw, &secret); err != nil {
		t.Fatal(err)
	}
	if secret.Metadata.Name != "rauthy-keys" || secret.Metadata.Namespace != "default" || secret.Metadata.ResourceGroup != "default" {
		t.Errorf("the Secret's defaults: %+v", secret.Metadata)
	}
	hash, err := base64.StdEncoding.DecodeString(secret.Spec.Data[keyAdminPassword])
	if err != nil || !matchesPassword(string(hash), m[1]) {
		t.Errorf("the Secret's %s is not the printed password's hash: %v", keyAdminPassword, err)
	}
	if strings.Contains(string(raw), m[1]) {
		t.Error("the Secret holds the password itself")
	}
}

func TestURLsReadTheIdentityProvidersConfig(t *testing.T) {
	fakes(t, `{"login": {"spec": {"config": {"publicHost": "login.example.com", "https": true}}}}`)
	code, out, stderr := run("urls", "login")
	if code != 0 {
		t.Fatalf("exit %d: %s", code, stderr)
	}
	if out != "issuer:  https://login.example.com/auth/v1/\naccount: https://login.example.com/auth/v1/account\n"+
		"admin:   https://login.example.com/auth/v1/admin\n" {
		t.Fatalf("unexpected URLs:\n%s", out)
	}
	for _, args := range [][]string{{"urls", "login", "-n", "team"}, {"urls", "-n", "team", "login"}} {
		if code, _, stderr := run(args...); code != 0 {
			t.Fatalf("%v: exit %d: %s", args, code, stderr)
		}
	}
	for _, args := range [][]string{{"urls"}, {"urls", "-n", "team"}, {"urls", "login", "other"}} {
		if code, _, stderr := run(args...); code != 1 || stderr != "funcdctl rauthy: usage: funcdctl rauthy urls <identityprovider> [flags]\n" {
			t.Fatalf("%v: exit %d, stderr %q: want the usage, one IdentityProvider", args, code, stderr)
		}
	}
}

// A refused read ends urls with funcdctl's own message.
func TestURLsEndWithFuncdctlsRefusal(t *testing.T) {
	fakes(t, "{}")
	code, _, stderr := run("urls", "gone", "-n", "team")
	if code != 1 || stderr != "funcdctl rauthy: get the IdentityProvider team/gone: funcdctl: sdk: store.Get: identityprovider/gone not found\n" {
		t.Fatalf("exit %d, stderr %q", code, stderr)
	}
}

func TestRandomIsAlphanumericOfItsLength(t *testing.T) {
	t.Parallel()
	s, err := random(48)
	if err != nil {
		t.Fatal(err)
	}
	if len(s) != 48 || strings.Trim(s, alnum) != "" {
		t.Fatalf("random(48) = %q", s)
	}
}

// The settings Function's API key is rauthy's bootstrap JSON, base64, named settings.
func TestSettingsKeyIsRauthysBootstrapJSON(t *testing.T) {
	t.Parallel()
	enc, err := settingsKey()
	if err != nil {
		t.Fatal(err)
	}
	raw, err := base64.StdEncoding.DecodeString(enc)
	if err != nil {
		t.Fatal(err)
	}
	var def struct {
		Name   string `json:"name"`
		Access []struct {
			Group  string   `json:"group"`
			Rights []string `json:"access_rights"`
		} `json:"access"`
	}
	if err := json.Unmarshal(raw, &def); err != nil || def.Name != "settings" || len(def.Access) == 0 {
		t.Fatalf("%s: %v", raw, err)
	}
}

// The plugin page's command reference is the one the plugin's commands generate: `just plugin-docs` writes it.
func TestReference(t *testing.T) {
	plugintest.CheckReference(t, plugin(), filepath.Join("..", "..", "docs", "plugin.md"))
}
