// Package plugintest tests a plugin built on pluginkit. It stands in for the programs a plugin runs, such as funcdctl
// and docker, with fakes written in Go, and it checks the command reference that the plugin's README carries. Only
// tests import it, so a plugin's binary carries none of it.
package plugintest

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
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
	code := m.Run()
	if built.dir != "" {
		_ = os.RemoveAll(built.dir)
	}
	os.Exit(code)
}

// built is the copy of the tests that runs the fakes under the race detector, made once per test binary.
var built struct {
	once sync.Once
	dir  string
	exe  string
	err  error
}

// fakeBinary is the binary that runs the fakes: the test binary itself, or, under the race detector, a copy built
// without it, since a binary built with it takes about a second to start, and a plugin's tests run their fakes many
// times.
func fakeBinary() (string, error) {
	if !raceEnabled {
		return os.Executable()
	}
	built.once.Do(func() {
		if built.dir, built.err = os.MkdirTemp("", "plugintest-"); built.err != nil {
			return
		}
		built.exe = filepath.Join(built.dir, "fakes.test")
		// go test runs a package's tests in its folder, so "." is the package under test.
		if out, err := exec.Command("go", "test", "-c", "-o", built.exe, ".").CombinedOutput(); err != nil {
			built.err = fmt.Errorf("build the fakes without the race detector: %w: %s", err, out)
		}
	})
	return built.exe, built.err
}

// Install links the binary that runs the fakes into a new folder under each name, puts the folder first on the test's
// PATH, and returns it: a program that the plugin runs by one of those names runs its Fake.
func Install(t *testing.T, names ...string) string {
	t.Helper()
	exe, err := fakeBinary()
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
