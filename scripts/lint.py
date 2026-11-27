#!/usr/bin/env python3
"""Check the repository's conventions, the Rules of CLAUDE.md. `just lint` runs it on every tracked file, and the
pre-commit hook on the staged ones:

    python3 scripts/lint.py [<file>...]

It runs golangci-lint on the Go packages, yamllint on the YAML, ruff on the Python and eslint on the JavaScript, each
with its config at the repository's root. Then it runs the checks that no linter has:

- hygiene: no file holds an absolute path of a developer's machine or a personal email;
- quoting: a YAML value whose ${{ }} expression holds ": ", such as a ternary, is single-quoted;
- plugins: each <addon>/plugin/ folder holds funcdctl-<name>/ Go commands on internal/pluginkit, with their tests,
  which reach funcd only through the kit's funcdctl.

It runs every linter and check, then exits 1 when one failed. It needs nothing outside Python's standard library.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The prefixes of an absolute path on a developer's machine, written in parts so that this file passes its own check.
PATH_PREFIXES = ("/" + "Users/", "/" + "home/", "C:" + "\\" + "Users")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b")
# The domains that hold no one's address: those reserved for examples and tests (RFC 2606, RFC 6761), and GitHub's
# no-reply one; and the no-reply address of a commit's co-author line.
FREE_DOMAIN = re.compile(r"(^|\.)(example\.(com|org|net)|example|test|localhost|invalid|users\.noreply\.github\.com)$")
FREE_ADDRESSES = {"noreply@anthropic.com"}

EXPRESSION_WITH_COLON = re.compile(r"\$\{\{[^}]*: [^}]*\}\}")
KEY_VALUE = re.compile(r"^(\s*)(?:- +)?[^\s#'\"-][^:#]*:(?: +(.*))?$")
ITEM = re.compile(r"^(\s*)- +(.*)$")
BLOCK_SCALAR = re.compile(r"^[|>][0-9+-]*(\s+#.*)?$")

PLUGIN = re.compile(r"^funcdctl-[a-z][a-z0-9-]*$")
KIT_IMPORT = '"github.com/pyvvo/funcd-addons/internal/pluginkit"'
OWN_FUNCDCTL = re.compile(r'Getenv\("FUNCDCTL"\)|exec\.Command(Context)?\([^)]*"funcdctl"')


def tracked() -> list[str]:
    """The files git tracks, or would add: the repository's own, never work/ or dist/."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if (ROOT / f).is_file()]


def text_of(path: str) -> str | None:
    """The file's text, or None for a file that is no UTF-8 text."""
    try:
        return (ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def hygiene(files: list[str]) -> list[str]:
    """Each line of a file that holds an absolute path of a developer's machine or a personal email."""
    found = []
    for path in files:
        text = text_of(path)
        if text is None:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(p in line for p in PATH_PREFIXES):
                found.append(f"{path}:{n}: an absolute path of a machine: write it relative to the repository's root")
            for m in EMAIL.finditer(line):
                if m.group(0) not in FREE_ADDRESSES and not FREE_DOMAIN.search(m.group(1).lower()):
                    found.append(f"{path}:{n}: a personal email: use an example.com address")
    return found


def quoting(files: list[str]) -> list[str]:
    """Each YAML value whose ${{ }} expression holds ": " and that is not single-quoted. A block scalar's lines are
    its text, not values."""
    found = []
    for path in files:
        if not path.endswith((".yaml", ".yml")):
            continue
        block = None
        for n, line in enumerate((text_of(path) or "").splitlines(), 1):
            indent = len(line) - len(line.lstrip(" "))
            if block is not None and (not line.strip() or indent > block):
                continue
            block = None
            if line.lstrip().startswith("#"):
                continue
            m = KEY_VALUE.match(line) or ITEM.match(line)
            value = (m.group(2) or "").strip() if m else ""
            if BLOCK_SCALAR.match(value):
                block = indent
                continue
            if EXPRESSION_WITH_COLON.search(value) and not value.startswith("'"):
                found.append(f"{path}:{n}: a value whose ${{{{ }}}} holds \": \" is single-quoted")
    return found


def plugins(files: list[str]) -> list[str]:
    """What breaks the plugin rule: a plugin is one Go command under <addon>/plugin/funcdctl-<name>/, built on
    internal/pluginkit, with its tests, and it reaches funcd only through the kit's funcdctl."""
    found = []
    folders: dict[Path, list[Path]] = {}
    for path in files:
        p = Path(path)
        if len(p.parts) > 2 and p.parts[1] == "plugin":
            if len(p.parts) == 3 or not PLUGIN.match(p.parts[2]):
                found.append(f"{path}: a plugin folder holds only funcdctl-<name>/ commands")
                continue
            folders.setdefault(Path(*p.parts[:3]), []).append(p)
    for folder, entries in sorted(folders.items()):
        code = [e for e in entries if e.suffix == ".go" and not e.name.endswith("_test.go")]
        for e in entries:
            if e.suffix != ".go":
                found.append(f"{e}: a plugin is one Go command: move this file out of {folder}/")
        if not any(e.name.endswith("_test.go") for e in entries):
            found.append(f"{folder}: a plugin has its tests beside it")
        if not any(KIT_IMPORT in (text_of(str(e)) or "") for e in code):
            found.append(f"{folder}: a plugin is built on internal/pluginkit")
        for e in code:
            if OWN_FUNCDCTL.search(text_of(str(e)) or ""):
                found.append(f"{e}: a plugin reaches funcd through pluginkit.Funcdctl, which runs $FUNCDCTL")
    return found


def run(command: list[str]) -> bool:
    """Run a linter from the repository's root; it prints its own findings."""
    return subprocess.run(command, cwd=ROOT).returncode == 0


def main(args: list[str]) -> int:
    every = tracked()
    files = [f for f in args if (ROOT / f).is_file()] if args else every

    def of(*suffixes: str) -> list[str]:
        return [f for f in files if f.endswith(suffixes)]

    ok = True
    go = of(".go")
    if go:
        packages = ["./..."] if not args else sorted({"./" + Path(f).parent.as_posix() for f in go})
        ok &= run(["golangci-lint", "run", *packages])
    if of(".yaml", ".yml"):
        ok &= run(["yamllint", "--strict", *of(".yaml", ".yml")])
    if of(".py"):
        ok &= run(["ruff", "check", "--no-cache", *of(".py")])
    if of(".js", ".mjs"):
        ok &= run(["eslint", *of(".js", ".mjs")])
    found = hygiene(files) + quoting(files) + plugins(every)
    for line in found:
        print(line)
    return 0 if ok and not found else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
