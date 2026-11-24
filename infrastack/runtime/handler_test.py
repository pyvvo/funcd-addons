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
    if args[0] == "show":
        print(json.dumps({"resource_changes": [
            {"address": "random_pet.name", "change": {"actions": ["create"]}},
            {"address": "null_resource.marker", "change": {"actions": ["delete", "create"]}},
            {"address": "random_password.token", "change": {"actions": ["no-op"]}}],
            "output_changes": {"name": {"actions": ["create"]}, "token": {"actions": ["no-op"]}}}))
    if args[0] == "output":
        print(json.dumps({"name": {"sensitive": False, "value": "check-pet"},
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

    def test_preview_plans_keeps_the_plan_and_answers_its_summary(self):
        fake = Fake()
        doc = handler.preview(fake, event())
        self.assertEqual((doc["action"], doc["add"], doc["change"], doc["destroy"], doc["replace"]), ("apply", 1, 0, 0, 1))
        self.assertEqual([r["address"] for r in doc["resources"]], ["random_pet.name", "null_resource.marker"])
        self.assertEqual(doc["outputs"], ["name"])
        plan = fake.blobs[("state", "plans/edge-g1-9c2e41aa.tfplan")]
        self.assertEqual(doc["planDigest"], handler._digest(plan))
        init, plan_call = self.calls()[0], self.calls()[1]
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
        env = self.calls()[1]["env"]
        self.assertFalse([k for k in env if k.startswith(("AWS_", "FUNCD_", "INFRASTACK_"))], env)
        self.assertEqual((env["BUNNYNET_API_KEY"], env["TF_VAR_zone"]), ("bunny-key", "z1"))
        self.assertNotIn("lower_output", env, "a key named as an output is no environment variable")
        self.assertNotIn("TF_ENCRYPTION", env, "the state's encryption is off with no passphrase")

    def test_a_passphrase_turns_the_state_and_plan_encryption_on(self):
        handler.preview(Fake(secrets={handler.PASSPHRASE: "sixteen-chars-or-more"}), event())
        enc = self.calls()[1]["env"]["TF_ENCRYPTION"]
        for part in ('key_provider "pbkdf2"', '"sixteen-chars-or-more"', "state {", "plan {"):
            self.assertIn(part, enc)

    def test_a_destroy_plans_with_destroy(self):
        doc = handler.preview(Fake(), event("destroy"))
        self.assertEqual(doc["action"], "destroy")
        self.assertIn("-destroy", self.calls()[1]["args"])

    def test_the_apply_runs_the_approved_plan_and_answers_the_plain_outputs(self):
        fake = Fake()
        doc = handler.preview(fake, event())
        approved = Fake(approval={"approved_by": "ops", "digest": "sha256:x", "preview": doc})
        approved.blobs = fake.blobs
        result = handler.handle(approved, event())
        self.assertEqual(result, {"name": "check-pet"})
        self.assertEqual(approved.written["edge-outputs"], {"token": "s3cret-token", "ports": "[7000]"})
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
        self.assertEqual(handler.handle(fake, event()), {"name": "check-pet"})
        self.assertEqual([c["args"][0] for c in self.calls()], ["init", "plan", "apply", "output"])

    def test_a_destroy_forgets_the_sensitive_outputs(self):
        fake = Fake()
        fake.written = {"edge-outputs": {"token": "s3cret-token", "ports": "[7000]", "other": "x"}}
        self.assertEqual(handler.handle(fake, event("destroy")), {})
        self.assertEqual(fake.written["edge-outputs"], {"other": "x"})

    def test_a_lock_an_interrupted_run_left_is_removed_once(self):
        (self.state / "locked").write_text("6a8b1c3d-0000-4000-8000-000000000001", encoding="utf-8")
        with mock.patch("builtins.print") as said:
            handler.preview(Fake(), event())
        args = [c["args"] for c in self.calls()]
        self.assertEqual([a[0] for a in args], ["init", "plan", "force-unlock", "plan", "show"])
        self.assertEqual(args[2], ["force-unlock", "-force", "6a8b1c3d-0000-4000-8000-000000000001"])
        self.assertTrue(any("which an interrupted run left" in str(c) for c in said.call_args_list))

    def test_a_run_that_cannot_go_on_says_why(self):
        cases = {
            "neither apply nor destroy": (Fake(), {"data": {"action": "import"}}),
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
