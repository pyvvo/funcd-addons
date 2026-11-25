"""The OpenTofu runtime image's check: inside the image, the runtime's own handler plans, applies and destroys the
example random-stack module against an S3 server, as a stack's runs do, with a context that stands in for funcd's.
It also runs a module that carries its own providers, as funcdctl infra push packs them (funcd ADR-0355 Decision 4),
one whose provider package matches no hash of its lock file, and a plan run (Decision 5). And the image's shim reads a
run's approval from funcd's local API at FUNCD_INVOKE_URL, as a worker does under funcdctl dev (funcd ADR-0346).

image_test.sh runs it on an internal Docker network that reaches only versitygw, the S3 engine of funcd's gateway, so a
run that would download anything fails. versitygw serves the bucket from a folder that this check also reads, so a
saved plan sits beside the state, and a stale lock is planted there as an interrupted run leaves one.
"""

import datetime
import hashlib
import hmac
import http.server
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, "/opt/infrastack")

import handler  # noqa: E402 - the image's handler, on the path set above
from funcd_shim.job import read_approval  # noqa: E402 - the image's shim

BUCKET = os.environ["CHECK_BUCKET"]
PREFIX = "random-stack"
DATA = Path(os.environ["CHECK_DATA"]) / BUCKET


class Blob:
    """The state binding's prefix, as the S3 server's folder holds it."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def put(self, binding: str, key: str, data: bytes) -> None:
        assert binding == handler.STATE_BINDING, binding
        path = DATA / self.prefix / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, binding: str, key: str) -> bytes | None:
        path = DATA / self.prefix / key
        return path.read_bytes() if path.exists() else None

    def delete(self, binding: str, key: str) -> None:
        (DATA / self.prefix / key).unlink(missing_ok=True)


class Secret:
    """The Job's bound Secrets: one the outputs go to, and the stack's own."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)
        self.written: dict[str, dict[str, str]] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def list(self) -> list[str]:
        return sorted(self.values)

    def put(self, binding: str, key: str, value: str) -> None:
        self.written.setdefault(binding, {})[key] = value

    def delete(self, binding: str, key: str) -> None:
        self.written.get(binding, {}).pop(key, None)


class Config:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, key: str) -> str | None:
        return self.values.get(key)


class Context:
    def __init__(self, run: str, secret: Secret, approval: dict | None = None, prefix: str = PREFIX) -> None:
        self.run = {"name": run, "job": "random-stack", "revision": "random-stack-1", "phase": "run", "started_by": ""}
        self.approval = approval
        self.blob = Blob(prefix)
        self.secret = secret
        self.config = Config({"INFRASTACK_STATE_BUCKET": BUCKET, "INFRASTACK_STATE_PREFIX": prefix,
                              "INFRASTACK_OUTPUTS_SECRET": "random-stack-outputs"})

    def remaining_ms(self) -> None:
        return None


def event(action: str, prefix: str = "check") -> dict:
    return {"type": "funcd.job.run", "data": {"action": action, "variables": {"prefix": prefix}}}


def run(name: str, action: str, secret: Secret) -> tuple[dict, dict]:
    """A run of the Job with approval: its preview, then its apply of the approved preview."""
    doc = handler.preview(Context(name, secret), event(action))
    approval = {"approved_by": "check", "digest": "sha256:" + "0" * 64, "preview": doc}
    return doc, handler.handle(Context(name, secret, approval), event(action))


class LocalAPI(http.server.BaseHTTPRequestHandler):
    """funcd's local API of a worker in a container: a run's approval, for the worker's token only."""

    approval = {"approvedBy": "check", "digest": "sha256:" + "0" * 64, "preview": {"add": 1}}

    def do_GET(self) -> None:  # noqa: N802 - the base class's name
        ok = self.path == "/job/run" and self.headers.get("X-Funcd-Invoke-Token") == "check-token"
        self.send_response(200 if ok else 403)
        self.end_headers()
        self.wfile.write(json.dumps({"approval": self.approval} if ok else {"detail": "refused"}).encode())

    def log_message(self, *args: object) -> None:
        """Keep the server's request lines out of the check's output."""


def dev_approval() -> dict | None:
    """The approval that the image's shim reads from a local API at FUNCD_INVOKE_URL, with no socket."""
    server = http.server.HTTPServer(("127.0.0.1", 0), LocalAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ.update(FUNCD_INVOKE_URL=f"http://127.0.0.1:{server.server_port}", FUNCD_INVOKE_TOKEN="check-token")
    try:
        return read_approval()
    finally:
        del os.environ["FUNCD_INVOKE_URL"], os.environ["FUNCD_INVOKE_TOKEN"]
        server.shutdown()


def create_bucket() -> None:
    """Create the check's bucket with an S3 CreateBucket, signed as the AWS SDK signs one (SigV4)."""
    endpoint, region = os.environ["AWS_ENDPOINT_URL_S3"], os.environ["AWS_REGION"]
    key, secret = os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"]
    now = datetime.datetime.now(datetime.UTC)
    amzdate, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    host = endpoint.split("://", 1)[1]
    empty = hashlib.sha256(b"").hexdigest()
    headers = f"host:{host}\nx-amz-content-sha256:{empty}\nx-amz-date:{amzdate}\n"
    signed = "host;x-amz-content-sha256;x-amz-date"
    request = f"PUT\n/{BUCKET}\n\n{headers}\n{signed}\n{empty}"
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n" + hashlib.sha256(request.encode()).hexdigest()
    k = ("AWS4" + secret).encode()
    for part in (day, region, "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    auth = f"AWS4-HMAC-SHA256 Credential={key}/{scope}, SignedHeaders={signed}, Signature={signature}"
    req = urllib.request.Request(f"{endpoint}/{BUCKET}", method="PUT", data=b"",
                                 headers={"x-amz-date": amzdate, "x-amz-content-sha256": empty, "Authorization": auth})
    try:
        urllib.request.urlopen(req, timeout=10)  # noqa: S310 - the check's own S3 server
    except urllib.error.HTTPError as err:
        if err.code != 409:  # the bucket of an earlier pass
            raise


def gained_module() -> str:
    """A copy of the module that has since gained an output named pull-zone, which no Secret key may be."""
    copy = Path("/tmp/gained-module")
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(os.environ["INFRASTACK_MODULE_DIR"], copy)
    (copy / "gained.tf").write_text('output "pull-zone" {\n  value = "gained"\n}\n', encoding="utf-8")
    return str(copy)


def packed_module(name: str) -> Path:
    """A copy of the module that carries its own providers in .funcd-providers, packed as `tofu providers mirror`
    packs them, from the image's own, and the lock file of those packages, made with no network (funcd ADR-0355)."""
    arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    copy = Path("/tmp") / name
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(os.environ["INFRASTACK_MODULE_DIR"], copy)
    plugins = Path(handler.PLUGINS)
    for unpacked in sorted(plugins.glob(f"*/*/*/*/linux_{arch}")):
        address, version = unpacked.parent.parent.relative_to(plugins), unpacked.parent.name
        kind = address.name
        packed = copy / handler.PROVIDERS / address / f"terraform-provider-{kind}_{version}_linux_{arch}.zip"
        packed.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(packed, "w") as zf:
            for f in sorted(unpacked.iterdir()):
                info = zipfile.ZipInfo(f.name)
                info.external_attr = (f.stat().st_mode & 0o777) << 16
                zf.writestr(info, f.read_bytes())
    lock = subprocess.run([handler.TOFU, "providers", "lock", f"-fs-mirror={copy / handler.PROVIDERS}",
                           f"-platform=linux_{arch}"], cwd=copy, capture_output=True, text=True,
                          env={**os.environ, "HOME": "/tmp", "TF_IN_AUTOMATION": "1", "CHECKPOINT_DISABLE": "1"})
    if lock.returncode != 0:
        raise SystemExit(f"FAIL: lock the packed providers: {lock.stderr}")
    return copy


def mismatched(module: Path) -> Path:
    """A copy of the packed module whose random provider package is null's, under random's name: it matches no hash of
    the lock file."""
    copy = Path("/tmp/mismatched-module")
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(module, copy)
    random = next((copy / handler.PROVIDERS).glob("*/hashicorp/random/*.zip"))
    null = next((copy / handler.PROVIDERS).glob("*/hashicorp/null/*.zip"))
    shutil.copyfile(null, random)
    return copy


def pet_module() -> str:
    """A module whose one resource is random_pet.name."""
    folder = Path("/tmp/pet-module")
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir()
    (folder / "main.tf").write_text(
        'terraform {\n  required_providers {\n    random = {\n      source  = "hashicorp/random"\n'
        '      version = "3.6.3"\n    }\n  }\n}\n\nvariable "prefix" {\n  type = string\n}\n\n'
        'resource "random_pet" "name" {\n  prefix = var.prefix\n}\n\noutput "name" {\n  value = random_pet.name.id\n}\n',
        encoding="utf-8")
    return str(folder)


def check(cond: bool, what: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    print(f"ok: {what}", flush=True)


def main() -> None:
    got = dev_approval()
    check(got == {"approved_by": "check", "digest": LocalAPI.approval["digest"], "preview": {"add": 1}},
          f"the shim reads the approval at FUNCD_INVOKE_URL, with its token: {got}")

    create_bucket()
    secret = Secret({"INFRASTACK_STATE_PASSPHRASE": os.environ.get("CHECK_PASSPHRASE", "")})
    doc, result = run("random-stack-1", "apply", secret)
    check(doc["add"] == 3 and doc["destroy"] == 0, f"the preview adds three resources: {json.dumps(doc)[:300]}")
    check(doc["planDigest"].startswith("sha256:"), "the preview's document pins the saved plan")
    plain = result.get("outputs", {})
    check(set(result) == {"outputs"} and set(plain) == {"name"} and plain["name"].startswith("check-"),
          f"the plain output is the result's outputs: {result}")
    written = secret.written.get("random-stack-outputs", {})
    check(len(written.get("token", "")) == 24, "the sensitive output went to the outputs Secret, not the result")
    check(written.get("name") == plain["name"], "the plain output went to the outputs Secret too")
    check(not (DATA / PREFIX / "plans" / "random-stack-1.tfplan").exists(), "the applied plan is removed")
    state = (DATA / PREFIX / "terraform.tfstate").read_bytes()
    encrypted = bool(os.environ.get("CHECK_PASSPHRASE"))
    check((b"random_pet" in state) != encrypted, f"the state is kept in the bucket, encrypted: {encrypted}")

    doc = handler.preview(Context("random-stack-2", secret), event("apply"))
    check(doc["add"] + doc["change"] + doc["destroy"] == 0, "a second plan changes nothing")

    tampered = dict(doc, planDigest="sha256:" + "1" * 64)
    try:
        handler.handle(Context("random-stack-2", secret, {"preview": tampered}), event("apply"))
        check(False, "a plan that is not the approved one is applied")
    except handler.StackError as err:
        check("not sha256:" in str(err), f"a plan that is not the approved one is refused: {err}")

    lock = DATA / PREFIX / "terraform.tfstate.tflock"
    lock.write_text(json.dumps({"ID": str(uuid.uuid4()), "Operation": "OperationTypeApply", "Info": "",
                                "Who": "an interrupted run", "Version": "1.13.9", "Created": "2026-11-24T00:00:00Z",
                                "Path": f"{BUCKET}/{PREFIX}/terraform.tfstate"}), encoding="utf-8")
    doc = handler.preview(Context("random-stack-3", secret), event("apply"))
    check(not lock.exists() and doc["add"] == 0, "a run goes on past a lock that an interrupted run left")

    module, os.environ["INFRASTACK_MODULE_DIR"] = os.environ["INFRASTACK_MODULE_DIR"], gained_module()
    try:
        handler.preview(Context("random-stack-4", secret), event("apply"))
        check(False, "an output named pull-zone is planned")
    except handler.StackError as err:
        check("pull-zone" in str(err), f"an output whose name is no Secret key fails the preview: {err}")

    # scenario: stack-destroy-empties-outputs
    doc, result = run("random-stack-5", "destroy", secret)
    os.environ["INFRASTACK_MODULE_DIR"] = module
    check(doc["action"] == "destroy" and doc["destroy"] == 3, f"the destroy's preview removes the three: {doc}")
    check(result == {}, "a destroy answers no output")
    written = secret.written.get("random-stack-outputs", {})
    check(written == {"name": "", "token": ""}, f"the destroy empties each output key and keeps it: {sorted(written)}")

    # scenario: infra-module-pushed-by-hand: the runs above, of a module pushed with no provider folder and a contract of
    # funcd ADR-0355 Decision 3's shape, installed the image's providers and answered {"outputs": ...}.

    # scenario: infra-run-downloads-nothing
    packed = packed_module("packed-module")
    images, handler.PLUGINS = handler.PLUGINS, "/nonexistent/plugins"
    os.environ["INFRASTACK_MODULE_DIR"] = str(packed)
    try:
        doc, result = run("packed-1", "apply", secret)
        check(doc["add"] == 3 and set(result.get("outputs", {})) == {"name"},
              "a module that carries its own providers plans and applies with them, downloading nothing")
        run("packed-2", "destroy", secret)

        # scenario: infra-provider-mismatch
        os.environ["INFRASTACK_MODULE_DIR"] = str(mismatched(packed))
        try:
            handler.preview(Context("mismatched-1", secret), event("apply"))
            check(False, "a provider package that matches no hash of its lock file is installed")
        except handler.StackError as err:
            check("hashicorp/random" in str(err), f"the preview fails, naming the provider: {str(err)[-300:]}")
        check(not (DATA / PREFIX / "plans" / "mismatched-1.tfplan").exists(), "nothing is planned or applied")
    finally:
        handler.PLUGINS = images
        os.environ["INFRASTACK_MODULE_DIR"] = module

    # scenario: infra-plan-run
    os.environ["INFRASTACK_MODULE_DIR"] = pet_module()
    try:
        applied = handler.handle(Context("pet-1", secret, prefix="pet-stack"), event("apply", "a"))
        check(set(applied.get("outputs", {})) == {"name"}, f"random_pet.name is applied with prefix a: {applied}")
        before = json.dumps(secret.written, sort_keys=True)
        doc = handler.preview(Context("pet-2", secret, prefix="pet-stack"), event("plan", "b"))
        check(not (DATA / "pet-stack" / "plans" / "pet-2.tfplan").exists(), "a plan run's preview keeps no saved plan")
        finding = handler.handle(Context("pet-2", secret, {"preview": doc}, prefix="pet-stack"), event("plan", "b"))
        want = {"plan": {"add": 0, "change": 0, "destroy": 0, "replace": 1, "resources": ["random_pet.name"]}}
        check(finding == want, f"a plan run with prefix b answers what would change: {finding}")
        check(json.dumps(secret.written, sort_keys=True) == before, "a plan run writes no output")
        again = handler.preview(Context("pet-3", secret, prefix="pet-stack"), event("apply", "a"))
        check(again["add"] + again["change"] + again["destroy"] + again["replace"] == 0, "a plan run applies nothing")
    finally:
        os.environ["INFRASTACK_MODULE_DIR"] = module
    print("PASS", flush=True)


if __name__ == "__main__":
    main()
