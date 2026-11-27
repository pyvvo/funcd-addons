package addons_test

import (
	"errors"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"

	yaml "go.yaml.in/yaml/v3"
)

var digest = regexp.MustCompile(`@sha256:[a-f0-9]{64}$`)

// unpublished is the digest of an image that the release workflow has not published yet; a follow-up pins it. No funcd
// takes it, since an Addon's image must hold 64 hex digits.
var unpublished = regexp.MustCompile(`^ghcr\.io/pyvvo/[a-z0-9-]+@sha256:TODO-[a-z0-9-]+$`)

// The funcd kinds an add-on's examples use beside the kinds the Addons register.
var funcdKinds = []string{"Addon", "Secret", "Route", "OIDCProvider", "Function", "Namespace", "BackupSchedule", "Backup", "Restore", "Bucket", "Job", "ConfigMap", "EgressPolicy"}

// function is the Function of a kind: an image, by its digest, or one of the namespace (funcd ADR-0358).
type function struct {
	Image string `yaml:"image"`
	Ref   string `yaml:"ref"`
}

func (f function) check(t *testing.T, file, kind string) {
	t.Helper()
	if f.Image != "" && !digest.MatchString(f.Image) {
		t.Errorf("%s: the image %q of the kind %s is not pinned by digest", file, f.Image, kind)
	}
}

// TestManifests decodes every add-on's YAML: an Addon pins each runtime's image and each kind's by digest, but for an
// Engineless entry, which runs none, and names the kind its Engine or Engineless entry runs (funcd ADR-0344,
// ADR-0345), and every other document is of a funcd kind or of a kind an Addon here registers. `just lint` checks
// that every YAML file is block style.
func TestManifests(t *testing.T) {
	t.Parallel()
	type doc struct {
		file string
		node *yaml.Node
	}
	var docs []doc
	err := filepath.WalkDir(".", func(path string, d fs.DirEntry, err error) error {
		// work/ is the e2e's untracked output, which holds manifests it refuses on purpose.
		if err != nil || d.IsDir() && (strings.HasPrefix(d.Name(), ".") && path != "." || path == "work") {
			if d != nil && d.IsDir() {
				return filepath.SkipDir
			}
			return err
		}
		// A function's contract file is no manifest.
		if d.IsDir() || filepath.Ext(path) != ".yaml" || strings.HasPrefix(path, "e2e/") || strings.HasSuffix(path, ".contract.yaml") {
			return nil
		}
		f, err := os.Open(path)
		if err != nil {
			return err
		}
		defer func() {
			_ = f.Close()
		}()
		dec := yaml.NewDecoder(f)
		for {
			var n yaml.Node
			if err := dec.Decode(&n); errors.Is(err, io.EOF) {
				return nil
			} else if err != nil {
				return err
			}
			docs = append(docs, doc{path, &n})
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	kinds := map[string]bool{}
	for _, d := range docs {
		var m struct {
			Kind string `yaml:"kind"`
			Spec struct {
				Runtimes []struct {
					Name       string   `yaml:"name"`
					Image      string   `yaml:"image"`
					Workloads  []string `yaml:"workloads"`
					Attributes struct {
						Kind     string   `yaml:"kind"`
						Function function `yaml:"function"`
						Settings []struct {
							Kind     string   `yaml:"kind"`
							Function function `yaml:"function"`
						} `yaml:"settings"`
					} `yaml:"attributes"`
				} `yaml:"runtimes"`
			} `yaml:"spec"`
		}
		if err := d.node.Decode(&m); err != nil {
			t.Errorf("%s: %v", d.file, err)
			continue
		}
		if m.Kind == "Addon" {
			named := false
			for _, rt := range m.Spec.Runtimes {
				engineless := slices.Contains(rt.Workloads, "Engineless")
				switch {
				case engineless && rt.Image != "":
					t.Errorf("%s: the Engineless runtime %q has an image", d.file, rt.Name)
				case engineless:
				case unpublished.MatchString(rt.Image):
					t.Logf("%s: the image of the runtime %q is not published yet: %s", d.file, rt.Name, rt.Image)
				case !digest.MatchString(rt.Image):
					t.Errorf("%s: the image %q of the runtime %q is not pinned by digest", d.file, rt.Image, rt.Name)
				}
				if !engineless && !slices.Contains(rt.Workloads, "Engine") {
					continue
				}
				named = rt.Attributes.Kind != ""
				kinds[rt.Attributes.Kind] = true
				rt.Attributes.Function.check(t, d.file, rt.Attributes.Kind)
				for _, st := range rt.Attributes.Settings {
					kinds[st.Kind] = true
					st.Function.check(t, d.file, st.Kind)
				}
			}
			if !named {
				t.Errorf("%s: no runtime of the Addon runs a kind", d.file)
			}
		}
	}
	for _, d := range docs {
		var m struct {
			Kind string `yaml:"kind"`
		}
		_ = d.node.Decode(&m)
		// scripts/ holds the README template, which is no manifest of a kind either.
		if templatePart(d.file) || strings.HasPrefix(filepath.ToSlash(d.file), "scripts/") {
			continue
		}
		if !kinds[m.Kind] && !slices.Contains(funcdKinds, m.Kind) {
			t.Errorf("%s: kind %q is neither funcd's nor one an Addon here registers", d.file, m.Kind)
		}
	}
	if len(kinds) == 0 {
		t.Fatal("no Addon found")
	}
}

// templatePart reports a file of an add-on's App template (funcd ADR-0337) that holds no manifest of a kind: its
// app.yaml and its resources, parts of the App that funcdctl app deploy renders.
func templatePart(file string) bool {
	parts := strings.Split(filepath.ToSlash(file), "/")
	return len(parts) >= 3 && parts[1] == "app" && (len(parts) == 3 && parts[2] == "app.yaml" || parts[2] == "resources")
}
