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

// The funcd kinds an add-on's examples use beside the kinds the Addons register.
var funcdKinds = []string{"Addon", "Secret", "Route", "OIDCProvider", "Function", "Namespace", "BackupSchedule", "Backup", "Restore", "Bucket"}

// TestManifests decodes every add-on's YAML: each document is block style, an Addon pins its image by digest and
// names its kind, and every other document is of a funcd kind or of a kind an Addon here registers.
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
		if d.IsDir() || filepath.Ext(path) != ".yaml" || strings.HasPrefix(path, "e2e/") {
			return nil
		}
		f, err := os.Open(path)
		if err != nil {
			return err
		}
		defer f.Close()
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
		if flow := flowNode(d.node); flow != nil {
			t.Errorf("%s:%d: flow style; write it in block style", d.file, flow.Line)
		}
		var m struct {
			Kind string `yaml:"kind"`
			Spec struct {
				Kind  string `yaml:"kind"`
				Image string `yaml:"image"`
			} `yaml:"spec"`
		}
		if err := d.node.Decode(&m); err != nil {
			t.Errorf("%s: %v", d.file, err)
			continue
		}
		if m.Kind == "Addon" {
			if !digest.MatchString(m.Spec.Image) {
				t.Errorf("%s: the Addon's image %q is not pinned by digest", d.file, m.Spec.Image)
			}
			if m.Spec.Kind == "" {
				t.Errorf("%s: the Addon names no kind", d.file)
			}
			kinds[m.Spec.Kind] = true
		}
	}
	for _, d := range docs {
		var m struct {
			Kind string `yaml:"kind"`
		}
		_ = d.node.Decode(&m)
		if !kinds[m.Kind] && !slices.Contains(funcdKinds, m.Kind) {
			t.Errorf("%s: kind %q is neither funcd's nor one an Addon here registers", d.file, m.Kind)
		}
	}
	if len(kinds) == 0 {
		t.Fatal("no Addon found")
	}
}

// flowNode is the first flow-style mapping or sequence under n that holds something; {} and [] are allowed.
func flowNode(n *yaml.Node) *yaml.Node {
	if (n.Kind == yaml.MappingNode || n.Kind == yaml.SequenceNode) && n.Style&yaml.FlowStyle != 0 && len(n.Content) > 0 {
		return n
	}
	for _, c := range n.Content {
		if f := flowNode(c); f != nil {
			return f
		}
	}
	return nil
}
