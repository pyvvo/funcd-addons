package plugintest

import (
	"bytes"
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/pyvvo/funcd-addons/internal/pluginkit"
	"github.com/spf13/cobra/doc"
)

// updateEnv names the variable that has CheckReference write the reference instead of checking it, as `just
// plugin-docs` sets it.
const updateEnv = "UPDATE_REFERENCE"

// Reference is the plugin's command reference as cobra/doc writes it, one section per command. Each heading goes one
// level down, under the page's own sections, and no section lists SEE ALSO, since the whole reference is one page.
func Reference(p *pluginkit.Plugin) (string, error) {
	var sections []string
	for _, c := range p.Tree().Commands() {
		if !c.IsAvailableCommand() || c.IsAdditionalHelpTopicCommand() {
			continue
		}
		var b bytes.Buffer
		if err := doc.GenMarkdownCustom(c, &b, func(link string) string { return link }); err != nil {
			return "", err
		}
		sections = append(sections, strings.TrimSpace(nest(b.String())))
	}
	return strings.Join(sections, "\n\n") + "\n", nil
}

// text escapes the angle brackets of a help text, such as <ref>, which markdown would read as a tag.
var text = strings.NewReplacer("<", "&lt;", ">", "&gt;")

// nest puts each heading one level down, escapes the help texts and drops each SEE ALSO section, outside code blocks.
func nest(md string) string {
	var out []string
	code, seeAlso := false, false
	for _, line := range strings.Split(md, "\n") {
		switch {
		case strings.HasPrefix(line, "```"):
			code = !code
		case code:
		case strings.HasPrefix(line, "#"):
			seeAlso = strings.TrimLeft(line, "# ") == "SEE ALSO"
			line = "#" + line
		default:
			line = text.Replace(line)
		}
		if !seeAlso {
			out = append(out, line)
		}
	}
	return strings.Join(out, "\n")
}

// Markers are the lines between which a page carries the plugin's reference.
func Markers(p *pluginkit.Plugin) (begin, end string) {
	return "<!-- funcdctl " + p.Name + " reference: just plugin-docs -->", "<!-- end funcdctl " + p.Name + " reference -->"
}

// CheckReference fails the test when the reference between the plugin's Markers in the markdown page at path is not
// Reference. With UPDATE_REFERENCE set, as `just plugin-docs` runs it, it writes Reference there instead.
func CheckReference(t *testing.T, p *pluginkit.Plugin, path string) {
	t.Helper()
	stale, line, err := checkReference(p, path, os.Getenv(updateEnv) != "")
	if err != nil {
		t.Fatal(err)
	}
	if stale {
		t.Errorf("%s: the reference of funcdctl %s is stale, first at %q: run `just plugin-docs`", path, p.Name, line)
	}
}

// checkReference compares the reference at path with Reference, or writes it there when update is set. A stale
// reference comes with its first line that differs.
func checkReference(p *pluginkit.Plugin, path string, update bool) (stale bool, line string, err error) {
	ref, err := Reference(p)
	if err != nil {
		return false, "", err
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return false, "", err
	}
	text := string(raw)
	begin, end := Markers(p)
	i, j := strings.Index(text, begin), strings.Index(text, end)
	if i < 0 || j < i {
		return false, "", fmt.Errorf("%s has no reference of funcdctl %s: put the lines %q and %q where it goes, then run "+
			"`just plugin-docs`", path, p.Name, begin, end)
	}
	want := begin + "\n\n" + ref + "\n" + end
	got := text[i : j+len(end)]
	if got == want {
		return false, "", nil
	}
	if update {
		return false, "", os.WriteFile(path, []byte(text[:i]+want+text[j+len(end):]), 0o644)
	}
	return true, firstDifference(got, want), nil
}

// firstDifference is the first line of want that got does not have at the same place, or got's first extra line.
func firstDifference(got, want string) string {
	g, w := strings.Split(got, "\n"), strings.Split(want, "\n")
	for n, line := range w {
		if n >= len(g) || g[n] != line {
			return line
		}
	}
	return g[len(w)]
}
