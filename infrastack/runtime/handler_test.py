"""The OpenTofu runtime's handler, with a stand-in for OpenTofu that records each command and the environment it got.
image_test.sh runs the handler with OpenTofu itself, in the image."""

import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import handler  # noqa: E402 - the handler beside this test

FAKE_TOFU = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    state = os.environ["FAKE_STATE"]
    args = sys.argv[1:]
    seen = {k: v for k, v in os.environ.items() if k.startswith(("AWS_", "FUNCD_", "INFRASTACK_", "TF_", "BUNNY", "lower"))}
    with open(os.path.join(state, "calls.jsonl"), "a") as f:
        f.write(json.dumps({"args": args, "env": seen}) + "\\n")
    locked = os.path.join(state, "locked")
    if args[0] in ("plan", "apply") and os.path.exists(locked):
        sys.stderr.write("Error: Error acquiring the state lock\\n\\nLock Info:\\n  ID:        " + open(locked).read()
                         + "\\n  Operation: OperationTypeApply\\n")
        sys.exit(1)
    if args[0] == "force-unlock":
        os.remove(locked)
    if args[0] == "plan":
        open("plan.tfplan", "wb").write(b"a saved plan of " + " ".join(args).encode())
    if args[0] == "show" and "-module=." in args:
        module = os.path.join(state, "module.json")
        outputs = json.load(open(module)) if os.path.exists(module) else {
            "name": {}, "token": {"sensitive": True}, "ports": {"sensitive": True}}
        print(json.dumps({"root_module": {"outputs": outputs, "variables": {"prefix": {"required": True}}}}))
    elif args[0] == "show":
        print(json.dumps({"resource_changes": [
            {"address": "random_pet.name", "change": {"actions": ["create"]}},
            {"address": "null_resource.marker", "change": {"actions": ["delete", "create"]}},
            {"address": "random_password.token", "change": {"actions": ["no-op"]}}],
            "output_changes": {"name": {"actions": ["create"]}, "token": {"actions": ["no-op"]}}}))
    if args[0] == "output":
        applied = os.path.join(state, "outputs.json")
        print(open(applied).read() if os.path.exists(applied) else json.dumps({
            "name": {"sensitive": False, "value": "check-pet"},
            "token": {"sensitive": True, "value": "s3cret-token"},
            "ports": {"sensitive": True, "value": [7000]}}))
    """)


class Fake:
    """A Job worker's context: its run, approval, state binding, Secrets and ConfigMap keys."""

    def __init__(self, approval=None, secrets=None, config=None):
        self.run = {"name": "edge-g1-9c2e41aa", "job": "edge", "revision": "edge-1", "phase": "run", "started_by": ""}
        self.approval = approval
        self.blobs = {}
        self.written = {}
        self.events = []
        self.refuse = None
        self.secrets = {"BUNNYNET_API_KEY": "bunny-key", "TF_VAR_zone": "z1", "lower_output": "old",
                        "INFRASTACK_X": "kept"} if secrets is None else secrets
        self.values = {"INFRASTACK_STATE_BUCKET": "stacks", "INFRASTACK_STATE_PREFIX": "edge",
                       "INFRASTACK_OUTPUTS_SECRET": "edge-outputs"} if config is None else config
        self.blob, self.secret, self.config = self, self._Secret(self), self._Config(self)

    def put(self, binding, key, data):
        self.blobs[(binding, key)] = data

    def get(self, binding, key):
        return self.blobs.get((binding, key))

    def delete(self, binding, key):
        self.blobs.pop((binding, key), None)

    def remaining_ms(self):
        return None

    class _Secret:
        def __init__(self, fake):
            self.fake = fake

        def get(self, key):
            return self.fake.secrets.get(key)

        def list(self):
            return sorted(self.fake.secrets)

        def put(self, binding, key, value):
            if self.fake.refuse:
                raise RuntimeError(self.fake.refuse)
            self.fake.events.append(("put", binding, key))
            self.fake.written.setdefault(binding, {})[key] = value

        def delete(self, binding, key):
            self.fake.written.get(binding, {}).pop(key, None)

    class _Config:
        def __init__(self, fake):
            self.fake = fake

        def get(self, key):
            return self.fake.values.get(key)


def event(action="apply"):
    return {"type": "funcd.job.run", "data": {"action": action, "variables": {"prefix": "check"}}}


class HandlerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.state = self.dir / "fake"
        self.state.mkdir()
        tofu = self.dir / "tofu"
        tofu.write_text(FAKE_TOFU, encoding="utf-8")
        tofu.chmod(tofu.stat().st_mode | stat.S_IXUSR)
        module = self.dir / "module"
        module.mkdir()
        (module / "main.tf").write_text("# the module\n", encoding="utf-8")
        (module / ".terraform.lock.hcl").write_text("# its lock file\n", encoding="utf-8")
        (module / "__funcd_contract.json").write_text("{}", encoding="utf-8")
        (module / ".funcd-entry").write_text("main.tf", encoding="utf-8")
        env = {"FAKE_STATE": str(self.state), "INFRASTACK_MODULE_DIR": str(module), "AWS_ACCESS_KEY_ID": "JKEY",
               "AWS_SECRET_ACCESS_KEY": "jsecret", "AWS_REGION": "us-east-1", "AWS_ENDPOINT_URL_S3": "http://10.0.0.1:8333",
               "FUNCD_INVOKE_SOCKET": "/run/funcd.sock", "PATH": os.environ.get("PATH", "")}
        for patch in (mock.patch.dict(os.environ, env, clear=True), mock.patch.object(handler, "TOFU", str(tofu)),
                      mock.patch.object(handler, "WORK", self.dir / "work")):
            patch.start()
            self.addCleanup(patch.stop)

    def calls(self):
        lines = (self.state / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def call(self, command):
        """The first call of the OpenTofu command."""
        return next(c for c in self.calls() if c["args"][0] == command and "-module=." not in c["args"])

    def module_outputs(self, **outputs):
        """The module's outputs, as its configuration names them."""
        (self.state / "module.json").write_text(json.dumps(outputs), encoding="utf-8")

    def test_preview_plans_keeps_the_plan_and_answers_its_summary(self):
        fake = Fake()
        doc = handler.preview(fake, event())
        self.assertEqual((doc["action"], doc["add"], doc["change"], doc["destroy"], doc["replace"]), ("apply", 1, 0, 0, 1))
        self.assertEqual([r["address"] for r in doc["resources"]], ["random_pet.name", "null_resource.marker"])
        self.assertEqual(doc["outputs"], ["name"])
        plan = fake.blobs[("state", "plans/edge-g1-9c2e41aa.tfplan")]
        self.assertEqual(doc["planDigest"], handler._digest(plan))
        show, init, plan_call = self.calls()[0], self.calls()[1], self.calls()[2]
        self.assertEqual(show["args"], ["show", "-json", "-no-color", "-module=."], "the module's outputs come first")
        self.assertEqual(init["args"][0], "init")
        self.assertIn("-reconfigure", init["args"])
        self.assertEqual(plan_call["args"][0], "plan")
        self.assertNotIn("-destroy", plan_call["args"])
        backend = (self.dir / "work" / "backend.hcl").read_text(encoding="utf-8")
        for line in ('bucket = "stacks"', 'key = "edge/terraform.tfstate"', 'profile = "funcd-state"', "use_lockfile = true",
                     "use_path_style = true", 's3 = "http://10.0.0.1:8333"'):
            self.assertIn(line, backend)
        creds = self.dir / "work" / "state-credentials"
        self.assertEqual(stat.S_IMODE(creds.stat().st_mode), 0o600)
        self.assertIn("aws_access_key_id = JKEY", creds.read_text(encoding="utf-8"))
        work = self.dir / "work" / "module"
        self.assertTrue((work / ".terraform.lock.hcl").exists(), "the module's lock file is kept")
        self.assertFalse((work / "__funcd_contract.json").exists() or (work / ".funcd-entry").exists())
        self.assertIn('backend "s3"', (work / "zz_infrastack_override.tf").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((work / "run.tfvars.json").read_text(encoding="utf-8")), {"prefix": "check"})

    def test_tofu_never_sees_the_state_keys_and_gets_the_stack_secrets(self):
        handler.preview(Fake(), event())
        env = self.call("plan")["env"]
        self.assertFalse([k for k in env if k.startswith(("AWS_", "FUNCD_", "INFRASTACK_"))], env)
        self.assertEqual((env["BUNNYNET_API_KEY"], env["TF_VAR_zone"]), ("bunny-key", "z1"))
        self.assertNotIn("lower_output", env, "a key named as an output is no environment variable")
        self.assertNotIn("TF_ENCRYPTION", env, "the state's encryption is off with no passphrase")

    def test_a_passphrase_turns_the_state_and_plan_encryption_on(self):
        handler.preview(Fake(secrets={handler.PASSPHRASE: "sixteen-chars-or-more"}), event())
        enc = self.call("plan")["env"]["TF_ENCRYPTION"]
        for part in ('key_provider "pbkdf2"', '"sixteen-chars-or-more"', "state {", "plan {"):
            self.assertIn(part, enc)

    def test_a_destroy_plans_with_destroy(self):
        doc = handler.preview(Fake(), event("destroy"))
        self.assertEqual(doc["action"], "destroy")
        self.assertIn("-destroy", self.call("plan")["args"])

    def test_the_apply_runs_the_approved_plan_and_answers_the_plain_outputs(self):
        fake = Fake()
        doc = handler.preview(fake, event())
        approved = Fake(approval={"approved_by": "ops", "digest": "sha256:x", "preview": doc})
        approved.blobs = fake.blobs
        result = handler.handle(approved, event())
        self.assertEqual(result, {"outputs": {"name": "check-pet"}})
        self.assertEqual(approved.written["edge-outputs"], {"name": "check-pet", "token": "s3cret-token", "ports": "[7000]"})
        apply = [c for c in self.calls() if c["args"][0] == "apply"]
        self.assertEqual(len(apply), 1)
        self.assertEqual(apply[0]["args"][-1], "plan.tfplan")
        self.assertEqual(len([c for c in self.calls() if c["args"][0] == "plan"]), 1, "the apply plans nothing new")
        self.assertEqual(approved.blobs, {}, "the applied plan is removed")

    def test_the_apply_refuses_a_plan_that_is_not_the_approved_one(self):
        fake = Fake()
        doc = handler.preview(fake, event())
        fake.blobs[("state", "plans/edge-g1-9c2e41aa.tfplan")] = b"another plan"
        approved = Fake(approval={"preview": doc})
        approved.blobs = fake.blobs
        with self.assertRaisesRegex(handler.StackError, "not sha256:.*the approved one"):
            handler.handle(approved, event())
        self.assertFalse([c for c in self.calls() if c["args"][0] == "apply"])

    def test_the_apply_needs_the_saved_plan(self):
        with self.assertRaisesRegex(handler.StackError, "no saved plan of run edge-g1-9c2e41aa"):
            handler.handle(Fake(approval={"preview": {"planDigest": "sha256:x"}}), event())

    def test_a_run_with_no_approval_plans_and_applies(self):
        fake = Fake()
        self.assertEqual(handler.handle(fake, event()), {"outputs": {"name": "check-pet"}})
        self.assertEqual([c["args"][0] for c in self.calls()], ["show", "init", "plan", "apply", "output"])

    def test_a_destroy_empties_every_output_it_read_before_it(self):
        fake = Fake()
        fake.written = {"edge-outputs": {"name": "check-pet", "token": "s3cret-token", "ports": "[7000]", "other": "x"}}
        self.assertEqual(handler.handle(fake, event("destroy")), {})
        self.assertEqual(fake.written["edge-outputs"], {"name": "", "token": "", "ports": "", "other": "x"},
                         "each key it read is kept, empty; another key is left alone")
        self.assertEqual([c["args"][0] for c in self.calls()], ["show", "init", "plan", "output", "apply"],
                         "the outputs are read before the destroy")

    def test_a_refused_emptying_is_logged_by_name_and_the_destroy_goes_on(self):
        fake = Fake()
        fake.refuse = "context.secret.put failed: 403 forbidden"
        with mock.patch("builtins.print") as said:
            self.assertEqual(handler.handle(fake, event("destroy")), {})
        lines = " ".join(str(c) for c in said.call_args_list)
        for name in ("name", "token", "ports"):
            self.assertIn(f"could not empty the output {name} in edge-outputs", lines)
        self.assertNotIn("s3cret-token", lines)

    def test_with_no_outputs_secret_the_plain_outputs_are_the_result_alone(self):
        fake = Fake(config={"INFRASTACK_STATE_BUCKET": "stacks", "INFRASTACK_STATE_PREFIX": "edge"})
        with mock.patch("builtins.print") as said:
            self.assertEqual(handler.handle(fake, event()), {"outputs": {"name": "check-pet"}})
        self.assertEqual(fake.written, {})
        lines = " ".join(str(c) for c in said.call_args_list)
        for name in ("token", "ports"):
            self.assertIn(f"the sensitive output {name} is kept nowhere", lines)
        self.assertNotIn("output name is kept nowhere", lines, "a plain output is in the result")
        self.assertNotIn("s3cret-token", lines)

    # oracle: docs/design/job/stack-outputs.fizz#ReadyStackHasItsOutputs
    def test_apply_writes_every_output_before_it_answers(self):
        """funcd ADR-0356 Decision 2: when an apply answers, so that its run may succeed and its stack turn Ready, every
        output of the state is in the outputs Secret, the plain ones too."""
        fake = Fake()
        result = handler.handle(fake, event())
        self.assertEqual(fake.events, [("put", "edge-outputs", "name"), ("put", "edge-outputs", "ports"),
                                       ("put", "edge-outputs", "token")])
        self.assertEqual(fake.written["edge-outputs"], {"name": "check-pet", "ports": "[7000]", "token": "s3cret-token"})
        self.assertEqual(result, {"outputs": {"name": "check-pet"}})

    # oracle: docs/design/job/stack-outputs.fizz#RunEnds
    def test_apply_answers_after_its_last_write(self):
        """The apply answers once its last write is done, so its run ends; a write that funcd refuses ends the call
        too, failing it, so the run ends either way."""
        fake = Fake()
        steps = []
        put = fake.secret.put
        fake.secret.put = lambda binding, key, value: (steps.append(key), put(binding, key, value))
        steps.append(("answer", handler.handle(fake, event())))
        self.assertEqual(steps, ["name", "ports", "token", ("answer", {"outputs": {"name": "check-pet"}})])
        refused = Fake()
        refused.refuse = "context.secret.put failed: 403 forbidden"
        with self.assertRaises(handler.StackError):
            handler.handle(refused, event())

    # scenario: stack-output-name-refused
    def test_scenario_stack_output_name_refused(self):
        """An output whose name is no env-var name, or starts with FUNCD_ or INFRASTACK_, fails the preview before it
        plans, naming the output; a destroy skips the check."""
        for name in ("pull-zone", "1st", "FUNCD_TOKEN", "INFRASTACK_STATE_PASSPHRASE"):
            with self.subTest(name):
                (self.state / "calls.jsonl").unlink(missing_ok=True)
                self.module_outputs(**{name: {}, "name": {}})
                with self.assertRaisesRegex(handler.StackError, f"the module's output {name} "):
                    handler.preview(Fake(), event())
                self.assertEqual([c["args"][0] for c in self.calls()], ["show"], "nothing is planned or applied")
        self.module_outputs(**{"pull-zone": {}, "name": {}})
        with self.assertRaisesRegex(handler.StackError, "pull-zone"):
            handler.handle(Fake(), event())
        self.assertFalse([c for c in self.calls() if c["args"][0] in ("plan", "apply")], "a bypass run plans nothing")
        self.assertEqual(handler.preview(Fake(), event("destroy"))["action"], "destroy", "a destroy skips the check")

    # scenario: stack-output-write-refused
    def test_scenario_stack_output_write_refused(self):
        """A write that funcd refuses, as for a Job that does not own its outputs Secret, fails the apply's call, naming
        the output and the Secret but no value."""
        fake = Fake()
        fake.refuse = "context.secret.put failed: 403 the Job edge does not own the Secret edge-outputs"
        with self.assertRaises(handler.StackError) as refused:
            handler.handle(fake, event())
        message = str(refused.exception)
        self.assertIn("the output name could not be written to the Secret edge-outputs", message)
        for value in ("check-pet", "s3cret-token", "7000"):
            self.assertNotIn(value, message)
        self.assertTrue([c for c in self.calls() if c["args"][0] == "apply"], "the apply ran: the change stays in the state")

    # scenario: stack-outputs-feed-no-run
    def test_scenario_stack_outputs_feed_no_run(self):
        """An output named TF_VAR_region sits in the outputs Secret that the Job binds; the next run's OpenTofu gets no
        key named as one of the module's outputs, so the plan takes the variable's default."""
        self.module_outputs(TF_VAR_region={}, name={})
        fake = Fake(secrets={"TF_VAR_region": "US", "TF_VAR_zone": "z1", "name": "check-pet"})
        handler.preview(fake, event())
        env = self.call("plan")["env"]
        self.assertNotIn("TF_VAR_region", env, "the output feeds no variable")
        self.assertEqual(env.get("TF_VAR_zone"), "z1", "a Secret's other variables still reach OpenTofu")

    # scenario: infra-plan-run (the handler's half; image_check.py runs it with OpenTofu)
    def test_scenario_infra_plan_run(self):
        """A plan run's preview keeps no saved plan; its run plans again, applies nothing, writes no output and answers
        the plan's finding (funcd ADR-0355 Decision 5)."""
        fake = Fake()
        doc = handler.preview(fake, event("plan"))
        self.assertEqual((doc["action"], doc["add"], doc["replace"]), ("plan", 1, 1))
        self.assertEqual(fake.blobs, {}, "a plan run keeps no saved plan")
        approved = Fake(approval={"approved_by": "ops", "preview": doc})
        result = handler.handle(approved, event("plan"))
        self.assertEqual(result, {"plan": {"add": 1, "change": 0, "destroy": 0, "replace": 1,
                                           "resources": ["random_pet.name", "null_resource.marker"]}})
        commands = [c["args"][0] for c in self.calls()]
        self.assertNotIn("apply", commands, "a plan run applies nothing")
        self.assertNotIn("output", commands)
        self.assertEqual((approved.written, approved.blobs), ({}, {}), "it writes no output and keeps no plan")
        self.assertNotIn("-destroy", self.call("plan")["args"])
        self.assertEqual(handler.handle(Fake(), event("plan"))["plan"]["add"], 1, "a bypass plan run answers it too")

    def test_plan_finding_names_the_first_resources(self):
        shown = {"resource_changes": [{"address": f"null_resource.n[{i}]", "change": {"actions": ["create"]}}
                                      for i in range(handler.MAX_RESOURCES + 5)]
                 + [{"address": "random_pet.name", "change": {"actions": ["no-op"]}}]}
        finding = handler.plan_finding(shown)
        self.assertEqual(finding["add"], handler.MAX_RESOURCES + 5)
        self.assertEqual(len(finding["resources"]), handler.MAX_RESOURCES)
        self.assertEqual(handler.plan_finding({}), {"add": 0, "change": 0, "destroy": 0, "replace": 0, "resources": []})

    def test_the_run_installs_the_modules_own_providers_else_the_images(self):
        """funcd ADR-0355 Decision 4: init takes the module's .funcd-providers when it holds one."""
        handler.preview(Fake(), event())
        self.assertIn(f"-plugin-dir={handler.PLUGINS}", self.call("init")["args"])
        own = Path(os.environ["INFRASTACK_MODULE_DIR"]) / handler.PROVIDERS
        own.mkdir()
        (self.state / "calls.jsonl").unlink()
        handler.preview(Fake(), event())
        self.assertIn(f"-plugin-dir={own}", self.call("init")["args"])
        self.assertFalse((self.dir / "work" / "module" / handler.PROVIDERS).exists(), "the work copy leaves it in the module")

    # scenario: infra-local-files-refused (the run's half; the push's half is plugin_test.py's)
    def test_scenario_infra_local_files_refused(self):
        """A module pushed by hand with its own variables or state files runs as if they were not there: the work copy
        leaves them out, so a run's variables come only from its input, its ConfigMaps' and Secrets' TF_VAR_ keys and
        the module's defaults."""
        module = Path(os.environ["INFRASTACK_MODULE_DIR"])
        (module / "sub").mkdir()
        names = ("terraform.tfvars", "terraform.tfvars.json", "terraform.tfstate", "terraform.tfstate.backup",
                 "prod.auto.tfvars", "prod.auto.tfvars.json", "sub/terraform.tfvars")
        for name in names:
            (module / name).write_text('prefix = "from-the-file"\n', encoding="utf-8")
        (module / "sub" / "main.tf").write_text("# a child\n", encoding="utf-8")
        handler.preview(Fake(), event())
        work = self.dir / "work" / "module"
        for name in names:
            self.assertFalse((work / name).exists(), name)
        self.assertTrue((work / "sub" / "main.tf").exists())
        self.assertEqual(json.loads((work / "run.tfvars.json").read_text(encoding="utf-8")), {"prefix": "check"})

    def test_check_outputs_takes_env_var_names(self):
        handler.check_outputs(["name", "_private", "TF_VAR_region", "pullzone_host", "FRP_TOKEN", "AWS_REGION"])
        for name in ("pull-zone", "", "9lives", "a.b", "FUNCD_X", "INFRASTACK_X"):
            with self.subTest(name), self.assertRaises(handler.StackError):
                handler.check_outputs([name])

    def test_write_outputs_writes_a_string_as_it_is_and_any_other_value_as_its_json(self):
        fake = Fake()
        handler.write_outputs(fake, "edge-outputs", {"host": "a.b-cdn.net", "port": 7000, "on": True, "tags": ["x"], "none": None})
        self.assertEqual(fake.written["edge-outputs"],
                         {"host": "a.b-cdn.net", "port": "7000", "on": "true", "tags": '["x"]', "none": "null"})

    def test_a_lock_an_interrupted_run_left_is_removed_once(self):
        (self.state / "locked").write_text("6a8b1c3d-0000-4000-8000-000000000001", encoding="utf-8")
        with mock.patch("builtins.print") as said:
            handler.preview(Fake(), event())
        args = [c["args"] for c in self.calls()]
        self.assertEqual([a[0] for a in args], ["show", "init", "plan", "force-unlock", "plan", "show"])
        self.assertEqual(args[3], ["force-unlock", "-force", "6a8b1c3d-0000-4000-8000-000000000001"])
        self.assertTrue(any("which an interrupted run left" in str(c) for c in said.call_args_list))

    def test_a_run_whose_stack_sets_no_variables_has_none(self):
        """funcd ADR-0355 Decision 3, amended 2026-11-24: void variables, absent or null, are no variables."""
        for data in ({"action": "apply"}, {"action": "apply", "variables": None}):
            with self.subTest(data=data):
                self.assertEqual(handler.Run(Fake(), {"data": data}).variables, {})

    def test_a_run_that_cannot_go_on_says_why(self):
        cases = {
            "none of apply, destroy and plan": (Fake(), {"data": {"action": "import"}}),
            "variables are not an object": (Fake(), {"data": {"action": "apply", "variables": [1]}}),
            "INFRASTACK_STATE_BUCKET": (Fake(config={"INFRASTACK_STATE_PREFIX": "edge"}), event()),
        }
        for want, (fake, ev) in cases.items():
            with self.subTest(want), self.assertRaisesRegex(handler.StackError, want):
                handler.preview(fake, ev)
        with mock.patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": ""}), \
                self.assertRaisesRegex(handler.StackError, "no S3 key pair"):
            handler.preview(Fake(), event())


if __name__ == "__main__":
    unittest.main()
