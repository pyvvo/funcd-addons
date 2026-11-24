"""The OpenTofu runtime's handler (funcd ADR-0344, ADR-0348, ADR-0355).

A run of a stack's Job plans the Job's module in the run's preview, keeps the saved plan beside the stack's state, and
answers the plan's summary, whose digest pins the plan; once a person approved that preview, the run applies that saved
plan and nothing else. A Job with ``approval.bypass`` plans and applies in its one call. A ``plan`` run keeps no saved
plan, applies nothing and answers what would change (funcd ADR-0355 Decision 5).

The Job's artifact is the module: funcd mounts it read-only, and ``entry.py`` names its folder in
``INFRASTACK_MODULE_DIR``. OpenTofu installs the providers from the module's own ``.funcd-providers`` when it holds one,
as ``funcdctl infra push`` packs it, else from the image's (funcd ADR-0355 Decision 4). The state lives in the Job's Bucket prefix through funcd's S3 gateway with the Job's own key
pair (funcd ADR-0351), which OpenTofu reads from a credentials file of its own, so no provider sees it. A lock that an
interrupted run left is removed before the run goes on: a Job runs one run at a time, so no other run holds it.

The run's input is ``{"action": "apply" | "destroy" | "plan", "variables": {...}}``, and the module's own variables
and state files never reach a run (funcd ADR-0355 Decision 3). The Job's bound ConfigMap gives
``INFRASTACK_STATE_BUCKET`` and ``INFRASTACK_STATE_PREFIX``, the Bucket and the prefix of its blob binding ``state``,
and may give ``INFRASTACK_OUTPUTS_SECRET``, the Secret it owns that gets every output of an apply, one key each (funcd
ADR-0356). Its bound Secrets give OpenTofu its environment, each key in upper case or named ``TF_VAR_<variable>`` but
none named as one of the module's outputs, and may give ``INFRASTACK_STATE_PASSPHRASE``, which turns OpenTofu's state
and plan encryption on.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

TOFU = os.environ.get("INFRASTACK_TOFU", "/opt/tofu/bin/tofu")
PLUGINS = os.environ.get("INFRASTACK_PLUGINS", "/opt/tofu/plugins")
WORK = Path(os.environ.get("INFRASTACK_WORK", "/tmp/infrastack"))

STATE_BINDING = "state"
PASSPHRASE = "INFRASTACK_STATE_PASSPHRASE"
ACTIONS = ("apply", "destroy", "plan")
MAX_RESOURCES = 100
# The module's own provider folder, which funcdctl infra push fills for the bundle's platform (funcd ADR-0355 Decision 4).
PROVIDERS = ".funcd-providers"
# Files OpenTofu would load over a run's variables, or that hold a state, left out of the work copy (Decision 3).
LOCAL_FILES = ("terraform.tfstate", "terraform.tfstate.backup", "terraform.tfvars", "terraform.tfvars.json")
LOCAL_SUFFIXES = (".auto.tfvars", ".auto.tfvars.json")

ENV_KEY = re.compile(r"^(?:[A-Z][A-Z0-9_]*|TF_VAR_[A-Za-z0-9_]+)$")
# An output's name, its key in the outputs Secret: an env-var name, as a Secret key's rule has it (funcd ADR-0356
# Decision 4).
OUTPUT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# What an output's name may not start with: FUNCD_, which no reader loads (funcd ADR-0327 Decision 7), and INFRASTACK_,
# the prefix of the runtime's own settings.
OUTPUT_PREFIXES = ("FUNCD_", "INFRASTACK_")
LOCK_ID = re.compile(r"Lock Info:.*?ID:\s+(\S+)", re.DOTALL)
# Never handed to OpenTofu: the Job's state key pair goes in a credentials file of its own, and funcd's own variables
# stay the shim's.
WITHHELD = re.compile(r"^(?:AWS_|FUNCD_|INFRASTACK_)")


class StackError(RuntimeError):
    """A run that cannot go on: its message says why, and the run fails with it."""


def check_outputs(names: list[str]) -> None:
    """Refuse a module with an output that cannot be a key of its outputs Secret, naming the first by name (funcd
    ADR-0356 Decision 4)."""
    for name in sorted(names):
        if not OUTPUT_KEY.match(name):
            raise StackError(f"the module's output {name} is no env-var name, which an output's key must be: rename it")
        prefix = next((p for p in OUTPUT_PREFIXES if name.startswith(p)), None)
        if prefix is not None:
            raise StackError(f"the module's output {name} starts with {prefix}, which an output's key may not: rename it")


def write_outputs(context: Any, secret: str, outputs: dict[str, Any]) -> None:
    """Write each output into the Secret ``secret``, one key each, a string as it is and any other value as its JSON
    (funcd ADR-0356 Decision 2). A write that funcd refuses fails the run, naming the output and the Secret, never the
    value (Decision 3)."""
    for name in sorted(outputs):
        value = outputs[name]
        try:
            context.secret.put(secret, name, value if isinstance(value, str) else json.dumps(value))
        except Exception as err:  # noqa: BLE001 - any refusal fails the run, which names no value
            raise StackError(f"the output {name} could not be written to the Secret {secret}: {err}") from None


def preview(context: Any, event: dict[str, Any]) -> dict[str, Any]:
    """Plan the run's action, keep the saved plan beside the state, unless the action is plan, and answer its summary
    (funcd ADR-0348)."""
    run = Run(context, event)
    plan = run.plan()
    if run.action != "plan":
        run.keep(plan)
    return run.summary(plan)


def handle(context: Any, event: dict[str, Any]) -> dict[str, Any]:
    """Apply the saved plan of the approved preview, or, with no approval, plan and apply; answer ``{"outputs": {...}}``,
    the plain outputs, as funcd ADR-0355 Decision 3 has the runtime answer an apply, and ``{}`` for a destroy. A plan
    run plans again, applies nothing, writes no output and answers ``{"plan": {...}}`` (Decision 5)."""
    run = Run(context, event)
    if run.action == "plan":
        run.plan()
        return {"plan": plan_finding(run.shown_plan())}
    approval = getattr(context, "approval", None)
    if approval is None:
        run.plan()
    else:
        run.approved(approval)
    return run.apply()


def plan_finding(shown: dict[str, Any]) -> dict[str, Any]:
    """A plan run's finding (funcd ADR-0355 Decision 5): how many resources the plan adds, changes, destroys and
    replaces, counted as a preview's summary counts them, and the addresses of the first MAX_RESOURCES of them."""
    counts, changed = _changes(shown)
    return {**counts, "resources": [rc["address"] for rc in changed[:MAX_RESOURCES]]}


class Run:
    """One call of a stack's run: its module copied into a work folder, its backend and its environment."""

    def __init__(self, context: Any, event: dict[str, Any]) -> None:
        self.context = context
        data = event.get("data") or {}
        if not isinstance(data, dict):
            raise StackError("the run's input is not an object of action and variables")
        self.action = data.get("action", "apply")
        if self.action not in ACTIONS:
            raise StackError(f"the run's action {self.action!r} is none of apply, destroy and plan")
        self.variables = data.get("variables") or {}
        if not isinstance(self.variables, dict):
            raise StackError("the run's variables are not an object")
        self.name = context.run["name"]
        self.bucket = self._config("INFRASTACK_STATE_BUCKET")
        self.prefix = self._config("INFRASTACK_STATE_PREFIX").strip("/")
        self.outputs_secret = context.config.get("INFRASTACK_OUTPUTS_SECRET")
        self.module = Path(os.environ.get("INFRASTACK_MODULE_DIR", "/var/funcd/artifact"))
        self.work = WORK / "module"
        self._copy()
        self.env = self._base_env()
        self.module_outputs = self._module_outputs()
        if self.action != "destroy":  # a destroy empties only the keys its last apply wrote (funcd ADR-0356 Decision 4)
            check_outputs(self.module_outputs)
        self.env = self._env()
        self._init()

    @property
    def plan_key(self) -> str:
        """The saved plan's key under the state binding's prefix."""
        return f"plans/{self.name}.tfplan"

    def plan(self) -> bytes:
        """Plan the run's action into a saved plan and return its bytes."""
        args = ["plan", "-input=false", "-no-color", "-lock-timeout=0s", "-out=plan.tfplan", "-var-file=run.tfvars.json"]
        if self.action == "destroy":
            args.append("-destroy")
        self.tofu(*args)
        return (self.work / "plan.tfplan").read_bytes()

    def keep(self, plan: bytes) -> None:
        """Keep the saved plan beside the state, where the run's apply call reads it."""
        self.context.blob.put(STATE_BINDING, self.plan_key, plan)

    def shown_plan(self) -> dict[str, Any]:
        """The saved plan in the work folder, as ``tofu show -json`` reads it."""
        return json.loads(self.tofu("show", "-json", "-no-color", "plan.tfplan", echo=False))

    def summary(self, plan: bytes) -> dict[str, Any]:
        """The preview's document: what the plan changes, by address, and the digest that pins the plan."""
        shown = self.shown_plan()
        counts, changed = _changes(shown)
        resources = [{"address": rc["address"], "actions": rc["actions"]} for rc in changed]
        outputs = sorted(name for name, oc in (shown.get("output_changes") or {}).items() if _kind(oc.get("actions") or []))
        doc: dict[str, Any] = {"action": self.action, **counts, "resources": resources[:MAX_RESOURCES]}
        if len(resources) > MAX_RESOURCES:
            doc["more"] = len(resources) - MAX_RESOURCES
        doc["outputs"] = outputs
        doc["planDigest"] = _digest(plan)
        return doc

    def approved(self, approval: dict[str, Any]) -> None:
        """Put the saved plan of the approved preview in the work folder, checked against the digest its document
        names."""
        want = (approval.get("preview") or {}).get("planDigest")
        plan = self.context.blob.get(STATE_BINDING, self.plan_key)
        if plan is None:
            raise StackError(f"no saved plan of run {self.name} at {self.plan_key}: preview the run again")
        if want != _digest(plan):
            raise StackError(f"the saved plan of run {self.name} is {_digest(plan)}, not {want}, the approved one")
        (self.work / "plan.tfplan").write_bytes(plan)

    def apply(self) -> dict[str, Any]:
        """Apply the saved plan in the work folder, write every output into the outputs Secret, and answer the plain
        ones, only after the last write (funcd ADR-0356 Decision 2); a destroy empties the keys of the outputs it read
        before it and answers none (Decision 5)."""
        before = self.outputs() if self.action == "destroy" else {}
        self.tofu("apply", "-input=false", "-no-color", "-lock-timeout=0s", "plan.tfplan")
        try:
            self.context.blob.delete(STATE_BINDING, self.plan_key)
        except Exception as err:  # noqa: BLE001 - a plan left behind is overwritten by the next preview
            print(f"infrastack: could not remove the saved plan {self.plan_key}: {err}")
        if self.action == "destroy":
            self._forget(sorted(before))
            return {}
        outputs = self.outputs()
        plain = {name: out.get("value") for name, out in outputs.items() if not out.get("sensitive")}
        if self.outputs_secret:
            write_outputs(self.context, self.outputs_secret, {name: out.get("value") for name, out in outputs.items()})
        else:
            for name in sorted(n for n, out in outputs.items() if out.get("sensitive")):
                print(f"infrastack: the sensitive output {name} is kept nowhere: the Job names no INFRASTACK_OUTPUTS_SECRET")
        return {"outputs": plain}

    def outputs(self) -> dict[str, Any]:
        """The state's outputs, as ``tofu output -json`` reads them."""
        return json.loads(self.tofu("output", "-json", "-no-color", echo=False) or "{}")

    def tofu(self, *args: str, echo: bool = True) -> str:
        """Run OpenTofu in the work folder and return its standard output. A state lock that an interrupted run left
        is removed, once, and the command runs again."""
        out = self._run(args, echo)
        if out.returncode != 0 and "Error acquiring the state lock" in out.stderr:
            lock = LOCK_ID.search(out.stderr)
            if lock is None:
                raise StackError(f"the state is locked and its lock names no ID:\n{_tail(out.stderr)}")
            print(f"infrastack: the state of {self.prefix} is locked by {lock.group(1)}, which an interrupted run "
                  "left: unlocking it")
            unlock = self._run(("force-unlock", "-force", lock.group(1)), True)
            if unlock.returncode != 0:
                raise StackError(f"tofu force-unlock failed:\n{_tail(unlock.stderr)}")
            out = self._run(args, echo)
        if out.returncode != 0:
            raise StackError(f"tofu {args[0]} failed:\n{_tail(out.stderr or out.stdout)}")
        return out.stdout

    def _run(self, args: tuple[str, ...], echo: bool) -> subprocess.CompletedProcess[str]:
        remaining = self.context.remaining_ms() if hasattr(self.context, "remaining_ms") else None
        timeout = remaining / 1000 if remaining else None
        try:
            out = subprocess.run(  # noqa: S603 - a fixed binary, with arguments this handler builds
                [TOFU, *args], cwd=self.work, env=self.env, capture_output=True, text=True, timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as err:
            raise StackError(f"tofu {args[0]} did not end before the Job's timeout") from err
        if echo:
            for stream in (out.stdout, out.stderr):
                if stream.strip():
                    print(stream.rstrip())
        return out

    def _config(self, key: str) -> str:
        value = self.context.config.get(key)
        if not value:
            raise StackError(f"the Job binds no ConfigMap that sets {key}")
        return value

    def _base_env(self) -> dict[str, str]:
        """The worker's own environment without the state keys and funcd's variables, for OpenTofu in automation."""
        env = {k: v for k, v in os.environ.items() if not WITHHELD.match(k)}
        env.update({"TF_IN_AUTOMATION": "1", "TF_INPUT": "0", "CHECKPOINT_DISABLE": "1", "HOME": str(WORK)})
        return env

    def _env(self) -> dict[str, str]:
        """OpenTofu's environment: the base one, the bound Secrets' keys in upper case or named TF_VAR_<variable> but
        none named as one of the module's outputs, so no output feeds a run (funcd ADR-0356 Decision 7), and the
        encryption the passphrase turns on."""
        env = self._base_env()
        secrets = self.context.secret
        for key in secrets.list():
            if ENV_KEY.match(key) and not WITHHELD.match(key) and key not in self.module_outputs:
                value = secrets.get(key)
                if value is not None:
                    env[key] = value
        passphrase = secrets.get(PASSPHRASE)
        if passphrase:
            env["TF_ENCRYPTION"] = _encryption(passphrase)
        return env

    def _copy(self) -> None:
        """A fresh copy of the module, the backend override and the run's variables."""
        if not self.module.is_dir():
            raise StackError(f"the Job's module folder {self.module} does not exist")
        shutil.rmtree(WORK, ignore_errors=True)
        shutil.copytree(self.module, self.work, ignore=_not_module)
        (self.work / "zz_infrastack_override.tf").write_text('terraform {\n  backend "s3" {}\n}\n', encoding="utf-8")
        (self.work / "run.tfvars.json").write_text(json.dumps(self.variables), encoding="utf-8")

    def _module_outputs(self) -> list[str]:
        """The names of the module's outputs, from its configuration (``tofu show -json -module``)."""
        shown = json.loads(self.tofu("show", "-json", "-no-color", "-module=.", echo=False) or "{}")
        return sorted((shown.get("root_module") or {}).get("outputs") or {})

    def _init(self) -> None:
        """The state's credentials and backend, and ``tofu init`` against them."""
        key_id, secret = os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
        endpoint = os.environ.get("AWS_ENDPOINT_URL_S3")
        if not key_id or not secret or not endpoint:
            raise StackError("the Job has no S3 key pair: it binds no Bucket prefix, or funcd's S3 gateway is off")
        credentials = WORK / "state-credentials"
        credentials.write_text(f"[funcd-state]\naws_access_key_id = {key_id}\naws_secret_access_key = {secret}\n",
                               encoding="utf-8")
        credentials.chmod(0o600)
        backend = {
            "bucket": self.bucket,
            "key": f"{self.prefix}/terraform.tfstate",
            "region": os.environ.get("AWS_REGION", "us-east-1"),
            "profile": "funcd-state",
            "shared_credentials_files": [str(credentials)],
            "use_path_style": True,
            "use_lockfile": True,
            "skip_credentials_validation": True,
            "skip_requesting_account_id": True,
            "skip_metadata_api_check": True,
            "skip_region_validation": True,
            "skip_s3_checksum": True,
        }
        lines = [f"{k} = {json.dumps(v)}" for k, v in backend.items()]
        lines.append(f"endpoints = {{\n  s3 = {json.dumps(endpoint)}\n}}")
        (WORK / "backend.hcl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        own = self.module / PROVIDERS
        plugins = own if own.is_dir() else PLUGINS
        self.tofu("init", "-input=false", "-no-color", f"-plugin-dir={plugins}", f"-backend-config={WORK / 'backend.hcl'}",
                  "-reconfigure")

    def _forget(self, names: list[str]) -> None:
        """Empty the destroyed outputs' keys and keep them, so no reader waits for a key again (funcd ADR-0356 Decision
        5); a refused emptying is logged by the output's name and the run goes on."""
        if not self.outputs_secret:
            return
        for name in names:
            try:
                self.context.secret.put(self.outputs_secret, name, "")
            except Exception as err:  # noqa: BLE001 - a key left behind names an output that is gone
                print(f"infrastack: could not empty the output {name} in {self.outputs_secret}: {err}")


def _not_module(_folder: str, names: list[str]) -> list[str]:
    """The files of a Job's artifact that are funcd's, not the module's, a stale .terraform folder, and the files that
    would set a run's variables or hold a state besides its own (funcd ADR-0355 Decision 3)."""
    return [n for n in names if n.startswith(".funcd") or n in ("__funcd_contract.json", ".terraform", *LOCAL_FILES)
            or n.endswith(LOCAL_SUFFIXES)]


def _changes(shown: dict[str, Any]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """A plan's counts of what it adds, changes, destroys and replaces, and its changed resources, each with its address
    and actions, in the plan's order."""
    counts = {"add": 0, "change": 0, "destroy": 0, "replace": 0}
    changed = []
    for rc in shown.get("resource_changes") or []:
        actions = (rc.get("change") or {}).get("actions") or []
        kind = _kind(actions)
        if kind is None:
            continue
        counts[kind] += 1
        changed.append({"address": rc.get("address", ""), "actions": actions})
    return counts, changed


def _kind(actions: list[str]) -> str | None:
    """What a change's actions do to a resource or an output: add, change, destroy, replace, or nothing."""
    if "delete" in actions and "create" in actions:
        return "replace"
    for action, kind in (("create", "add"), ("update", "change"), ("delete", "destroy")):
        if action in actions:
            return kind
    return None


def _digest(plan: bytes) -> str:
    return "sha256:" + hashlib.sha256(plan).hexdigest()


def _encryption(passphrase: str) -> str:
    """OpenTofu's encryption of the state and the saved plans, with a key derived from the passphrase."""
    return (
        f'key_provider "pbkdf2" "infrastack" {{\n  passphrase = {json.dumps(passphrase)}\n}}\n'
        'method "aes_gcm" "infrastack" {\n  keys = key_provider.pbkdf2.infrastack\n}\n'
        "state {\n  method = method.aes_gcm.infrastack\n}\n"
        "plan {\n  method = method.aes_gcm.infrastack\n}\n"
    )


def _tail(text: str, lines: int = 30) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])
