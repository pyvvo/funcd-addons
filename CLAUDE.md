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
- A plugin is one Go command under `<addon>/plugin/funcdctl-<addon>/`, with its tests; it reaches funcd through
  `$FUNCDCTL`. The exception is InfraStack's `infrastack/plugin/funcdctl-infra`, a stdlib Python script, as funcd
  ADR-0355 Decision 1 decides: it reaches funcd through `$FUNCDCTL`, else `funcdctl` on PATH.
- Run the toolchain through the flake: `nix develop -c just ci`, `nix develop -c just e2e`.
- PR titles and commits are conventional (`feat(rauthy): …`): the queue squashes with the title, and release-please
  reads it.
