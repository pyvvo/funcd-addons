"""The scenario infra-push-builds-index with real OpenTofu (funcd ADR-0355): funcdctl-infra pushes a module whose
main.tf calls the local child module ./sub into an OCI layout, with the OpenTofu of the image INFRASTACK_OPENTOFU_IMAGE
names and the funcdctl FUNCDCTL names. It checks the index, the contract that funcdctl inspect prints, and that each
platform's bundle holds only its own provider packages; then that a module whose only variable is sensitive pushes, with
void variables (the scenario infra-sensitive-only-module)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROVIDER = 'terraform {\n  required_providers {\n    random = {\n      source  = "hashicorp/random"\n' \
           '      version = "3.6.3"\n    }\n  }\n}\n'
MAIN = PROVIDER + '\nvariable "prefix" {\n  type = string\n}\n\nvariable "token" {\n  type      = string\n' \
                  '  sensitive = true\n  default   = "unset"\n}\n\nmodule "sub" {\n  source = "./sub"\n' \
                  '  prefix = var.prefix\n}\n\noutput "name" {\n  value = module.sub.name\n}\n'
SUB = PROVIDER + '\nvariable "prefix" {\n  type = string\n}\n\nresource "random_pet" "name" {\n  prefix = var.prefix\n' \
                 '}\n\noutput "name" {\n  value = random_pet.name.id\n}\n'
# A module whose only variable is sensitive (funcd ADR-0355 Decision 3, amended 2026-11-24).
SECRET = PROVIDER + '\nvariable "token" {\n  type      = string\n  sensitive = true\n}\n\n' \
                    'resource "random_pet" "name" {\n  keepers = {\n    token = sha256(var.token)\n  }\n}\n'


def check(cond: bool, what: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    print(f"ok: {what}", flush=True)


def main() -> None:
    funcdctl = os.environ["FUNCDCTL"]
    with tempfile.TemporaryDirectory(prefix="infra-push-check-") as tmp:
        module = Path(tmp) / "stack"
        (module / "sub").mkdir(parents=True)
        (module / "main.tf").write_text(MAIN, encoding="utf-8")
        (module / "sub" / "main.tf").write_text(SUB, encoding="utf-8")
        layout = Path(tmp) / "layout"
        ref = f"oci-layout://{layout}:stack-1"
        pushed = subprocess.run([sys.executable, str(HERE / "funcdctl-infra"), "push", str(module), ref],
                                capture_output=True, text=True)
        check(pushed.returncode == 0, f"funcdctl infra push succeeds: {pushed.stderr[-2000:]}")
        got = pushed.stdout.strip()
        check(got.startswith(ref + "@sha256:"), f"it prints <ref>@<digest>: {got}")
        digest = got.rsplit("@", 1)[1]
        lock = (module / ".terraform.lock.hcl").read_text(encoding="utf-8")
        check("hashicorp/random" in lock, "the lock file is written into the module's folder, to commit")

        index = json.loads((layout / "blobs" / "sha256" / digest.split(":", 1)[1]).read_text(encoding="utf-8"))
        platforms = sorted(f"{m['platform']['os']}/{m['platform']['architecture']}" for m in index["manifests"])
        check(platforms == ["linux/amd64", "linux/arm64"], f"an image index of linux/amd64 and linux/arm64: {platforms}")

        shown = json.loads(subprocess.run([funcdctl, "inspect", got], capture_output=True, text=True, check=True).stdout)
        variables = shown["input"]["properties"]["variables"]
        check(variables["properties"] == {"prefix": {"type": "string"}} and variables["required"] == ["prefix"],
              f"the contract's input is the module's variables, the sensitive one left out: {variables}")
        outputs = shown["output"]["properties"]["outputs"]["properties"]
        check(outputs == {"name": {}}, f"its output is the module's plain outputs: {outputs}")

        for platform in ("linux/amd64", "linux/arm64"):
            into = Path(tmp) / platform.replace("/", "-")
            subprocess.run([funcdctl, "pull", ref, digest, str(into), "--platform", platform], capture_output=True,
                           text=True, check=True)
            packages = sorted(p.name for p in into.rglob("*.zip"))
            arch = platform.split("/")[1]
            check(packages == [f"terraform-provider-random_3.6.3_linux_{arch}.zip"],
                  f"the {platform} bundle holds only its own provider packages: {packages}")
            check((into / "sub" / "main.tf").is_file() and (into / ".terraform.lock.hcl").is_file(),
                  f"the {platform} bundle holds the child module and the lock file")

        # scenario: infra-sensitive-only-module
        secret = Path(tmp) / "secret"
        secret.mkdir()
        (secret / "main.tf").write_text(SECRET, encoding="utf-8")
        sref = f"oci-layout://{layout}:secret-1"
        spushed = subprocess.run([sys.executable, str(HERE / "funcdctl-infra"), "push", str(secret), sref],
                                 capture_output=True, text=True)
        check(spushed.returncode == 0, f"a module whose only variable is sensitive pushes too: {spushed.stderr[-2000:]}")
        sinput = json.loads(subprocess.run([funcdctl, "inspect", spushed.stdout.strip()], capture_output=True, text=True,
                                           check=True).stdout)["input"]
        check(sinput["properties"]["variables"] == {"type": "null"} and sinput["required"] == ["action"],
              f"its variables are void, so a run's input sets none: {sinput}")
    print("PASS", flush=True)


if __name__ == "__main__":
    main()
