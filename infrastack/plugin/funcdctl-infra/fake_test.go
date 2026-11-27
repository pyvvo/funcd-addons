package main

// The fakes that the tests run in place of the container engine and funcdctl, as funcd ADR-0355's Implementation plan
// step 1 has them: each records its arguments. The engine stands in for OpenTofu in the add-on's image: it answers
// `tofu show -json -module` from the test's configurations, writes a lock file, and mirrors one provider package per
// platform. It copies only by a tar on its standard input or output, as a nerdctl in a VM must, which sees no path of
// this host.

import (
	"archive/tar"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/pyvvo/funcd-addons/internal/pluginkit/plugintest"
)

func TestMain(m *testing.M) {
	plugintest.Main(m, map[string]plugintest.Fake{
		"docker":   fakeEngine,
		"nerdctl":  fakeEngine,
		"funcdctl": fakeFuncdctl,
	})
}

// fakeEngine keeps its containers under FAKE_ENGINE: create makes one that holds the commands it was given, cp copies
// a tar in or out of it, start runs the commands, and rm removes it.
func fakeEngine(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	state := os.Getenv("FAKE_ENGINE")
	if err := appendJSON(filepath.Join(state, "calls.jsonl"), args); err != nil {
		return fail(stderr, err)
	}
	root := func(cid string) string { return filepath.Join(state, "containers", cid) }
	local := func(spec string) string {
		cid, path, _ := strings.Cut(spec, ":")
		return filepath.Join(root(cid), strings.TrimLeft(path, "/"))
	}
	switch args[0] {
	case "create":
		cid := make([]byte, 6)
		_, _ = rand.Read(cid)
		id := hex.EncodeToString(cid)
		if !json.Valid([]byte(args[len(args)-1])) {
			return fail(stderr, fmt.Errorf("the commands %q are no JSON", args[len(args)-1]))
		}
		if err := os.MkdirAll(root(id), 0o755); err != nil {
			return fail(stderr, err)
		}
		if err := os.WriteFile(filepath.Join(root(id), "commands.json"), []byte(args[len(args)-1]), 0o644); err != nil {
			return fail(stderr, err)
		}
		fmt.Fprintln(stdout, id)
	case "cp":
		src, dst := args[1], args[2]
		_, err := os.Lstat(local(src))
		switch {
		case src == "-":
			if err := extract(tar.NewReader(stdin), local(dst)); err != nil {
				return fail(stderr, err)
			}
		case dst == "-" && err == nil:
			if err := writeTar(stdout, local(src), filepath.Base(src)); err != nil {
				return fail(stderr, err)
			}
		default:
			fmt.Fprint(stderr, "unable to copy: source does not exist")
			return 1
		}
	case "start":
		cid := args[len(args)-1]
		outs, err := runCommands(state, root(cid))
		if err != nil {
			return fail(stderr, err)
		}
		if err := json.NewEncoder(stdout).Encode(outs); err != nil {
			return fail(stderr, err)
		}
	case "rm":
		_ = os.RemoveAll(root(args[len(args)-1]))
	}
	return 0
}

// extract writes the tar's members under into, each owned by root, as the plugin must hand them.
func extract(tr *tar.Reader, into string) error {
	for {
		h, err := tr.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if h.Uid != 0 || h.Gid != 0 {
			return fmt.Errorf("%s is not root's", h.Name)
		}
		at := filepath.Join(into, h.Name)
		switch h.Typeflag {
		case tar.TypeDir:
			err = os.MkdirAll(at, 0o755)
		case tar.TypeSymlink:
			err = os.Symlink(h.Linkname, at)
		default:
			var body []byte
			if body, err = io.ReadAll(tr); err == nil {
				err = os.WriteFile(at, body, 0o644)
			}
		}
		if err != nil {
			return err
		}
	}
}

// writeTar writes path to w as a tar whose members start with name, as an engine's cp to - does.
func writeTar(w io.Writer, path, name string) error {
	tw := tar.NewWriter(w)
	err := filepath.WalkDir(path, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		h, err := tar.FileInfoHeader(info, "")
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(path, p)
		if err != nil {
			return err
		}
		h.Name = filepath.ToSlash(filepath.Join(name, rel))
		if d.IsDir() {
			h.Name += "/"
		}
		if err := tw.WriteHeader(h); err != nil {
			return err
		}
		if d.IsDir() {
			return nil
		}
		body, err := os.ReadFile(p)
		if err == nil {
			_, err = tw.Write(body)
		}
		return err
	})
	if err != nil {
		return err
	}
	return tw.Close()
}

// runCommands runs a container's commands on its copy of the module: show answers from the test's shows.json, a lock
// writes the lock file, and a mirror writes one provider package for its platform.
func runCommands(state, root string) ([]string, error) {
	work := filepath.Join(root, "m")
	var shows map[string]json.RawMessage
	if err := readJSON(filepath.Join(state, "shows.json"), &shows); err != nil {
		return nil, err
	}
	var commands [][]string
	if err := readJSON(filepath.Join(root, "commands.json"), &commands); err != nil {
		return nil, err
	}
	outs := []string{}
	for _, cmd := range commands {
		switch {
		case cmd[0] == "show":
			_, at, _ := strings.Cut(cmd[len(cmd)-1], "=")
			if info, err := os.Stat(filepath.Join(work, at)); err != nil || !info.IsDir() {
				return nil, fmt.Errorf("tofu show: no folder %s", at)
			}
			outs = append(outs, string(shows[at]))
		case len(cmd) > 1 && cmd[0] == "providers" && cmd[1] == "lock":
			var platforms []string
			for _, a := range cmd {
				if p, ok := strings.CutPrefix(a, "-platform="); ok {
					platforms = append(platforms, p)
				}
			}
			slices.Sort(platforms)
			lock := "# hashes for " + strings.Join(platforms, " ") + "\n"
			if err := os.WriteFile(filepath.Join(work, lockFile), []byte(lock), 0o644); err != nil {
				return nil, err
			}
			outs = append(outs, "")
		case len(cmd) > 1 && cmd[0] == "providers" && cmd[1] == "mirror":
			platform := ""
			for _, a := range cmd {
				if p, ok := strings.CutPrefix(a, "-platform="); ok && platform == "" {
					platform = p
				}
			}
			folder := filepath.Join(work, cmd[len(cmd)-1], "registry.opentofu.org", "hashicorp", "random")
			if err := os.MkdirAll(folder, 0o755); err != nil {
				return nil, err
			}
			zip := filepath.Join(folder, "terraform-provider-random_3.6.3_"+platform+".zip")
			if err := os.WriteFile(zip, []byte(platform), 0o644); err != nil {
				return nil, err
			}
			if err := os.WriteFile(filepath.Join(folder, "index.json"), []byte("{}"), 0o644); err != nil {
				return nil, err
			}
			outs = append(outs, "")
		default:
			outs = append(outs, "")
		}
	}
	return outs, nil
}

// funcdCall is what the fake funcdctl records of a call: its arguments and, for a push, the bundle's files and its
// contract.
type funcdCall struct {
	Args     []string `json:"args"`
	Files    []string `json:"files,omitempty"`
	Contract string   `json:"contract,omitempty"`
}

// fakeFuncdctl keeps its state under FAKE_FUNCDCTL: push and index print <ref>@sha256:<digest of their arguments>,
// get answers from objects.json by <kind>/<name>, and job runs prints runs.json.
func fakeFuncdctl(args []string, _ io.Reader, stdout, stderr io.Writer) int {
	state := os.Getenv("FAKE_FUNCDCTL")
	seen := funcdCall{Args: args}
	if args[0] == "push" {
		build := args[1]
		err := filepath.WalkDir(build, func(path string, d fs.DirEntry, err error) error {
			if err != nil || d.IsDir() {
				return err
			}
			rel, err := filepath.Rel(build, path)
			seen.Files = append(seen.Files, filepath.ToSlash(rel))
			return err
		})
		if err != nil {
			return fail(stderr, err)
		}
		slices.Sort(seen.Files)
		contract, err := os.ReadFile(filepath.Join(build, filepath.Base(build)+".contract.yaml"))
		if err != nil {
			return fail(stderr, err)
		}
		seen.Contract = string(contract)
	}
	if err := appendJSON(filepath.Join(state, "calls.jsonl"), seen); err != nil {
		return fail(stderr, err)
	}
	switch {
	case args[0] == "push" || args[0] == "index":
		ref := args[1]
		if args[0] == "push" {
			ref = args[2]
		}
		sum := sha256.Sum256([]byte(strings.Join(args, " ")))
		fmt.Fprintln(stdout, ref+"@sha256:"+hex.EncodeToString(sum[:]))
	case args[0] == "get":
		var objects map[string]json.RawMessage
		if err := readJSON(filepath.Join(state, "objects.json"), &objects); err != nil {
			return fail(stderr, err)
		}
		key := args[1] + "/" + args[2]
		object, ok := objects[key]
		if !ok {
			fmt.Fprintf(stderr, "funcdctl: sdk: store.Get: %s not found\n", key)
			return 1
		}
		fmt.Fprintln(stdout, string(object))
	case len(args) > 1 && args[0] == "job" && args[1] == "runs":
		runs, err := os.ReadFile(filepath.Join(state, "runs.json"))
		if err != nil {
			return fail(stderr, err)
		}
		fmt.Fprintln(stdout, string(runs))
	}
	return 0
}

func fail(stderr io.Writer, err error) int {
	fmt.Fprintln(stderr, err)
	return 1
}

func appendJSON(path string, v any) error {
	line, err := json.Marshal(v)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	if _, err := f.Write(append(line, '\n')); err != nil {
		_ = f.Close()
		return err
	}
	return f.Close()
}

func readJSON(path string, v any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(raw, v)
}
