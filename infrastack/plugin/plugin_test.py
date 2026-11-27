"""funcdctl-infra, with a fake container engine and a fake funcdctl that record their argv (funcd ADR-0355 Implementation
plan step 1). The engine stands in for OpenTofu in the add-on's image: it answers `tofu show -json -module` from the
test's configurations, writes a lock file, and mirrors one provider package per platform. It copies only by a tar on its
standard input or output, as a nerdctl in a VM must, which sees no path of this host."""

import base64
import importlib.machinery
import re
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import stamp  # noqa: E402 - beside this test

_loader = importlib.machinery.SourceFileLoader("funcdctl_infra", str(HERE / "funcdctl-infra"))
_spec = importlib.util.spec_from_loader("funcdctl_infra", _loader)
infra = importlib.util.module_from_spec(_spec)
_loader.exec_module(infra)

FAKE_ENGINE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, shutil, sys, tarfile, uuid
    state = os.environ["FAKE_ENGINE"]
    args = sys.argv[1:]
    with open(os.path.join(state, "calls.jsonl"), "a") as f:
        f.write(json.dumps(args) + "\\n")
    def root(cid):
        return os.path.join(state, "containers", cid)
    def local(spec):
        cid, _, path = spec.partition(":")
        return os.path.join(root(cid), path.lstrip("/"))
    if args[0] == "create":
        cid = uuid.uuid4().hex[:12]
        os.makedirs(root(cid))
        json.dump(json.loads(args[-1]), open(os.path.join(root(cid), "commands.json"), "w"))
        print(cid)
    elif args[0] == "cp":
        src, dst = args[1], args[2]
        if src == "-":
            with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as tar:
                for m in tar:
                    assert m.uid == 0 and m.gid == 0, m.name + " is not root's"
                    tar.extract(m, local(dst))
        elif dst == "-" and os.path.lexists(local(src)):
            with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tar:
                tar.add(local(src), arcname=os.path.basename(src))
        else:
            sys.stderr.write("unable to copy: source does not exist")
            sys.exit(1)
    elif args[0] == "start":
        cid = args[-1]
        work = os.path.join(root(cid), "m")
        shows = json.load(open(os.path.join(state, "shows.json")))
        outs = []
        for cmd in json.load(open(os.path.join(root(cid), "commands.json"))):
            if cmd[0] == "show":
                at = cmd[-1].split("=", 1)[1]
                if not os.path.isdir(os.path.join(work, at)):
                    sys.stderr.write("tofu show: no folder " + at)
                    sys.exit(1)
                outs.append(json.dumps(shows[at]))
            elif cmd[:2] == ["providers", "lock"]:
                platforms = sorted(a.split("=", 1)[1] for a in cmd if a.startswith("-platform="))
                open(os.path.join(work, ".terraform.lock.hcl"), "w").write("# hashes for " + " ".join(platforms) + "\\n")
                outs.append("")
            elif cmd[:2] == ["providers", "mirror"]:
                platform = next(a.split("=", 1)[1] for a in cmd if a.startswith("-platform="))
                folder = os.path.join(work, cmd[-1], "registry.opentofu.org", "hashicorp", "random")
                os.makedirs(folder, exist_ok=True)
                open(os.path.join(folder, "terraform-provider-random_3.6.3_" + platform + ".zip"), "w").write(platform)
                open(os.path.join(work, cmd[-1], "registry.opentofu.org", "hashicorp", "random", "index.json"), "w").write("{}")
                outs.append("")
            else:
                outs.append("")
        print(json.dumps(outs))
    elif args[0] == "rm":
        shutil.rmtree(root(args[-1]), ignore_errors=True)
    """)

FAKE_FUNCDCTL = textwrap.dedent("""\
    #!/usr/bin/env python3
    import hashlib, json, os, sys
    state = os.environ["FAKE_FUNCDCTL"]
    args = sys.argv[1:]
    seen = {"args": args}
    if args[0] == "push":
        build = args[1]
        seen["files"] = sorted(os.path.relpath(os.path.join(d, f), build) for d, _, fs in os.walk(build) for f in fs)
        seen["contract"] = open(os.path.join(build, os.path.basename(build) + ".contract.yaml")).read()
    with open(os.path.join(state, "calls.jsonl"), "a") as f:
        f.write(json.dumps(seen) + "\\n")
    if args[0] in ("push", "index"):
        ref = args[2] if args[0] == "push" else args[1]
        print(ref + "@sha256:" + hashlib.sha256(" ".join(args).encode()).hexdigest())
    elif args[0] == "get":
        objects = json.load(open(os.path.join(state, "objects.json")))
        key = args[1] + "/" + args[2]
        if key not in objects:
            sys.stderr.write("funcdctl: sdk: store.Get: " + key + " not found\\n")
            sys.exit(1)
        print(json.dumps(objects[key]))
    elif args[:2] == ["job", "runs"]:
        print(json.dumps(json.load(open(os.path.join(state, "runs.json")))))
    """)

# The contract of a module with the string variable prefix and the plain output name, as funcd ADR-0355's Contracts
# write it.
EXAMPLE_CONTRACT = """\
runtime: opentofu1
input:
  type: object
  additionalProperties: false
  properties:
    action:
      type: string
      enum:
        - apply
        - destroy
        - plan
    variables:
      type: object
      additionalProperties: false
      properties:
        prefix:
          type: string
      required:
        - prefix
  required:
    - action
    - variables
output:
  type: object
  additionalProperties: false
  properties:
    outputs:
      type: object
      additionalProperties: false
      properties:
        name: {}
    plan:
      type: object
      additionalProperties: false
      properties:
        add:
          type: integer
        change:
          type: integer
        destroy:
          type: integer
        replace:
          type: integer
        resources:
          type: array
          items:
            type: string
      required:
        - add
        - change
        - destroy
        - replace
        - resources
"""

ROOT_SHOWN = {"root_module": {
    "variables": {"prefix": {"type": "string", "required": True}, "token": {"type": "string", "required": True,
                                                                          "sensitive": True}},
    "outputs": {"name": {}, "secret": {"sensitive": True}},
    "module_calls": {"sub": {"source": "./sub"}}}}
SUB_SHOWN = {"root_module": {"variables": {"x": {"type": "string", "required": True}}, "outputs": {"y": {}}}}


def executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.engine, self.funcd = self.dir / "engine", self.dir / "funcd"
        self.engine.mkdir()
        self.funcd.mkdir()
        executable(self.bin / "docker", FAKE_ENGINE)
        executable(self.bin / "funcdctl", FAKE_FUNCDCTL)
        self.shows({".": ROOT_SHOWN, "sub": SUB_SHOWN})
        self.module = self.dir / "work" / "random-stack"
        (self.module / "sub").mkdir(parents=True)
        (self.module / "main.tf").write_text('module "sub" {\n  source = "./sub"\n}\n', encoding="utf-8")
        (self.module / "outputs.tf").write_text("# outputs\n", encoding="utf-8")
        (self.module / "sub" / "main.tf").write_text("# the child\n", encoding="utf-8")
        (self.module / "random-stack.contract.yaml").write_text("# written by hand\n", encoding="utf-8")
        (self.module / "random-stack.contract.json").write_text("{}", encoding="utf-8")
        (self.module / "__funcd_contract.json").write_text("{}", encoding="utf-8")
        (self.module / ".funcd-providers").mkdir()
        (self.module / ".funcd-providers" / "stale.zip").write_text("stale", encoding="utf-8")
        (self.module / ".terraform" / "providers").mkdir(parents=True)
        (self.module / ".terraform" / "terraform.tfstate").write_text("{}", encoding="utf-8")
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}", "FAKE_ENGINE": str(self.engine),
               "FAKE_FUNCDCTL": str(self.funcd), "FUNCDCTL": str(self.bin / "funcdctl")}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)

    def shows(self, shown):
        (self.engine / "shows.json").write_text(json.dumps(shown), encoding="utf-8")

    def engine_calls(self):
        path = self.engine / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def funcd_calls(self):
        path = self.funcd / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def objects(self, **objects):
        (self.funcd / "objects.json").write_text(json.dumps(objects), encoding="utf-8")

    def awaiting(self, *names):
        (self.funcd / "runs.json").write_text(json.dumps([{"metadata": {"name": n}} for n in names]), encoding="utf-8")

    def stack(self, reason, message):
        self.objects(**{"infrastack/edge": {"spec": {"bindings": {"job": [{"alias": "stack", "job": "edge"}]}},
                                            "status": {"conditions": [{"type": "Ready", "status": "False",
                                                                       "reason": reason, "message": message}]}}})

    # scenario: infra-push-builds-index
    def test_scenario_infra_push_builds_index(self):
        """The push reads the module and its local child, locks both platforms, and pushes one bundle per platform,
        each with its own provider packages and the contract made from the module, joined by an index."""
        ref = "oci-layout:///tmp/layout:stack-1"
        got = infra.push(self.module, ref)
        self.assertTrue(got.startswith(ref + "@sha256:"), got)
        pushes = [c for c in self.funcd_calls() if c["args"][0] == "push"]
        self.assertEqual([c["args"][2:] for c in pushes], [
            [f"{ref}-linux-amd64", "--entry", "main.tf", "--platform", "linux/amd64"],
            [f"{ref}-linux-arm64", "--entry", "main.tf", "--platform", "linux/arm64"]])
        self.assertEqual(self.funcd_calls()[-1]["args"], ["index", ref, f"{ref}-linux-amd64", f"{ref}-linux-arm64"])
        for push, arch in zip(pushes, ("amd64", "arm64")):
            self.assertEqual(Path(push["args"][1]).name, "random-stack", "the build folder is named as the module's")
            files = push["files"]
            self.assertIn(f".funcd-providers/registry.opentofu.org/hashicorp/random/terraform-provider-random_3.6.3_linux_{arch}.zip",
                          files)
            self.assertFalse([f for f in files if f.endswith(".zip") and arch not in f], "only its own platform's packages")
            for kept in ("main.tf", "outputs.tf", "sub/main.tf", ".terraform.lock.hcl", "random-stack.contract.yaml"):
                self.assertIn(kept, files)
            self.assertFalse([f for f in files if f.startswith(".terraform/")], "no .terraform/")
            self.assertNotIn("written by hand", push["contract"], "the module's own contract file is left out")
            for left in ("random-stack.contract.json", "__funcd_contract.json", ".funcd-providers/stale.zip"):
                self.assertNotIn(left, files, "the module's own contract files and provider folder are left out")
            self.assertIn("runtime: opentofu1", push["contract"])
        self.assertEqual((self.module / ".terraform.lock.hcl").read_text(encoding="utf-8"),
                         "# hashes for linux_amd64 linux_arm64\n", "the lock file is copied back into the module")
        shows = [json.loads(c[-1]) for c in self.engine_calls() if c[0] == "create" and "show" in c[-1]]
        self.assertEqual([[cmd[-1] for cmd in s] for s in shows], [["-module=."], ["-module=sub"]], "the child is read too")

    def test_the_push_runs_opentofu_only_in_the_addons_image_with_no_bind_mount(self):
        infra.push(self.module, "registry.example.com/stacks/random:1")
        creates = [c for c in self.engine_calls() if c[0] == "create"]
        self.assertEqual(len(creates), 5, "the module's and its child's configurations, the lock, a mirror per platform")
        for c in creates:
            self.assertIn(infra.IMAGE, c)
            self.assertFalse({"-v", "--volume", "--mount"} & set(c), "no bind mount")
        lock = json.loads(creates[2][-1])
        self.assertEqual(lock, [["get"], ["providers", "lock", "-platform=linux_amd64", "-platform=linux_arm64"]])
        mirrors = [json.loads(c[-1]) for c in creates[3:]]
        self.assertEqual(mirrors, [[["get"], ["providers", "mirror", f"-platform=linux_{a}", ".funcd-providers"]]
                                   for a in ("amd64", "arm64")])
        self.assertTrue(all(c[0] in ("create", "cp", "start", "rm") for c in self.engine_calls()))
        with mock.patch.dict(os.environ, {"INFRASTACK_OPENTOFU_IMAGE": "funcd-addons/opentofu:check"}):
            (self.engine / "calls.jsonl").unlink()
            infra.push(self.module, "registry.example.com/stacks/random:1")
        self.assertTrue(all("funcd-addons/opentofu:check" in c for c in self.engine_calls() if c[0] == "create"))

    def test_a_copy_the_engine_refuses_fails_with_its_words(self):
        with self.assertRaisesRegex(infra.InfraError, "docker cp failed: unable to copy: source does not exist"):
            infra.Engine()._copy_out("gone", "x", self.module / "x")

    def test_unpack_takes_plain_files_and_folders_inside_its_target(self):
        def tar_of(*members):
            f = io.BytesIO()
            with tarfile.open(fileobj=f, mode="w") as tar:
                for name, kind in members:
                    m = tarfile.TarInfo(name)
                    m.type = kind
                    m.mode = 0o640
                    body = b"" if kind != tarfile.REGTYPE else b"x"
                    m.size = len(body)
                    m.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
                    tar.addfile(m, io.BytesIO(body))
            f.seek(0)
            return tarfile.open(fileobj=f)

        into = self.module / "out"
        infra.unpack(tar_of(("p", tarfile.DIRTYPE), ("p/a/b.zip", tarfile.REGTYPE)), into)
        self.assertEqual((into / "a" / "b.zip").read_bytes(), b"x")
        self.assertEqual(stat.S_IMODE((into / "a" / "b.zip").stat().st_mode), 0o640)
        for members in ((("p", tarfile.DIRTYPE), ("p/../../x", tarfile.REGTYPE)), (("/p/x", tarfile.REGTYPE),),
                        (("p", tarfile.DIRTYPE), ("p/link", tarfile.SYMTYPE))):
            with self.assertRaisesRegex(infra.InfraError, "no plain file or folder"):
                infra.unpack(tar_of(*members), self.module / "refused")
            self.assertFalse((self.module.parent / "x").exists())

    def test_the_engine_is_docker_else_nerdctl(self):
        os.rename(self.bin / "docker", self.bin / "nerdctl")
        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            self.assertTrue(infra.Engine().binary.endswith("nerdctl"))
            os.remove(self.bin / "nerdctl")
            with self.assertRaisesRegex(infra.InfraError, "neither docker nor nerdctl"):
                infra.Engine()

    # scenario: infra-remote-module-refused
    def test_scenario_infra_remote_module_refused(self):
        """A call of a registry module, or of a folder outside the module's, pushes nothing and names the call; so does
        one of a local child."""
        for source, named in (("hashicorp/consul/aws", "consul (hashicorp/consul/aws)"), ("../shared", "consul (../shared)"),
                              ("git::https://example.com/m.git", "consul (git::https://example.com/m.git)"),
                              ("./sub/../..", "consul (./sub/../..)")):
            with self.subTest(source):
                self.shows({".": {"root_module": {"module_calls": {"consul": {"source": source}}}}})
                with self.assertRaisesRegex(infra.InfraError, re.escape(f"the module calls {named}")):
                    infra.push(self.module, "registry.example.com/stacks/random:1")
                self.assertEqual(self.funcd_calls(), [], "nothing is pushed")
        self.shows({".": ROOT_SHOWN, "sub": {"root_module": {"module_calls": {"vpc": {"source": "terraform-aws-modules/vpc/aws"}}}}})
        with self.assertRaisesRegex(infra.InfraError, r"its child module sub calls vpc \(terraform-aws-modules/vpc/aws\)"):
            infra.push(self.module, "registry.example.com/stacks/random:1")
        self.assertEqual(self.funcd_calls(), [])

    def test_module_calls_takes_relative_paths_inside_the_folder(self):
        shown = {"root_module": {"module_calls": {
            "a": {"source": "./a"}, "b": {"source": "./x/../b"}, "up": {"source": "../up"}, "abs": {"source": "/abs"},
            "bare": {"source": "sub"}, "reg": {"source": "hashicorp/consul/aws"}}}}
        self.assertEqual(infra.module_calls(shown), ["abs (/abs)", "bare (sub)", "reg (hashicorp/consul/aws)", "up (../up)"])
        self.assertEqual(infra.module_calls({"root_module": {"module_calls": {"s": {"source": "../sibling"}}}}, "sub"), [],
                         "a child's call that stays inside the module's folder")
        self.assertEqual(infra.module_calls({"root_module": {"module_calls": {"s": {"source": "../../out"}}}}, "sub"),
                         ["s (../../out)"])

    # scenario: infra-local-files-refused (the push's half; the run's half is handler_test.py's)
    def test_scenario_infra_local_files_refused(self):
        """A module folder that holds a state or a variables file OpenTofu would load pushes nothing and names the file;
        one under .terraform/ is OpenTofu's own."""
        for name in ("terraform.tfstate", "terraform.tfstate.backup", "terraform.tfvars", "terraform.tfvars.json",
                     "prod.auto.tfvars", "prod.auto.tfvars.json", "sub/terraform.tfvars"):
            with self.subTest(name):
                path = self.module / name
                path.write_text("prefix = \"x\"\n", encoding="utf-8")
                with self.assertRaisesRegex(infra.InfraError, f"random-stack holds {name}"):
                    infra.push(self.module, "registry.example.com/stacks/random:1")
                path.unlink()
                self.assertEqual((self.engine_calls(), self.funcd_calls()), ([], []), "nothing runs or is pushed")

    def test_the_push_needs_a_folder_with_a_tf_file_and_a_tagged_ref(self):
        empty = self.dir / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(infra.InfraError, "no folder that holds a .tf file"):
            infra.push(empty, "registry.example.com/stacks/random:1")
        for ref in ("registry.example.com/stacks/random", "registry.example.com/stacks/random@sha256:" + "0" * 64,
                    "localhost:5000/random"):
            with self.subTest(ref), self.assertRaisesRegex(infra.InfraError, "names no tag"):
                infra.push(self.module, ref)

    def test_a_refused_push_ends_with_funcdctls_message(self):
        executable(self.bin / "funcdctl", "#!/bin/sh\necho 'funcdctl: the contract file is outside the funcd profile' >&2\nexit 1\n")
        with self.assertRaisesRegex(infra.InfraError, "outside the funcd profile"):
            infra.push(self.module, "registry.example.com/stacks/random:1")

    def test_schema_follows_the_type_table_row_by_row(self):
        rows = [
            ("string", {"type": "string"}),
            ("number", {"type": "number"}),
            ("bool", {"type": "boolean"}),
            (["list", "string"], {"type": "array", "items": {"type": "string"}}),
            (["set", "number"], {"type": "array", "items": {"type": "number"}}),
            (["tuple", ["string", "number"]], {"type": "array", "items": {}}),
            (["map", "bool"], {"type": "object", "additionalProperties": {"type": "boolean"}}),
            (["object", {"a": "string", "b": "number"}, ["b"]],
             {"type": "object", "additionalProperties": False, "properties": {"a": {"type": "string"}, "b": {"type": "number"}},
              "required": ["a"]}),
            (["object", {"a": "string"}], {"type": "object", "additionalProperties": False,
                                           "properties": {"a": {"type": "string"}}, "required": ["a"]}),
            (["object", {}], {}),
            (["list", ["object", {"n": "dynamic"}, ["n"]]],
             {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"n": {}}}}),
            ("dynamic", {}),
            (None, {}),
        ]
        for tofu_type, want in rows:
            with self.subTest(tofu_type):
                self.assertEqual(infra.schema(tofu_type), want)

    def test_the_contract_is_made_from_the_variables_and_outputs(self):
        shown = {"root_module": {"variables": {"prefix": {"type": "string", "required": True}}, "outputs": {"name": {}}}}
        self.assertEqual(infra.to_yaml(infra.contract(shown)), EXAMPLE_CONTRACT, "the contract of funcd ADR-0355's Contracts")
        doc = infra.contract(ROOT_SHOWN)
        variables = doc["input"]["properties"]["variables"]
        self.assertEqual(list(variables["properties"]), ["prefix"], "no input schema holds a sensitive variable")
        self.assertEqual(list(doc["output"]["properties"]["outputs"]["properties"]), ["name"], "nor a sensitive output")
        defaults = infra.contract({"root_module": {"variables": {"a": {"type": "string", "default": None},
                                                                 "b": {"default": "x"}, "c": {"required": True}}}})
        self.assertEqual(defaults["input"]["properties"]["variables"]["required"], ["c"], "one with a default is optional")
        bare = infra.contract({"root_module": {}})
        self.assertEqual(bare["output"]["properties"]["outputs"], {}, "a record that would have no member is the empty schema")
        self.assertEqual((bare["input"]["properties"]["variables"], bare["input"]["required"]), (infra.VOID, ["action"]),
                         "but a module with no variable takes none")
        optional = infra.contract({"root_module": {"variables": {"region": {"type": "string", "default": "DE"}}}})
        self.assertEqual(optional["input"]["required"], ["action"], "variables with defaults only are not required")
        self.assertEqual(optional["input"]["properties"]["variables"]["properties"], {"region": {"type": "string"}})

    # scenario: infra-sensitive-only-module
    def test_scenario_infra_sensitive_only_module(self):
        """funcd ADR-0355 Decision 3, amended 2026-11-24: a module whose only variable is sensitive takes void variables,
        a side that takes nothing as funcd ADR-0090 has a Function's, so a run's input that sets it is refused."""
        only_secret = infra.contract({"root_module": {"variables": {"token": {"type": "string", "sensitive": True}}}})
        self.assertEqual(only_secret["input"]["properties"]["variables"], {"type": "null"})
        self.assertEqual(only_secret["input"]["required"], ["action"])
        self.assertIn('variables:\n      type: "null"\n', infra.to_yaml(only_secret), "the string null, not YAML's null")

    def test_to_yaml_is_block_style(self):
        self.assertEqual(infra.to_yaml({"a": {"b": ["x", "true", "a b"], "c": {}, "d": 3, "e": False}}),
                         'a:\n  b:\n    - x\n    - "true"\n    - "a b"\n  c: {}\n  d: 3\n  e: false\n')

    # scenario: infra-outputs-lists-them
    def test_outputs_prints_each_key_and_never_a_sensitive_value(self):
        """funcd ADR-0356 Decision 8: the stack's Job, its contract, its ConfigMaps' INFRASTACK_OUTPUTS_SECRET, a later
        one winning, and that Secret, each value the contract does not name printed as (sensitive)."""
        def b64(text):
            return base64.b64encode(text.encode()).decode()
        self.objects(**{
            "infrastack/edge": {"spec": {"bindings": {"job": [{"alias": "stack", "job": "edge"}]}}},
            "job/edge": {"spec": {"bindings": {"config": ["edge-a", "edge-b"]}},
                         "status": {"contract": {"output": {"properties": {"outputs": {"properties": {"host": {}}}}}}}},
            "configmap/edge-a": {"spec": {"data": {"INFRASTACK_OUTPUTS_SECRET": "first"}}},
            "configmap/edge-b": {"spec": {"data": {"INFRASTACK_OUTPUTS_SECRET": "edge-outputs"}}},
            "secret/edge-outputs": {"spec": {"data": {"host": b64("a.b-cdn.net"), "FRP_TOKEN": b64("s3cret"),
                                                      "extra": b64("unnamed")}}}})
        self.assertEqual(infra.outputs("edge", "team-a"), ({"host": "a.b-cdn.net"}, ["FRP_TOKEN", "extra"]))
        self.assertEqual(self.funcd_calls()[0]["args"], ["get", "infrastack", "edge", "-n", "team-a", "-o", "json"])
        with mock.patch("builtins.print") as said:
            self.assertEqual(infra.main(["outputs", "edge", "-n", "team-a"]), 0)
        printed = "\n".join(str(c.args[0]) for c in said.call_args_list)
        self.assertEqual(printed, "NAME       VALUE\nhost       a.b-cdn.net\nFRP_TOKEN  (sensitive)\nextra      (sensitive)")
        self.assertNotIn("s3cret", printed)
        with mock.patch("builtins.print") as said:
            infra.main(["outputs", "edge", "-n", "team-a", "-o", "json"])
        self.assertEqual(json.loads(said.call_args.args[0]),
                         {"outputs": {"host": "a.b-cdn.net"}, "sensitive": ["FRP_TOKEN", "extra"]})
        self.assertNotIn("s3cret", said.call_args.args[0])

    def test_approve_approves_the_run_that_the_stack_names(self):
        """Of the Job's runs that await approval, approve hands funcdctl job approve the one the stack's Ready condition
        names, so an older generation's run that still waits is never approved by mistake."""
        self.stack("AwaitingApproval", "run edge-g2-abcd1234, which applies edge, awaits approval: "
                                       "funcdctl job approve edge-g2-abcd1234")
        self.awaiting("edge-g1-abcd1234", "edge-g2-abcd1234")
        self.assertEqual(infra.main(["approve", "edge", "-n", "team-a"]), 0)
        self.assertEqual([c["args"] for c in self.funcd_calls()], [
            ["get", "infrastack", "edge", "-n", "team-a", "-o", "json"],
            ["job", "runs", "edge", "-n", "team-a", "--phase", "AwaitingApproval", "-o", "json"],
            ["job", "approve", "edge-g2-abcd1234", "-n", "team-a"]])

    def test_approve_refuses_a_stack_that_awaits_no_approval(self):
        self.stack("Planning", "run edge-g1-abcd1234 plans its change")
        with self.assertRaisesRegex(infra.InfraError,
                                    "the InfraStack default/edge awaits no approval: run edge-g1-abcd1234 plans its change"):
            infra.approve("edge", "default")
        self.assertNotIn("approve", [c["args"][1] for c in self.funcd_calls() if c["args"][0] == "job"])

    def test_approve_refuses_when_the_named_run_no_longer_awaits(self):
        self.stack("AwaitingApproval", "run edge-d3-abcd1234, which destroys edge, awaits approval: "
                                       "funcdctl job approve edge-d3-abcd1234")
        self.awaiting("edge-g2-abcd1234")
        with self.assertRaisesRegex(infra.InfraError, "names no run that awaits approval"):
            infra.approve("edge", "default")
        self.assertNotIn("approve", [c["args"][1] for c in self.funcd_calls() if c["args"][0] == "job"])

    def test_outputs_needs_the_job_bound_as_stack(self):
        self.objects(**{"infrastack/edge": {"spec": {"bindings": {"job": [{"alias": "other", "job": "edge"}]}}}})
        with self.assertRaisesRegex(infra.InfraError, "the InfraStack default/edge binds no Job as stack"):
            infra.outputs("edge", "default")

    def test_a_refused_read_ends_outputs_with_funcdctls_error(self):
        self.objects(**{"infrastack/edge": {"spec": {"bindings": {"job": [{"alias": "stack", "job": "edge"}]}}}})
        with self.assertRaisesRegex(infra.InfraError, "store.Get: job/edge not found"):
            infra.outputs("edge", "default")
        self.objects(**{"infrastack/edge": {"spec": {"bindings": {"job": [{"alias": "stack", "job": "edge"}]}}}, "job/edge": {"spec": {}}})
        with self.assertRaisesRegex(infra.InfraError, "binds no ConfigMap that names INFRASTACK_OUTPUTS_SECRET"):
            infra.outputs("edge", "default")

    def test_the_plugins_image_is_its_addons_opentofu1_image(self):
        """funcd ADR-0355 Implementation plan step 4."""
        addon = (HERE.parent / "app" / "addon.yaml").read_text(encoding="utf-8")
        self.assertEqual(infra.IMAGE, stamp.addon_image(addon))
        self.assertRegex(infra.IMAGE, r"^ghcr\.io/pyvvo/funcd-opentofu@sha256:[0-9a-f]{64}$", "pinned by digest")

    def test_the_release_stamps_the_addons_image(self):
        out = self.dir / "funcdctl-infra"
        addon = self.dir / "addon.yaml"
        addon.write_text("spec:\n  runtimes:\n    - name: opentofu1\n      image: ghcr.io/pyvvo/funcd-opentofu@sha256:"
                         + "a" * 64 + "\n    - name: infrastack\n", encoding="utf-8")
        stamp.main(str(addon), str(HERE / "funcdctl-infra"), str(out))
        text = out.read_text(encoding="utf-8")
        self.assertIn('IMAGE = "ghcr.io/pyvvo/funcd-opentofu@sha256:' + "a" * 64 + '"', text)
        self.assertEqual(text.count("IMAGE = "), 1)
        self.assertTrue(out.stat().st_mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
