"""The checks of scripts/lint.py that no linter has, each on a file that breaks its rule and on one that keeps it."""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location("lint", HERE / "lint.py")
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


class LintTest(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.root = root
        self.old = lint.ROOT
        lint.ROOT = root
        self.addCleanup(setattr, lint, "ROOT", self.old)

    def write(self, path: str, text: str) -> str:
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text(text, encoding="utf-8")
        return path

    def test_hygiene_refuses_a_machines_path_and_a_personal_email(self):
        # Written in parts, as lint.py's own patterns are, so that this file passes the check it tests.
        bad = self.write("notes.md", "built in " + "/" + "Users/someone/src\nask someone" + "@" + "gmail.com\n")
        good = self.write("good.md", "admin@example.com, probe@login.localhost, x@users.noreply.github.com\n"
                                     "funcdctl-infra@v0.10.0 ghcr.io/pyvvo/funcd-opentofu@sha256:abc\n")
        found = lint.hygiene([bad, good])
        self.assertEqual([f.split(":")[1] for f in found], ["1", "2"], found)

    def test_quoting_wants_single_quotes_around_an_expression_with_a_colon(self):
        bad = self.write("a.yaml", 'env:\n  A: "${{ x ? \'y\' : \'z\' }}"\n  B: ${{ x ? 1 : 2 }}\n  - "${{ x ? 1 : 2 }}"\n')
        good = self.write("b.yaml", "env:\n  A: '${{ x ? \"y\" : \"z\" }}'\n  B: ${{ inputs.ref || github.ref }}\n"
                                    "run: |\n  echo \"${{ x ? 'y' : 'z' }}\"\n\n  echo done\nnext: 1\n")
        self.assertEqual([f.split(":")[1] for f in lint.quoting([bad])], ["2", "3", "4"])
        self.assertEqual(lint.quoting([good]), [])

    def test_plugins_are_go_commands_on_the_kit_that_reach_funcd_through_it(self):
        kit = 'package main\n\nimport "github.com/pyvvo/funcd-addons/internal/pluginkit"\n'
        good = [self.write("a/plugin/funcdctl-a/main.go", kit), self.write("a/plugin/funcdctl-a/main_test.go", "package main\n"),
                self.write("a/plugin/funcdctl-a/stamp.go", "//go:build ignore\n\npackage main\n")]
        self.assertEqual(lint.plugins(good), [])
        bad = [self.write("b/plugin/funcdctl-b/main.go", kit + 'var p = os.Getenv("FUNCDCTL")\n'),
               self.write("b/plugin/funcdctl-b/helper.py", ""),
               self.write("c/plugin/tool/main.go", kit),
               self.write("d/plugin/funcdctl-d/main.go", "package main\n"),
               self.write("e/plugin/funcdctl-e", "#!/bin/sh\n")]
        found = "\n".join(lint.plugins(bad))
        for want in ("funcdctl-b/main.go: a plugin reaches funcd through pluginkit.Funcdctl",
                     "helper.py: a plugin is one Go command", "c/plugin/tool/main.go: a plugin folder holds only",
                     "d/plugin/funcdctl-d: a plugin is built on internal/pluginkit", "d/plugin/funcdctl-d: a plugin has its tests",
                     "e/plugin/funcdctl-e: a plugin folder holds only"):
            self.assertIn(want, found)


if __name__ == "__main__":
    unittest.main()
