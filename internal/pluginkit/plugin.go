// Package pluginkit is the framework of this repository's funcdctl plugins (funcd ADR-0319), on cobra, the library
// funcd's own funcdctl uses. A plugin's main registers its commands in a Plugin. Each command is a Spec, which
// declares in one place its use line, its short help, its flags with their defaults and the flags it requires. Its
// input is a typed struct that checks itself, its service does the work against Funcdctl, and act turns every error
// into `funcdctl <plugin>: <message>` and exit 1.
package pluginkit

import (
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

// A plugin's help and its generated reference list its commands in the order its Plugin declares them, the order a
// user takes them in, not by name.
func init() {
	cobra.EnableCommandSorting = false
}

// Plugin is one funcdctl plugin: funcdctl runs `funcdctl-<Name>` from PATH as `funcdctl <Name>` (funcd ADR-0319).
type Plugin struct {
	Name     string
	Short    string
	Commands []Command
}

// Command is a command a Plugin registers: a Spec.
type Command interface {
	command(env *Env) *cobra.Command
}

// Input is a command's typed input. Validate checks it once its flags and arguments are in it, before its service
// runs.
type Input interface {
	Validate() error
}

// Spec declares one command whose input is I.
type Spec[I Input] struct {
	// Use is the command's name, then each of its arguments as <name>, such as "push <module> <ref>".
	Use   string
	Short string
	// Flags binds each flag to a field of the input, with its default.
	Flags func(fs *pflag.FlagSet, in *I)
	// Required names the flags that the command does not run without.
	Required []string
	// Args puts the arguments into the input, once their number is the one Use names.
	Args func(in *I, args []string)
	// Run is the command's service.
	Run func(env *Env, in I) error
}

// Env is what a service works with: the plugin's output, and funcdctl.
type Env struct {
	Out      io.Writer
	Err      io.Writer
	Funcdctl *Funcdctl
}

// ExitCode ends the plugin with that code and no message of its own, as when the funcdctl that Interactive ran printed
// its own.
type ExitCode int

func (c ExitCode) Error() string {
	return fmt.Sprintf("exit status %d", int(c))
}

// Main runs the plugin on the process's arguments and terminal, then exits with its code.
func Main(p *Plugin) {
	os.Exit(p.Run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
}

// Run runs the plugin on args, with stdin, stdout and stderr as its terminal, and returns its exit code.
func (p *Plugin) Run(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	ctl := &Funcdctl{Path: funcdctlPath(), in: stdin, out: stdout, err: stderr}
	root := p.tree(&Env{Out: stdout, Err: stderr, Funcdctl: ctl})
	root.SetArgs(args)
	root.SetIn(stdin)
	root.SetOut(stdout)
	root.SetErr(stderr)
	// A plugin named with no command prints its help where errors go, and fails, as a call that names no command is
	// one that cannot run.
	if len(args) == 0 {
		root.SetOut(stderr)
		_ = root.Help()
		return 1
	}
	return act(p.Name, root.Execute(), stderr)
}

// Tree is the plugin's command tree, from which plugintest writes its reference.
func (p *Plugin) Tree() *cobra.Command {
	return p.tree(&Env{})
}

func (p *Plugin) tree(env *Env) *cobra.Command {
	root := &cobra.Command{
		Use:   p.Name,
		Short: p.Short,
		// The usage lines read `funcdctl infra push`, the command a user types, rather than the executable's name.
		Annotations:       map[string]string{cobra.CommandDisplayNameAnnotation: "funcdctl " + p.Name},
		SilenceErrors:     true,
		SilenceUsage:      true,
		DisableAutoGenTag: true,
	}
	root.CompletionOptions.DisableDefaultCmd = true
	// --help works on every command, and stays out of each command's flags, so that the reference lists only its own.
	root.PersistentFlags().BoolP("help", "h", false, "help for a command")
	_ = root.PersistentFlags().MarkHidden("help")
	for _, c := range p.Commands {
		root.AddCommand(c.command(env))
	}
	return root
}

// act is the one error wrapper of every command: no error is exit 0, an ExitCode is its code, and any other error is
// printed as `funcdctl <plugin>: <message>`, then exit 1.
func act(name string, err error, stderr io.Writer) int {
	if err == nil {
		return 0
	}
	var code ExitCode
	if errors.As(err, &code) {
		return int(code)
	}
	fmt.Fprintf(stderr, "funcdctl %s: %v\n", name, err)
	return 1
}

func (s Spec[I]) command(env *Env) *cobra.Command {
	var in I
	args := argNames(s.Use)
	cmd := &cobra.Command{
		Use:               s.Use,
		Short:             s.Short,
		DisableAutoGenTag: true,
		Args: func(cmd *cobra.Command, got []string) error {
			if len(got) != len(args) {
				return fmt.Errorf("usage: %s", cmd.UseLine())
			}
			return nil
		},
		RunE: func(cmd *cobra.Command, got []string) error {
			// The kit checks the required flags itself, so that the message names the flag as a user types it.
			for _, name := range s.Required {
				if !cmd.Flags().Changed(name) {
					return fmt.Errorf("--%s is required", name)
				}
			}
			if s.Args != nil {
				s.Args(&in, got)
			}
			if err := in.Validate(); err != nil {
				return err
			}
			return s.Run(env, in)
		},
	}
	if s.Flags != nil {
		s.Flags(cmd.Flags(), &in)
	}
	for _, name := range s.Required {
		f := cmd.Flags().Lookup(name)
		if f == nil {
			panic(fmt.Sprintf("pluginkit: %s requires --%s, which it does not declare", s.Use, name))
		}
		f.Usage += " (required)"
	}
	return cmd
}

// argNames are the <name> words of a use line, its arguments.
func argNames(use string) []string {
	var names []string
	for _, word := range strings.Fields(use)[1:] {
		if strings.HasPrefix(word, "<") && strings.HasSuffix(word, ">") {
			names = append(names, word)
		}
	}
	return names
}
