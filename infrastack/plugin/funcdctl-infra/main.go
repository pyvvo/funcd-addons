// Command funcdctl-infra is the InfraStack add-on's funcdctl plugin (funcd ADR-0319): on PATH, funcdctl runs it as
// `funcdctl infra`.
//
//   - push pushes an OpenTofu module as one bundle per platform, linux/amd64 and linux/arm64, each with that
//     platform's providers and a contract made from the module's variables and outputs, joined by an image index, and
//     prints <ref>@<digest> (funcd ADR-0355).
//   - outputs prints each output of an InfraStack from the Secret its Job owns, a sensitive one as (sensitive) (funcd
//     ADR-0356).
//   - approve shows the plan of the run that an InfraStack waits on, the apply run of its generation or, while it is
//     deleting, its destroy run, and approves it once a person confirms.
//
// push runs OpenTofu in the add-on's runtime image, on the first of docker and nerdctl on PATH, with no bind mount.
// The plugin reaches funcd only through $FUNCDCTL.
package main

import "github.com/pyvvo/funcd-addons/internal/pluginkit"

func main() {
	pluginkit.Main(plugin())
}

// plugin registers the plugin's commands, in the order a stack's life takes them.
func plugin() *pluginkit.Plugin {
	return &pluginkit.Plugin{
		Name:     "infra",
		Short:    "The InfraStack add-on's commands",
		Commands: []pluginkit.Command{pushCommand, outputsCommand, approveCommand},
	}
}
