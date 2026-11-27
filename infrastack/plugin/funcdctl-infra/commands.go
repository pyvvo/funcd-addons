package main

import (
	"github.com/pyvvo/funcd-addons/internal/pluginkit"
	"github.com/spf13/pflag"
)

var pushCommand = pluginkit.Spec[pushInput]{
	Use:   "push <module> <ref>",
	Short: "Push an OpenTofu module with its providers and its contract; print <ref>@<digest>",
	Args: func(in *pushInput, args []string) {
		in.Module, in.Ref = args[0], args[1]
	},
	Run: push,
}

var outputsCommand = pluginkit.Spec[outputsInput]{
	Use:   "outputs <stack>",
	Short: "Print an InfraStack's outputs, a sensitive one as (sensitive)",
	Flags: func(fs *pflag.FlagSet, in *outputsInput) {
		pluginkit.Namespace(fs, &in.Namespace)
		pluginkit.Output(fs, &in.Output)
	},
	Args: func(in *outputsInput, args []string) {
		in.Stack = args[0]
	},
	Run: outputs,
}

var approveCommand = pluginkit.Spec[approveInput]{
	Use:   "approve <stack>",
	Short: "Show the plan of the run that an InfraStack waits on, and approve it once confirmed",
	Flags: func(fs *pflag.FlagSet, in *approveInput) {
		pluginkit.Namespace(fs, &in.Namespace)
	},
	Args: func(in *approveInput, args []string) {
		in.Stack = args[0]
	},
	Run: approve,
}
