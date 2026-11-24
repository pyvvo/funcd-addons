"""The OpenTofu runtime image's check: inside the image, the runtime's own handler plans, applies and destroys the
example random-stack module against an S3 server, as a stack's runs do, with a context that stands in for funcd's.

image_test.sh runs it: versitygw, the S3 engine of funcd's gateway, serves the bucket from a folder that this check
also reads, so a saved plan sits beside the state, and a stale lock is planted there as an interrupted run leaves one.
"""

import datetime
import hashlib
import hmac
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, "/opt/infrastack")

import handler  # noqa: E402 - the image's handler, on the path set above

BUCKET = os.environ["CHECK_BUCKET"]
PREFIX = "random-stack"
DATA = Path(os.environ["CHECK_DATA"]) / BUCKET


class Blob:
    """The state binding's prefix, as the S3 server's folder holds it."""

    def put(self, binding: str, key: str, data: bytes) -> None:
        assert binding == handler.STATE_BINDING, binding
        path = DATA / PREFIX / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, binding: str, key: str) -> bytes | None:
        path = DATA / PREFIX / key
        return path.read_bytes() if path.exists() else None

    def delete(self, binding: str, key: str) -> None:
        (DATA / PREFIX / key).unlink(missing_ok=True)


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
    def __init__(self, run: str, secret: Secret, approval: dict | None = None) -> None:
        self.run = {"name": run, "job": "random-stack", "revision": "random-stack-1", "phase": "run", "started_by": ""}
        self.approval = approval
        self.blob = Blob()
        self.secret = secret
        self.config = Config({"INFRASTACK_STATE_BUCKET": BUCKET, "INFRASTACK_STATE_PREFIX": PREFIX,
                              "INFRASTACK_OUTPUTS_SECRET": "random-stack-outputs"})

    def remaining_ms(self) -> None:
        return None


def event(action: str) -> dict:
    return {"type": "funcd.job.run", "data": {"action": action, "variables": {"prefix": "check"}}}


def run(name: str, action: str, secret: Secret) -> tuple[dict, dict]:
    """A run of the Job with approval: its preview, then its apply of the approved preview."""
    doc = handler.preview(Context(name, secret), event(action))
    approval = {"approved_by": "check", "digest": "sha256:" + "0" * 64, "preview": doc}
    return doc, handler.handle(Context(name, secret, approval), event(action))


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


def check(cond: bool, what: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    print(f"ok: {what}", flush=True)


def main() -> None:
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
    print("PASS", flush=True)


if __name__ == "__main__":
    main()
