package plugintest

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/pyvvo/funcd-addons/internal/pluginkit"
	"github.com/spf13/pflag"
)

type input struct {
	Stack     string
	Namespace string
}

func (input) Validate() error {
	return nil
}

func testPlugin(short string) *pluginkit.Plugin {
	return &pluginkit.Plugin{Name: "test", Short: "A test plugin", Commands: []pluginkit.Command{
		pluginkit.Spec[input]{
			Use:   "show <stack>",
			Short: short,
			Flags: func(fs *pflag.FlagSet, in *input) {
				pluginkit.Namespace(fs, &in.Namespace)
			},
			Args: func(in *input, args []string) { in.Stack = args[0] },
			Run:  func(*pluginkit.Env, input) error { return nil },
		},
		pluginkit.Spec[input]{
			Use:   "push",
			Short: "Push it",
			Run:   func(*pluginkit.Env, input) error { return nil },
		},
	}}
}

// The reference is cobra/doc's, a level down, in the plugin's order, with no SEE ALSO, help flag or generated-on line.
func TestReferenceIsCobrasMarkdownALevelDown(t *testing.T) {
	t.Parallel()
	got, err := Reference(testPlugin("Show a stack"))
	if err != nil {
		t.Fatal(err)
	}
	want := "### funcdctl test show\n\nShow a stack\n\n```\nfuncdctl test show <stack> [flags]\n```\n\n" +
		"#### Options\n\n```\n  -n, --namespace string   the namespace (default \"default\")\n```\n\n" +
		"### funcdctl test push\n\nPush it\n\n```\nfuncdctl test push\n```\n"
	if got != want {
		t.Errorf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestNestEscapesTextAndLeavesCodeAlone(t *testing.T) {
	t.Parallel()
	got := nest("## a\n\nprint <ref>\n\n```\n# not a heading <ref>\n```\n\n### SEE ALSO\n\n* [x](x.md)\n")
	if want := "### a\n\nprint &lt;ref&gt;\n\n```\n# not a heading <ref>\n```\n"; got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestCheckReferenceFindsAStaleOneAndWritesIt(t *testing.T) {
	t.Parallel()
	p := testPlugin("Show a stack")
	begin, end := Markers(p)
	page := filepath.Join(t.TempDir(), "plugin.md")
	if err := os.WriteFile(page, []byte("# funcdctl test\n\n"+begin+"\n"+end+"\n\nAfter.\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	stale, line, err := checkReference(p, page, false)
	if err != nil || !stale || line != "" && !strings.HasPrefix(line, "#") {
		t.Fatalf("stale %v, line %q, err %v", stale, line, err)
	}
	if _, _, err := checkReference(p, page, true); err != nil {
		t.Fatal(err)
	}
	if stale, _, err := checkReference(p, page, false); err != nil || stale {
		t.Fatalf("after the write: stale %v, err %v", stale, err)
	}
	raw, err := os.ReadFile(page)
	if err != nil {
		t.Fatal(err)
	}
	if text := string(raw); !strings.HasPrefix(text, "# funcdctl test\n\n"+begin+"\n\n### funcdctl test show\n") ||
		!strings.HasSuffix(text, "```\n\n"+end+"\n\nAfter.\n") {
		t.Errorf("the page:\n%s", text)
	}
	if stale, line, err := checkReference(testPlugin("Show one stack"), page, false); err != nil || !stale || line != "Show one stack" {
		t.Errorf("a changed short help: stale %v, line %q, err %v", stale, line, err)
	}
}

func TestCheckReferenceNeedsTheMarkers(t *testing.T) {
	t.Parallel()
	page := filepath.Join(t.TempDir(), "plugin.md")
	if err := os.WriteFile(page, []byte("# funcdctl test\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, _, err := checkReference(testPlugin("Show a stack"), page, true); err == nil || !strings.Contains(err.Error(), "has no reference") {
		t.Fatalf("got %v", err)
	}
}
