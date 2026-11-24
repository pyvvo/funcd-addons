"""Fetch OpenTofu and the providers that pins.json names for one architecture, check each archive against its pinned
sha256, and lay them out under a root: bin/tofu, and plugins/<host>/<namespace>/<type>/<version>/linux_<arch>/, the
unpacked layout of OpenTofu's -plugin-dir, so a run downloads nothing. NOTICE names each one, its MPL-2.0 licence and
where its source is."""

import hashlib
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path


def fetch(url: str, sha256: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - pinned https release URLs
        data = resp.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise SystemExit(f"{url}: sha256 {got}, not the pinned {sha256}")
    return data


def unpack(data: bytes, into: Path) -> None:
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            path = into / info.filename
            if info.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zf.read(info))
            path.chmod(0o755 if executable(info) else 0o644)


def executable(info: zipfile.ZipInfo) -> bool:
    """An archive's program: its recorded mode says so, or it is OpenTofu or a provider."""
    name = Path(info.filename).name
    return bool((info.external_attr >> 16) & 0o111) or name == "tofu" or name.startswith("terraform-provider-")


def main(pins_file: str, arch: str, root: str) -> None:
    pins = json.loads(Path(pins_file).read_text(encoding="utf-8"))
    out = Path(root)
    tofu = pins["tofu"]
    unpack(fetch(tofu["url"].format(version=tofu["version"], arch=arch), tofu["sha256"][arch]), out / "bin")
    notice = [f"OpenTofu {tofu['version']}, MPL-2.0, unmodified: {tofu['source']}"]
    for p in pins["providers"]:
        url = p["url"].format(version=p["version"], arch=arch)
        unpack(fetch(url, p["sha256"][arch]), out / "plugins" / p["address"] / p["version"] / f"linux_{arch}")
        notice.append(f"{p['address']} {p['version']}, MPL-2.0, unmodified: {p['source']}")
    (out / "NOTICE").write_text("\n".join(notice) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:])
