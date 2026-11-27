package main

import (
	"archive/tar"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// image is the opentofu1 image of the Addon that the plugin ships beside (funcd ADR-0355 Decision 1). A release
// build stamps it from infrastack/app/addon.yaml with -X main.image (stamp.go), and a test keeps it equal to the
// Addon's.
var image = "ghcr.io/pyvvo/funcd-opentofu@sha256:7840e234607a768a5a7f4276c929f9688fa0d3c9002552d27eac284b293ac0a1"

const (
	imageEnv = "INFRASTACK_OPENTOFU_IMAGE"
	// The image's interpreter runs OpenTofu's commands in turn on the copy at workdir, and prints their outputs as
	// JSON.
	python  = "/usr/local/bin/python3.14"
	workdir = "/m"
	driver  = `import json, subprocess, sys
outs = []
for args in json.loads(sys.argv[1]):
    run = subprocess.run(["/opt/tofu/bin/tofu", *args], capture_output=True, text=True)
    if run.returncode != 0:
        sys.stderr.write("tofu " + " ".join(args) + ":\n" + (run.stderr or run.stdout))
        sys.exit(run.returncode)
    outs.append(run.stdout)
print(json.dumps(outs))
`
)

var engines = []string{"docker", "nerdctl"}

// engine is a container engine that runs OpenTofu in the add-on's runtime image.
type engine struct {
	binary string
	image  string
}

// newEngine is the first of docker and nerdctl on PATH, with the image that INFRASTACK_OPENTOFU_IMAGE names, as the
// add-on's CI and lanes set for one not yet released, else the Addon's.
func newEngine() (*engine, error) {
	for _, name := range engines {
		if binary, err := exec.LookPath(name); err == nil {
			e := &engine{binary: binary, image: os.Getenv(imageEnv)}
			if e.image == "" {
				e.image = image
			}
			return e, nil
		}
	}
	return nil, errors.New("neither docker nor nerdctl is on PATH: the push runs OpenTofu in the add-on's image")
}

// do runs the engine with args, stdin and, when it is not nil, stdout, and returns what it printed otherwise.
func (e *engine) do(stdin io.Reader, stdout io.Writer, args ...string) (string, error) {
	cmd := exec.Command(e.binary, args...)
	var out, said bytes.Buffer
	cmd.Stdin, cmd.Stdout, cmd.Stderr = stdin, &out, &said
	if stdout != nil {
		cmd.Stdout = stdout
	}
	err := cmd.Run()
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		if said.Len() == 0 {
			said = out
		}
		return "", fmt.Errorf("%s %s failed: %s", filepath.Base(e.binary), args[0],
			strings.TrimSpace(strings.ToValidUTF8(said.String(), "�")))
	}
	return out.String(), err
}

// tofu runs OpenTofu's commands in turn in a container of the image, on a copy of folder, and returns what each
// printed. With fetch, it copies that path of the container's copy out to into.
func (e *engine) tofu(folder string, commands [][]string, fetch, into string) ([]string, error) {
	list, err := json.Marshal(commands)
	if err != nil {
		return nil, err
	}
	out, err := e.do(nil, nil, "create", "--entrypoint", python, "-w", workdir, "-e", "TF_IN_AUTOMATION=1",
		"-e", "CHECKPOINT_DISABLE=1", "-e", "HOME=/tmp", e.image, "-c", driver, string(list))
	if err != nil {
		return nil, err
	}
	cid := strings.TrimSpace(out)
	defer func() {
		_ = exec.Command(e.binary, "rm", "-f", cid).Run()
	}()
	if err := e.copyIn(cid, folder); err != nil {
		return nil, err
	}
	start := exec.Command(e.binary, "start", "-a", cid)
	var stdout, stderr bytes.Buffer
	start.Stdout, start.Stderr = &stdout, &stderr
	if err := start.Run(); err != nil {
		var exit *exec.ExitError
		if !errors.As(err, &exit) {
			return nil, err
		}
		if stderr.Len() == 0 {
			stderr = stdout
		}
		return nil, errors.New(strings.TrimSpace(stderr.String()))
	}
	if fetch != "" {
		if err := e.copyOut(cid, fetch, into); err != nil {
			return nil, err
		}
	}
	var outs []string
	if err := json.Unmarshal(stdout.Bytes(), &outs); err != nil {
		return nil, fmt.Errorf("read what OpenTofu printed: %w", err)
	}
	return outs, nil
}

// copyIn copies folder to the container's workdir as a tar on the engine's standard input: a nerdctl in a VM, as
// colima's, sees no path of this host.
func (e *engine) copyIn(cid, folder string) error {
	f, err := os.CreateTemp("", "funcdctl-infra-*.tar")
	if err != nil {
		return err
	}
	defer func() {
		_ = f.Close()
		_ = os.Remove(f.Name())
	}()
	tw := tar.NewWriter(f)
	if err := addTree(tw, folder, strings.TrimPrefix(workdir, "/")); err != nil {
		return err
	}
	if err := tw.Close(); err != nil {
		return err
	}
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		return err
	}
	_, err = e.do(f, nil, "cp", "-", cid+":/")
	return err
}

// addTree adds path to the tar as name, a folder with its entries in name order, each owned by root.
func addTree(tw *tar.Writer, path, name string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	link := ""
	if info.Mode()&fs.ModeSymlink != 0 {
		if link, err = os.Readlink(path); err != nil {
			return err
		}
	}
	h, err := tar.FileInfoHeader(info, link)
	if err != nil {
		return err
	}
	h.Name = name
	if info.IsDir() {
		h.Name += "/"
	}
	h.Uid, h.Gid, h.Uname, h.Gname = 0, 0, "", ""
	if err := tw.WriteHeader(h); err != nil {
		return err
	}
	switch {
	case info.Mode().IsRegular():
		return copyInto(tw, path)
	case info.IsDir():
		entries, err := os.ReadDir(path)
		if err != nil {
			return err
		}
		for _, entry := range entries {
			if err := addTree(tw, filepath.Join(path, entry.Name()), name+"/"+entry.Name()); err != nil {
				return err
			}
		}
	}
	return nil
}

func copyInto(w io.Writer, path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer func() {
		_ = f.Close()
	}()
	_, err = io.Copy(w, f)
	return err
}

// copyOut copies path of the container's workdir to into, from the tar the engine writes on its standard output.
func (e *engine) copyOut(cid, path, into string) error {
	f, err := os.CreateTemp("", "funcdctl-infra-*.tar")
	if err != nil {
		return err
	}
	defer func() {
		_ = f.Close()
		_ = os.Remove(f.Name())
	}()
	if _, err := e.do(nil, f, "cp", cid+":"+workdir+"/"+path, "-"); err != nil {
		return err
	}
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		return err
	}
	return unpack(tar.NewReader(f), into)
}

// unpack writes the one file or folder that the tar holds to into: plain files and folders only, each inside into.
func unpack(tr *tar.Reader, into string) error {
	for {
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
		parts := pathParts(h.Name)
		if strings.HasPrefix(h.Name, "/") || containsDotDot(parts) || !(isFolder(h) || isFile(h)) {
			return fmt.Errorf("the container's %s is no plain file or folder of what was asked", h.Name)
		}
		at := filepath.Join(append([]string{into}, parts[min(1, len(parts)):]...)...)
		if isFolder(h) {
			if err := os.MkdirAll(at, 0o777); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(at), 0o777); err != nil {
			return err
		}
		if err := writeFrom(tr, at); err != nil {
			return err
		}
		if err := os.Chmod(at, fs.FileMode(h.Mode)&fs.ModePerm); err != nil {
			return err
		}
	}
}

// pathParts are the names of a tar member's path, as a POSIX path reads them: an empty name or . is none.
func pathParts(name string) []string {
	var parts []string
	for _, p := range strings.Split(name, "/") {
		if p != "" && p != "." {
			parts = append(parts, p)
		}
	}
	return parts
}

func containsDotDot(parts []string) bool {
	for _, p := range parts {
		if p == ".." {
			return true
		}
	}
	return false
}

func isFolder(h *tar.Header) bool {
	return h.Typeflag == tar.TypeDir
}

func isFile(h *tar.Header) bool {
	switch h.Typeflag {
	case tar.TypeReg, tar.TypeCont, tar.TypeGNUSparse:
		return true
	}
	return false
}

func writeFrom(r io.Reader, path string) error {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o666)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, r); err != nil {
		_ = f.Close()
		return err
	}
	return f.Close()
}
