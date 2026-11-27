---
name: addon-readme
description: Write or rewrite a README of funcd-addons, an add-on's or the root one, to the README rule, the README template and the word budget, keeping every command, manifest and fact. Use for "write the README of the new add-on", "rewrite the InfraStack README", "the README is too long", or a README that the check refuses.
---

# addon-readme

Write one README around its reader's tasks: an admin who registers the add-on, a user who declares its object, and a
developer who builds and checks it.

1. **Read the rule**, `.claude/rules/readme.md`, and the README's current text. In a rewrite, read the code and the
   manifests that the README describes too.
2. **Follow the template.** `scripts/readme-template.yaml` gives the README's shape: the first section under the
   title, then the `##` sections in its order. An add-on's README starts with what the add-on adds and its smallest
   working example, then `Register the add-on`, then one task section per task, then the optional sections that apply.
   A section appears only when it explains a real task or behavior of the add-on.
3. **Keep everything in a rewrite.** Keep every command, manifest, code block, output, table of values, identifier,
   default, limit and condition unchanged, and shorten only the prose around them. Make a coverage list: each fact or
   sentence removed as repeated or as commentary, and where the fact stands now.
4. **Move what does not fit.** Reference detail that no task needs, such as the mechanics of a run, goes to a page under
   the add-on's `docs/`, linked on the term that needs it. Never drop a fact to fit the budget.
5. **Run the check**: `nix develop -c python3 scripts/readme-check.py <README.md>`, which `just ci` and the pre-commit
   hook also run. It counts every word of the file, fenced code included. Over the target (800 for an add-on's README,
   400 for the root one) it warns, and the README passes: move detail out when the README keeps growing. Over the hard
   cap (1200 and 600) it fails, as does a section out of the template's order, a refused heading or a missing required
   section. Then run `nix develop -c just lint`, which checks that no file, a README too, holds a machine's path or a
   personal email, and `nix develop -c just ci`, and check that every relative link resolves.
6. **Leave the plugin's command reference to the kit.** `just plugin-docs` writes it into the add-on's
   `docs/plugin.md`, from the plugin's commands, as the `funcdctl-plugin` skill says. Never write that reference by
   hand: the README's `## The plugin` section links the page.
