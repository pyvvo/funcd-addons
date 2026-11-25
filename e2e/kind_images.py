"""The Functions of the add-ons' kinds, which funcd creates from their images (funcd ADR-0358): push each one with funcdctl
and compare its digest with the one its Addon pins. One tree has one digest wherever funcdctl pushes it, so the Addon pins
an image before a release publishes it.

usage: kind_images.py <funcdctl> [--write] [--to <registry> --tag <tag>]

Without --to, each is pushed to a layout of its own, which goes. --write writes the digests into the Addons; without it, a
digest that an Addon does not pin ends the run with 1."""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = "ghcr.io/pyvvo"
# Each kind's Addon, the image's name and the Function's file, beside its contract.
KINDS = [
    ("rauthy/app/addon.yaml", "funcd-rauthy-settings", "rauthy/settings/rauthy-settings.mjs"),
    ("infrastack/app/addon.yaml", "funcd-infrastack-reconciler", "infrastack/reconciler/infrastack-reconciler.mjs"),
]


def push(funcdctl: str, file: Path, ref: str) -> str:
    """Push file to ref, and return the manifest's digest."""
    p = subprocess.run([funcdctl, "push", str(file), ref], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"push {file.name} to {ref}: {(p.stdout + p.stderr).strip()}")
    return re.findall(r"sha256:[0-9a-f]{64}", p.stdout)[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("funcdctl")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--to", default="")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    stale = False
    for addon, name, file in KINDS:
        with tempfile.TemporaryDirectory() as tmp:
            ref = f"{args.to}/{name}:{args.tag}" if args.to else f"oci-layout://{tmp}:{name}"
            got = push(args.funcdctl, ROOT / file, ref)
        path = ROOT / addon
        text = path.read_text(encoding="utf-8")
        pinned = re.compile(re.escape(f"{REGISTRY}/{name}@") + r"(sha256:[0-9a-f]{64})")
        found = pinned.search(text)
        if not found:
            raise SystemExit(f"{addon} names no image {REGISTRY}/{name} by its digest")
        if found.group(1) == got:
            print(f"{REGISTRY}/{name}@{got}")
        elif args.write:
            path.write_text(pinned.sub(f"{REGISTRY}/{name}@{got}", text), encoding="utf-8")
            print(f"{REGISTRY}/{name}@{got} written into {addon}")
        else:
            stale = True
            print(f"{addon} pins {found.group(1)} of {name}, whose files are {got}: run just kind-images", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
