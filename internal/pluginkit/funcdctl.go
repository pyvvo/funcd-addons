package pluginkit

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
)

// Funcdctl runs the funcdctl that runs the plugin, the only way a plugin reaches funcd: $FUNCDCTL, which funcdctl sets
// for its plugins (funcd ADR-0319 Decision 4), else the funcdctl on PATH.
type Funcdctl struct {
	Path string
	// The plugin's terminal, which Interactive hands to funcdctl.
	in  io.Reader
	out io.Writer
	err io.Writer
}

func funcdctlPath() string {
	if p := os.Getenv("FUNCDCTL"); p != "" {
		return p
	}
	return "funcdctl"
}

// Run runs funcdctl with args and returns what it printed. When funcdctl refuses, the error is funcdctl's own message.
func (f *Funcdctl) Run(args ...string) (string, error) {
	cmd := exec.Command(f.Path, args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		said := strings.TrimSpace(stderr.String())
		if said == "" {
			said = strings.TrimSpace(stdout.String())
		}
		if said == "" {
			said = "funcdctl " + verb(args) + " failed"
		}
		return "", errors.New(said)
	}
	if err != nil {
		return "", err
	}
	return stdout.String(), nil
}

// JSON runs funcdctl with args and decodes what it printed into v.
func (f *Funcdctl) JSON(v any, args ...string) error {
	out, err := f.Run(args...)
	if err != nil {
		return err
	}
	if err := json.Unmarshal([]byte(out), v); err != nil {
		return fmt.Errorf("read what funcdctl %s printed: %w", verb(args), err)
	}
	return nil
}

// Get decodes one object into v: funcdctl get <kind> <name> -n <namespace> -o json.
func (f *Funcdctl) Get(v any, kind, name, namespace string) error {
	return f.JSON(v, "get", kind, name, "-n", namespace, "-o", "json")
}

// Apply applies manifest with funcdctl apply -f -. What funcdctl prints goes to the plugin's stderr, so the plugin's
// stdout keeps only its own output.
func (f *Funcdctl) Apply(manifest []byte) error {
	cmd := exec.Command(f.Path, "apply", "-f", "-")
	cmd.Stdin, cmd.Stdout, cmd.Stderr = bytes.NewReader(manifest), f.err, f.err
	return cmd.Run()
}

// Interactive runs funcdctl on the plugin's own terminal, so that funcdctl can ask a person, as funcdctl job approve
// asks [y/N]. funcdctl prints its own messages, so a failure is only its exit code, which the plugin exits with.
func (f *Funcdctl) Interactive(args ...string) error {
	cmd := exec.Command(f.Path, args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = f.in, f.out, f.err
	err := cmd.Run()
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		// A funcdctl that a signal ended has no code of its own.
		return ExitCode(max(exit.ExitCode(), 1))
	}
	return err
}

// verb is the funcdctl command that args run, such as get.
func verb(args []string) string {
	if len(args) == 0 {
		return ""
	}
	return args[0]
}
