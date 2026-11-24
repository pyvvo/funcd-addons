"""Stamp funcdctl-infra with the opentofu1 image of the Addon it ships beside (funcd ADR-0355 Decision 1), as the
release workflow does: python3 stamp.py <addon.yaml> <funcdctl-infra> <out>."""

from __future__ import annotations

import re
import sys
from pathlib import Path

RUNTIME = re.compile(r"^\s*- name: opentofu1\s*$")
IMAGE_KEY = re.compile(r"^\s*image:\s*(\S+)\s*$")
IMAGE_LINE = re.compile(r'^IMAGE = "[^"]*"', re.MULTILINE)


def addon_image(addon: str) -> str:
    """The image of the Addon's opentofu1 runtime, as its block-style YAML names it."""
    lines = addon.splitlines()
    for i, line in enumerate(lines):
        if RUNTIME.match(line):
            for later in lines[i + 1:]:
                if later.lstrip().startswith("- name:"):
                    break
                found = IMAGE_KEY.match(later)
                if found:
                    return found.group(1)
    raise SystemExit("the Addon ships no opentofu1 runtime with an image")


def stamped(script: str, image: str) -> str:
    """The script with IMAGE set to image."""
    out, n = IMAGE_LINE.subn(f'IMAGE = "{image}"', script, count=1)
    if n != 1:
        raise SystemExit("the script holds no IMAGE line")
    return out


def main(addon: str, script: str, out: str) -> None:
    text = stamped(Path(script).read_text(encoding="utf-8"), addon_image(Path(addon).read_text(encoding="utf-8")))
    Path(out).write_text(text, encoding="utf-8")
    Path(out).chmod(0o755)


if __name__ == "__main__":
    main(*sys.argv[1:])
