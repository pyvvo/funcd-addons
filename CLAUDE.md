# funcd-addons — agent working agreement

This repository holds the add-ons built outside funcd. The platform (daemon, API, CLI, ADRs) lives in
[pyvvo/funcd](https://github.com/pyvvo/funcd), and an add-on's engine image in its own repository (rauthy's in
[pyvvo/funcd-rauthy](https://github.com/pyvvo/funcd-rauthy)).

## ⛔ Nothing about the dev machine ever enters the repo

No absolute OS paths, no local username, no personal email: not in files, commit messages, examples or grep
patterns. Paths are repo-root-relative. The only identity is `green-0-rabbit`, `github.com/pyvvo` and "The funcd
Authors".

## Decisions live in funcd

What an add-on is and how funcd runs it are funcd ADRs (ADR-0332 to ADR-0336, ADR-0344 for the runtimes an Addon
ships). This repository follows them and never
decides on its own: a new field or behavior needs a funcd ADR first.

## Rules

- An Addon pins each runtime's image by digest; `just ci` refuses a tag.
- YAML is block style: no `{ key: value }` or `[a, b]`, except `{}` or `[]` for an empty value.
- An `env` value that holds `: ` inside `${{ }}`, such as a ternary, is single-quoted.
- A plugin is one Go command under `<addon>/plugin/funcdctl-<addon>/`, built on `internal/pluginkit`, with its tests;
  it reaches funcd through `$FUNCDCTL`, else `funcdctl` on PATH. The `funcdctl-plugin` skill adds a plugin or a
  command, and `just plugin-docs` writes its reference into `<addon>/docs/plugin.md`.
- An import is at the top of its module, never inside a function.
- `just lint` checks these rules, and that no file holds a machine's path or a personal email: golangci-lint, yamllint,
  ruff, eslint and `scripts/lint.py`. `just ci` and the pre-commit hook run it.
- Run the toolchain through the flake: `nix develop -c just ci`, `nix develop -c just e2e`.
- PR titles and commits are conventional (`feat(rauthy): …`): the queue squashes with the title, and release-please
  reads it.
