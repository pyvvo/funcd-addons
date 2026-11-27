// Command funcdctl-rauthy is the funcdctl plugin of the rauthy add-on (funcd ADR-0319): on PATH, funcdctl runs it as
// `funcdctl rauthy`. keys applies the Secret an IdentityProvider binds, holding rauthy's cluster and encryption keys
// and its first admin, and urls prints an IdentityProvider's issuer, account and admin URLs.
package main

import "github.com/pyvvo/funcd-addons/internal/pluginkit"

func main() {
	pluginkit.Main(plugin())
}

// plugin registers the plugin's commands.
func plugin() *pluginkit.Plugin {
	return &pluginkit.Plugin{
		Name:     "rauthy",
		Short:    "The rauthy add-on's commands",
		Commands: []pluginkit.Command{keysCommand, urlsCommand},
	}
}
