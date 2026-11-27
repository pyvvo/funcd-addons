// Package plugintest tests a plugin built on pluginkit. It stands in for the programs a plugin runs, such as funcdctl
// and docker, with fakes written in Go, and it checks the command reference that the plugin's README carries. Only
// tests import it, so a plugin's binary carries none of it.
package plugintest

import (
	"io"
	"os"
	"path/filepath"
	"testing"
)

// Fake stands in for a program: it gets the program's arguments, standard input and output, and returns its exit
// code.
type Fake func(args []string, stdin io.Reader, stdout, stderr io.Writer) int

// Main runs the test binary as the Fake its name says, when a plugin ran it under a name that Install linked, and the
// tests otherwise. A plugin's TestMain calls it.
func Main(m *testing.M, fakes map[string]Fake) {
	if fake, ok := fakes[filepath.Base(os.Args[0])]; ok {
		os.Exit(fake(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
	}
	os.Exit(m.Run())
}

// Install links the test binary into a new folder under each name, puts the folder first on the test's PATH, and
// returns it: a program that the plugin runs by one of those names runs its Fake.
func Install(t *testing.T, names ...string) string {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	for _, name := range names {
		if err := os.Symlink(exe, filepath.Join(dir, name)); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	return dir
}
