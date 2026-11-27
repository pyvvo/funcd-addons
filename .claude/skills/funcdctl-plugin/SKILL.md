---
name: funcdctl-plugin
description: Add a funcdctl plugin to an add-on of funcd-addons, or a command to one, with the plugin kit in internal/pluginkit - declare the command, type and validate its input, write its service against funcdctl, test it with the fakes, regenerate its reference, build and release it. Use for "add a funcdctl plugin to the tunnel add-on", "add a command to funcdctl infra", "a new flag for funcdctl rauthy keys", or a plugin whose reference check fails.
---

# funcdctl-plugin

A plugin is one Go command, `<addon>/plugin/funcdctl-<addon>/`, that funcdctl runs from PATH as `funcdctl <addon>`
(funcd ADR-0319). It reaches funcd only through funcdctl. `infrastack/plugin/funcdctl-infra/` is the full example and
`rauthy/plugin/funcdctl-rauthy/` the small one. Keep the kit small: it grows only by what two plugins need.

1. **Declare the command** in the plugin's `commands.go`, as a `pluginkit.Spec` (`internal/pluginkit/plugin.go`): its
   use line with each argument as `<name>`, its short help, its flags bound to the input with their defaults, the
   flags in `Required`, `Args` and `Run`. Add it to `Commands` in `plugin()` in `main.go`, in the order a user takes
   the commands. `-n/--namespace` and `-o/--output json` are the kit's (`internal/pluginkit/flags.go`), shared by every
   plugin.
2. **Type and validate its input** in `input.go`: a struct with a value method `Validate() error` that refuses what
   the command cannot run with, in the words of the error a user reads. The kit checks the number of arguments and
   the required flags before it.
3. **Write its service** in its own file, such as `push.go`: `func(env *pluginkit.Env, in <input>) error`. It calls
   funcd through `env.Funcdctl` (`internal/pluginkit/funcdctl.go`): `Run`, `JSON`, `Get`, `Apply`, and `Interactive`
   for a command that asks a person. It prints with `pluginkit.Table` or `pluginkit.JSON`
   (`internal/pluginkit/output.go`). It returns its errors: `act` prints `funcdctl <addon>: <message>` and exits 1.
4. **Test it with the fakes**: `plugintest.Main` in `TestMain` and `plugintest.Install`
   (`internal/pluginkit/plugintest/fake.go`) run a fake funcdctl, or a fake docker, written in Go, that records its
   calls. Run the command through `plugin().Run(args, stdin, stdout, stderr)` and check its exit code and output, a
   refusal too. `nix develop -c go test -race ./<addon>/plugin/...`.
5. **Regenerate its reference**: `nix develop -c just plugin-docs` writes it between the markers of
   `<addon>/docs/plugin.md` (`internal/pluginkit/plugintest/readme.go`). A new plugin's page holds the two lines that
   `plugintest.Markers` returns, and its test calls `plugintest.CheckReference`, so `just ci` fails while it is stale.
   The README's `## The plugin` section links the page, under `.claude/rules/readme.md`.
6. **Build it for each platform**: `nix develop -c just plugins <goos> <goarch>` writes `dist/<goos>-<goarch>/`. A
   value that a release stamps into the plugin comes from a `stamp.go` beside it, as funcdctl-infra's image does.
   Then `nix develop -c just ci`.
7. **Release it**: a conventional PR title (`feat(<addon>): …`, `feat!` when an install step changes). On a release,
   `.github/workflows/release.yml` attaches `funcdctl-<addon>-<os>-<arch>.tar.gz` for Linux and macOS.
