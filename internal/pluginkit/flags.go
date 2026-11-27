package pluginkit

import (
	"fmt"

	"github.com/spf13/pflag"
)

// Namespace binds the shared -n/--namespace flag to p, with funcdctl's default namespace.
func Namespace(fs *pflag.FlagSet, p *string) {
	fs.StringVarP(p, "namespace", "n", "default", "the namespace")
}

// Output binds the shared -o/--output flag to p: -o json prints JSON instead of the command's table.
func Output(fs *pflag.FlagSet, p *Format) {
	fs.VarP(p, "output", "o", "print JSON instead of a table")
}

// Format is the value of -o/--output: empty for the command's table, or json.
type Format string

// JSON reports whether -o json was given.
func (f Format) JSON() bool {
	return f == "json"
}

func (f *Format) String() string {
	return string(*f)
}

// Set refuses every format but json, the one both plugins print.
func (f *Format) Set(s string) error {
	if s != "json" {
		return fmt.Errorf("%q is no output format: give json", s)
	}
	*f = Format(s)
	return nil
}

func (f *Format) Type() string {
	return "json"
}
