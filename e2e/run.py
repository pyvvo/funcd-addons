"""The end-to-end run of funcd-addons: each add-on under funcdctl dev, whose add-on engines run on the Docker daemon at
FUNCD_DEV_DOCKER_HOST. rauthy is registered from rauthy/app/addon.yaml, its Secret written by its plugin, its engine run
and served at the edge, backed up and restored in place, and it signs a user in at the edge in front of a test add-on
that echoes the request (traefik/whoami), with a client funcd registers itself; and its password policy, a settings
kind that the settings Function puts into rauthy through its binding. funcd is built at e2e/funcd.ref, or
from the checkout FUNCD_SRC names. Writes work/results.json and exits 1 when a check fails. SAMPLES sets the samples
per timing (default 1)."""

import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
MAN = ROOT / "e2e" / "fixtures"
BIN = WORK / "bin"
FUNCDCTL = BIN / "funcdctl"
PROBE = MAN / "probe"
# dev's folder: the probe, and rauthy's settings Function with its manifest (funcd ADR-0341).
SETTINGS = ROOT / "rauthy" / "settings"
DEV = WORK / "dev"
POLICIES = "rauthypasswordpolicys"
SAMPLES = int(os.environ.get("SAMPLES", "1"))
GPORT, CPORT, S3PORT = 18091, 18090, 18092
DEV_TOKEN, ADMIN_TOKEN = "funcd-dev-token", "funcd-dev-admin-token"
DOCKER = os.environ.get("FUNCD_DEV_DOCKER_HOST") or f"unix://{Path.home()}/.colima/default/docker.sock"
API = f"http://127.0.0.1:{CPORT}/apis/funcd.io/v1alpha1/namespaces/default"
APP, IDP = f"localhost:{GPORT}", f"login.localhost:{GPORT}"
ADMIN_EMAIL = "admin@login.localhost"
UA = {"User-Agent": "experiment-0025"}
WHOAMI = "traefik/whoami@sha256:43a68d10b9dfcfc3ffbfe4dd42100dc9aeaf29b3a5636c856337a5940f1b4f1c"

checks: list[dict] = []
timings: list[dict] = []
measures: list[dict] = []


def check(name: str, ok, detail) -> None:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(detail)).replace(str(ROOT), ".")
    checks.append({"name": name, "pass": bool(ok), "detail": text[:400]})
    print(("PASS " if ok else "FAIL ") + name, flush=True)


def timing(name: str, what: str, samples: list[float], digits: int = 3) -> None:
    timings.append({"name": name, "what": what, "samples_s": [round(s, digits) for s in samples]})


def measure(name: str, what: str, unit: str, samples: list[float]) -> None:
    measures.append({"name": name, "what": what, "unit": unit, "samples": [round(s, 1) for s in samples]})


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "--host", DOCKER, *args], capture_output=True, text=True)


def ctl(*args: str, token: str = DEV_TOKEN) -> tuple[int, str]:
    env = {**os.environ, "FUNCD_SERVER": f"http://127.0.0.1:{CPORT}", "FUNCD_TOKEN": token,
           "PATH": f"{BIN}{os.pathsep}{os.environ['PATH']}"}
    p = subprocess.run([str(FUNCDCTL), *args], capture_output=True, text=True, env=env)
    return p.returncode, (p.stdout + p.stderr).strip()


def apply(path: Path, token: str = DEV_TOKEN) -> tuple[int, str]:
    return ctl("apply", "-f", str(path), token=token)


def write(name: str, text: str) -> Path:
    p = WORK / name
    p.write_text(text)
    return p


def get(plural: str, name: str) -> dict:
    req = urllib.request.Request(f"{API}/{plural}/{name}", headers={"Authorization": f"Bearer {DEV_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError):
        return {}


def status(plural: str, name: str) -> dict:
    return get(plural, name).get("status") or {}


def wait(fn, timeout: float, every: float = 0.25):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if v:
            return v, time.monotonic() - t0
        time.sleep(every)
    return None, time.monotonic() - t0


def phase(plural: str, name: str, want: str, timeout: float = 120) -> float | None:
    def seen():
        p = status(plural, name).get("phase")
        return p if p in (want, "Failed") else None
    v, took = wait(seen, timeout)
    return took if v == want else None


def edge(method: str, host: str, path: str, headers: dict | None = None, body=None, conn=None):
    c = conn or http.client.HTTPConnection("127.0.0.1", GPORT, timeout=30)
    h = {"Host": host, **UA, **(headers or {})}
    if body is not None and not isinstance(body, (bytes, str)):
        body, h["Content-Type"] = json.dumps(body), "application/json"
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    if conn is None:
        c.close()
    return r.status, r.headers, data


def engine(method: str, addr: str, path: str, token: str = "", body=None, form: bytes | None = None):
    h = {**UA, **({"Authorization": f"Bearer {token}"} if token else {})}
    data = form
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(f"http://{addr}{path}", data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError):
        return 0, b""


# A browser's navigation, the only call that funcd's edge redirects to sign in (funcd ADR-0335 Decision 2), and the
# sign-in's cookies of the provider sso of default over http (Decision 5).
NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
STATE_COOKIE, SESSION_COOKIE = "funcd_auth.default.sso", "funcd_session.default.sso"


def cookie(headers, name: str) -> str | None:
    for v in headers.get_all("Set-Cookie") or []:
        k, _, rest = v.partition("=")
        if k == name:
            return rest.split(";", 1)[0]
    return None


def solve(challenge: str) -> str:
    bits = int(challenge[2:4])
    for n in range(1 << 32):
        if int.from_bytes(hashlib.sha256((challenge + str(n)).encode()).digest(), "big") >> (256 - bits) == 0:
            return challenge + str(n)
    raise RuntimeError("no proof of work found")


def signin(host: str, password: str) -> dict:
    """Signs in through the edge as a browser would: the redirect, rauthy's login (a session, the proof of work, the
    login post, as rauthy's own tests do), the callback, and the first call with the session."""
    t0 = time.monotonic()
    st, h, _ = edge("GET", host, "/", NAV)
    if st != 302:
        return {"error": f"want a redirect, got {st}"}
    auth = urllib.parse.urlsplit(h["Location"])
    q = dict(urllib.parse.parse_qsl(auth.query))
    state = cookie(h, STATE_COOKIE)
    _, h, body = edge("POST", IDP, "/auth/v1/oidc/session")
    sess = h["Set-Cookie"].split(";", 1)[0]
    csrf = json.loads(body)["csrf_token"]
    _, _, chal = edge("POST", IDP, "/auth/v1/pow")
    p0 = time.monotonic()
    pow_ = solve(chal.decode().strip().strip('"'))
    pow_s = time.monotonic() - p0
    login = {"email": ADMIN_EMAIL, "password": password, "pow": pow_, "client_id": q["client_id"],
             "redirect_uri": q["redirect_uri"], "scopes": q["scope"].split(), "state": q["state"], "nonce": q["nonce"],
             "code_challenge": q["code_challenge"], "code_challenge_method": q["code_challenge_method"]}
    st, h, body = edge("POST", IDP, "/auth/v1/oidc/authorize?" + auth.query, {"Cookie": sess, "x-csrf-token": csrf}, login)
    if st != 202:
        return {"error": f"login: {st} {body[:200]!r}"}
    cb = urllib.parse.urlsplit(h["Location"])
    st, h, body = edge("GET", host, cb.path + "?" + cb.query, {"Cookie": f"{STATE_COOKIE}={state}"})
    if st != 302:
        return {"error": f"callback: {st} {body[:200]!r}"}
    session = cookie(h, SESSION_COOKIE)
    st, h, body = edge("GET", host, h["Location"], {"Cookie": f"{SESSION_COOKIE}={session}"})
    return {"status": st, "echo": body.decode(), "session": session, "s": time.monotonic() - t0, "pow_s": pow_s}


def echoed(echo: str, header: str) -> str | None:
    for line in echo.splitlines():
        k, _, v = line.partition(": ")
        if k.lower() == header.lower():
            return v.strip()
    return None


def engine_of(name: str, fmt: str = "{{.Names}}") -> str:
    """The container of the IdentityProvider name's engine under dev, by its labels (funcd ADR-0334 Decision 6)."""
    out = docker("ps", "-a", "--no-trunc", "--filter", "label=funcd/role=engine", "--filter", f"label=funcd/name={name}",
                 "--format", fmt).stdout.split()
    return out[0] if out else ""


def engine_id() -> str:
    """The container now running the IdentityProvider login's engine."""
    return engine_of("login", "{{.ID}}")


def snapshot_files() -> list[int]:
    """The timestamps in the names of rauthy's snapshots, from its engine's snapshot folder."""
    cp = subprocess.run(["docker", "--host", DOCKER, "cp", engine_of("login") + ":/app/data/state_machine/backups", "-"],
                        capture_output=True)
    names = subprocess.run(["tar", "tf", "-"], input=cp.stdout, capture_output=True).stdout.decode()
    return sorted(int(m) for m in re.findall(r"backup_node_1_(\d+)\.sqlite$", names, re.M))


def register_client(addr: str, name: str) -> tuple[str, str]:
    """A client registered at rauthy's own address, as a change to its data that a restore can undo."""
    _, body = engine("POST", addr, "/auth/v1/clients_dyn", body={
        "redirect_uris": [f"http://localhost:{GPORT}/cb"], "grant_types": ["authorization_code"], "client_name": name,
        "token_endpoint_auth_method": "client_secret_post"})
    d = json.loads(body)
    return d["client_id"], d["registration_access_token"]


def secret_data(name: str) -> dict:
    rc, out = ctl("get", "secret", name, "-n", "default", "-o", "json")
    if rc != 0:
        return {}
    return {k: base64.b64decode(v).decode() for k, v in json.loads(out)["spec"].get("data", {}).items()}


def manifest(kind: str, name: str, spec: str, cluster: bool = False) -> str:
    meta = f"metadata:\n  name: {name}\n" + ("" if cluster else "  namespace: default\n  resourceGroup: rg1\n")
    return f"apiVersion: funcd.io/v1alpha1\nkind: {kind}\n{meta}spec:\n{spec}"


def b64(s: str | bytes) -> str:
    return base64.b64encode(s.encode() if isinstance(s, str) else s).decode()


def restore(name: str) -> Path:
    text = (MAN / "restore.yaml").read_text()
    return write(f"{name}.yaml", text.replace("name: login-restore", f"name: {name}").replace("backup: login-now\n", "backup: login-now-1\n"))


def build() -> None:
    """funcdctl's dev build at e2e/funcd.ref, or from FUNCD_SRC, and each add-on's plugin, into work/bin."""
    BIN.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GOBIN": str(BIN), "CGO_ENABLED": "0"}
    if os.environ.get("FUNCD_SRC"):
        subprocess.run(["go", "build", "-tags", "dev", "-o", str(FUNCDCTL), "./cmd/funcdctl"], cwd=os.environ["FUNCD_SRC"],
                       check=True, env=env)
    else:
        ref = (ROOT / "e2e" / "funcd.ref").read_text().strip()
        subprocess.run(["go", "install", "-tags", "dev", f"github.com/pyvvo/funcd/cmd/funcdctl@{ref}"], check=True, env=env)
    for d in sorted(ROOT.glob("*/plugin/*/")):
        if not any(d.glob("*.go")):
            continue
        subprocess.run(["go", "build", "-o", str(BIN / d.name), f"./{d.relative_to(ROOT)}"], cwd=ROOT, check=True, env=env)


def addon_manifest() -> Path:
    """rauthy/app/addon.yaml with its snapshot cron every 15 s, so the run waits seconds for a snapshot, not 15 minutes."""
    text = (ROOT / "rauthy" / "app" / "addon.yaml").read_text()
    shortened = re.sub(r'HQL_BACKUP_CRON: "[^"]*"', 'HQL_BACKUP_CRON: "*/15 * * * * * *"', text)
    if shortened == text:
        raise RuntimeError("rauthy/app/addon.yaml sets no HQL_BACKUP_CRON")
    # The password policy's resync every 5 s, so the drift check waits seconds.
    resynced = shortened.replace("            resync: 10m\n", "            resync: 5s\n")
    if resynced == shortened:
        raise RuntimeError("rauthy/app/addon.yaml's RauthyPasswordPolicy sets no resync of 10m")
    shortened = resynced
    return write("addon-rauthy.yaml", shortened)


def test_secrets() -> None:
    """Writes fresh test values for the session key and the backup target."""
    write("secret-sso-session.yaml", manifest("Secret", "sso-session", f"  data:\n    SESSION_KEY: {b64(secrets.token_bytes(32))}\n"))
    env = subprocess.run([str(FUNCDCTL), "dev", ".", "--s3port", str(S3PORT), "--print-env"], cwd=PROBE, capture_output=True,
                         text=True).stdout
    kv = dict(re.findall(r"export (\w+)=(\S+)", env))
    write("secret-backups-key.yaml", manifest("Secret", "backups-key", "  data:\n"
                                              f"    AWS_ACCESS_KEY_ID: {b64(kv['AWS_ACCESS_KEY_ID'])}\n"
                                              f"    AWS_SECRET_ACCESS_KEY: {b64(kv['AWS_SECRET_ACCESS_KEY'])}\n"))


def rauthy_keys() -> str:
    """rauthy's Secret, applied by its plugin; returns the first admin's password, which the plugin prints once."""
    rc, out = ctl("rauthy", "keys", "--admin-email", ADMIN_EMAIL, "-n", "default")
    check("rauthy plugin: funcdctl rauthy keys applies the Secret", rc == 0 and "password: " in out, out.split("password:")[0])
    m = re.search(r"^password: (\S+)$", out, re.M)
    return m.group(1) if m else ""


def clean_docker() -> None:
    names = docker("ps", "-a", "--filter", "name=funcd-dev-addon-", "--format", "{{.Names}}").stdout.split()
    if names:
        docker("rm", "-f", *names)
    vols = docker("volume", "ls", "--filter", "name=funcd-dev-addon-", "--format", "{{.Name}}").stdout.split()
    if vols:
        docker("volume", "rm", *vols)


def dev_folder() -> None:
    """dev's folder: the probe and rauthy's settings Function, which binds the IdentityProvider login as idp and reads
    the API key that rauthy-keys bootstraps; dev runs each from its local build, found by its name."""
    shutil.rmtree(DEV, ignore_errors=True)
    shutil.copytree(PROBE, DEV)
    for f in SETTINGS.iterdir():
        shutil.copy(f, DEV / f.name)
    (DEV / "rauthy-settings.yaml").write_text(manifest("Function", "rauthy-settings", (
        "  runtime: nodejs22\n  handler: reconcile\n  image: oci-layout:///mnt/funcd-deps/registry:rauthy-settings\n"
        "  secrets:\n    - name: rauthy-keys\n      keys:\n        - BOOTSTRAP_API_KEY_SECRET\n"
        "  bindings:\n    addon:\n      - alias: idp\n        object: login\n    secret:\n      - rauthy-keys\n")))


def start_dev() -> subprocess.Popen:
    clean_docker()
    dev_folder()
    log = open(WORK / "logs" / "dev.log", "w")
    dev = subprocess.Popen([str(FUNCDCTL), "dev", "--gport", str(GPORT), "--cport", str(CPORT), "--s3port", str(S3PORT)],
                           cwd=DEV, stdout=log, stderr=subprocess.STDOUT,
                           env={**os.environ, "FUNCD_DEV_DOCKER_HOST": DOCKER, "BOOTSTRAP_API_KEY_SECRET": secrets.token_hex(32)})
    for _ in range(240):
        try:
            socket.create_connection(("127.0.0.1", CPORT), timeout=0.5).close()
            return dev
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("funcdctl dev did not listen")


def f258_register() -> None:
    rc, out = apply(addon_manifest())
    check("F258: a developer cannot register an add-on", rc != 0 and "cluster-scoped" in out, out)
    rc, out = apply(write("addon-by-tag.yaml", (MAN / "addon-whoami.yaml").read_text().replace(WHOAMI, "traefik/whoami:v1.10.3")), ADMIN_TOKEN)
    check("F258: an image not pinned by digest is refused", rc != 0 and "spec.runtimes[0].image" in out, out)
    outs = [apply(addon_manifest(), ADMIN_TOKEN), apply(MAN / "addon-whoami.yaml", ADMIN_TOKEN)]
    check("F258: an admin registers rauthy and whoami", all(rc == 0 for rc, _ in outs), [o for _, o in outs])
    bad = write("idp-bad-config.yaml", (MAN / "identityprovider.yaml").read_text().replace(
        "    https: false\n", "    https: false\n    colour: blue\n"))
    rc, out = apply(bad)
    check("F258: an object whose config breaks the add-on's schema is refused", rc != 0 and "colour" in out, out)
    rc, out = apply(write("addon-funcd-runtime.yaml", manifest("Addon", "nodething", f"  runtimes:\n    - name: nodejs22\n"
                                                               f"      image: {WHOAMI}\n      workloads:\n        - Function\n",
                                                               cluster=True)), ADMIN_TOKEN)
    check("ADR-0344: an Addon cannot ship a runtime of funcd's own name", rc != 0 and "spec.runtimes[0].name" in out, out)


def f260_run() -> None:
    apply(MAN / "identityprovider.yaml")
    took = phase("identityproviders", "login", "Ready", 120)
    check("F260: rauthy's engine runs on Docker and turns Ready", took is not None, status("identityproviders", "login").get("conditions"))
    # rauthy refuses an origin with no port, so the Addon adds the scheme's own when publicHost names none.
    apply(write("noport.yaml", (MAN / "identityprovider.yaml").read_text().replace("name: login", "name: noport").replace(
        "publicHost: login.localhost:18091", "publicHost: noport.localhost").replace("rpId: login.localhost", "rpId: noport.localhost").replace(
        "host: login.localhost", "host: noport.localhost").replace("spec:\n", "spec:\n  deletion: delete\n")))
    check("rauthy turns Ready with a publicHost that names no port", phase("identityproviders", "noport", "Ready", 120) is not None,
          status("identityproviders", "noport").get("conditions"))
    ctl("delete", "identityprovider", "noport", "-n", "default")
    wait(lambda: not engine_of("noport"), 30)
    samples = [took] if took else []
    for i in range(1, SAMPLES):
        name = f"ready-{i}"
        spec = (MAN / "identityprovider.yaml").read_text().replace("name: login", f"name: {name}").replace(
            "host: login.localhost", f"host: {name}.localhost").replace("spec:\n", "spec:\n  deletion: delete\n")
        apply(write(f"{name}.yaml", spec))
        t = phase("identityproviders", name, "Ready", 120)
        if t:
            samples.append(t)
        ctl("delete", "identityprovider", name, "-n", "default")
        wait(lambda: not engine_of(name), 30)
    timing("rauthy-ready", "an IdentityProvider applied until Ready, on a new volume (rauthy's first start)", samples)
    rc, out = ctl("get", "identityproviders", "-n", "default")
    check("F258: funcdctl lists the add-on's kind with no code for it", rc == 0 and "login" in out, out)
    st, _, _ = edge("GET", IDP, "/auth/v1/ping")
    check("F260: the edge serves the engine under its base path, kept", st == 200, f"GET /auth/v1/ping via the edge: {st}")
    st, _, _ = edge("POST", IDP, "/auth/v1/clients_dyn", body={"redirect_uris": []})
    est, _ = engine("POST", status("identityproviders", "login").get("address"), "/auth/v1/clients_dyn", body={"redirect_uris": []})
    check("F258: a private path is refused at the edge and served at the engine's address", st == 404 and est not in (404, 0),
          f"edge {st}, engine {est}")
    apply(MAN / "echo-open.yaml")
    t = phase("echoservices", "echo", "Ready", 60)
    st, _, body = edge("GET", APP, "/")
    check("F260: a second add-on (whoami) runs with no code for it", t is not None and st == 200 and b"Hostname" in body,
          f"Ready after {t and round(t, 2)} s, GET / {st}")
    stats = docker("stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}").stdout
    mem = dict(re.findall(r"funcd-dev-addon-[0-9a-f]+-default-(\w+) ([\d.]+)MiB", stats))
    for name, what in (("login", "rauthy"), ("echo", "whoami")):
        if name in mem:
            measure(f"{what}-memory", f"{what}'s container memory once Ready", "MiB", [float(mem[name])])


def f259_backup() -> tuple[str, str]:
    apply(WORK / "secret-backups-key.yaml")
    apply(MAN / "backup.yaml")
    addr = status("identityproviders", "login")["address"]
    a_id, a_tok = register_client(addr, "exp-a")
    ta = int(time.time())
    newest, took = wait(lambda: (snapshot_files() or [0])[-1] > ta, 90, 1)
    time.sleep(2)  # hiqlite rewrites a copy's metadata right after it renames it into place
    check("F259: rauthy writes a snapshot newer than a change", bool(newest), f"after {round(took)} s")
    samples, members = [], None
    for i in range(1, SAMPLES + 1):
        apply(write(f"backup-{i}.yaml", manifest("Backup", f"login-now-{i}", "  schedule: login-nightly\n")))
        t = phase("backups", f"login-now-{i}", "Succeeded", 120)
        if t:
            samples.append(t)
        members = members or status("backups", f"login-now-{i}").get("members")
    timing("backup", "a Backup of the IdentityProvider applied until Succeeded (its newest snapshot, copied unopened)", samples)
    check("F259: a Backup copies the add-on object as a member of its own",
          bool(members) and members[0]["kind"] == "IdentityProvider" and members[0]["bytes"] > 0, members)
    b_id, b_tok = register_client(addr, "exp-b")
    rc, out = apply(write("restore-as.yaml", manifest("Restore", "login-as", "  backup: login-now-1\n  source:\n"
                          "    credentialsSecret: backups-key\n    identitySecret: backups-identity\n  members:\n"
                          "    - kind: IdentityProvider\n      name: login\n      as: other\n")))
    check("F259: an add-on object restores in place only", rc != 0 and "in place only" in out, out)
    restore_s, back_s, kept, start_log = [], [], None, ""

    def answers():
        a = status("identityproviders", "login").get("address")
        return a if a and engine("GET", a, "/auth/v1/ping")[0] == 200 else None

    def logged(cid: str):
        out = docker("logs", cid)
        text = out.stdout + out.stderr
        return text if "final place" in text or "Cannot remove old data dir" in text else None

    # A Restore succeeds once rauthy is back without the copy: funcd starts the engine with restore.env, then again
    # without it, under the namespace hold (funcd ADR-0333 Decision 4). The engine that loads the copy is read while
    # the Restore runs, before the second start replaces it.
    for i in range(1, SAMPLES + 1):
        c0 = engine_id()
        t0 = time.monotonic()
        apply(restore(f"login-restore-{i}"), ADMIN_TOKEN)
        c1, _ = wait(lambda: (c := engine_id()) and c != c0 and c, 120, 0.1)  # the engine that starts with restore.env
        if c1 and not start_log:
            start_log = wait(lambda: logged(c1), 30, 0.2)[0] or ""
        t = phase("restores", f"login-restore-{i}", "Succeeded", 180)
        if t is None:
            check(f"F259: Restore {i} succeeds", False, status("restores", f"login-restore-{i}").get("conditions"))
            break
        c2 = engine_id()  # the engine without restore.env
        back, _ = wait(lambda: c2 not in (c0, c1) and answers(), 60, 0.1)
        restore_s.append(time.monotonic() - t0)
        back_s.append(time.monotonic() - t0)
        if kept is None and back:
            kept = (engine("GET", back, f"/auth/v1/clients_dyn/{a_id}", a_tok)[0], engine("GET", back, f"/auth/v1/clients_dyn/{b_id}", b_tok)[0])
    timing("restore", "an in-place Restore of the IdentityProvider applied until Succeeded, its two restarts included", restore_s)
    timing("restore-engine-back", "a Restore applied until rauthy answers again after its two restarts, with restore.env and then without", back_s)
    check("F259: the restore brings rauthy back to the backup (a client from before kept, one from after gone)",
          kept == (200, 404), f"before the backup: {kept and kept[0]}, after it: {kept and kept[1]}")
    return a_id, start_log


def f262_register() -> dict:
    for p in (WORK / "secret-sso-session.yaml", MAN / "oidcprovider.yaml", MAN / "echo-oidc.yaml"):
        apply(p)
    took = phase("oidcproviders", "sso", "Ready", 60)
    st = status("oidcproviders", "sso")
    timing("register-client", "an OIDCProvider applied with its first user until Ready (spike: polled every 10 s)", [took] if took else [])
    data = secret_data("sso")
    check("F262: funcd registers its client at rauthy, through the engine's address", took is not None and st.get("clientId")
          and sorted(data) == ["CLIENT_ID", "CLIENT_SECRET", "REGISTRATION_ACCESS_TOKEN", "REGISTRATION_CLIENT_URI"],
          f"clientId {st.get('clientId')}, callbacks {st.get('callbacks')}, Secret keys {sorted(data)}, "
          f"conditions {st.get('conditions')}")
    return data


def client_at_rauthy(data: dict) -> dict:
    addr = status("identityproviders", "login")["address"]
    st, body = engine("GET", addr, urllib.parse.urlsplit(data["REGISTRATION_CLIENT_URI"]).path, data["REGISTRATION_ACCESS_TOKEN"])
    return json.loads(body) if st == 200 else {"status": st}


def token_probe(client_id: str, secret: str) -> str:
    """rauthy's message for a made-up code with these client credentials: a wrong secret is refused before the code."""
    form = urllib.parse.urlencode({"grant_type": "authorization_code", "code": "nope", "client_id": client_id,
                                   "client_secret": secret, "redirect_uri": f"http://{APP}/.auth/callback"}).encode()
    st, body = engine("POST", status("identityproviders", "login")["address"], "/auth/v1/oidc/token", form=form)
    return f"{st} {json.loads(body or b'{}').get('message', '')}"


def f261_signin(password: str) -> None:
    st, h, _ = edge("GET", APP, "/", NAV)
    loc = urllib.parse.urlsplit(h.get("Location", ""))
    q = dict(urllib.parse.parse_qsl(loc.query))
    sso = status("oidcproviders", "sso")
    check("F261: an unauthenticated GET is redirected to sign in, with PKCE, state and nonce",
          st == 302 and loc.netloc == IDP and q.get("client_id") == sso.get("clientId") and q.get("code_challenge_method") == "S256"
          and q.get("state") and q.get("nonce") and q.get("redirect_uri") == f"http://{APP}/.auth/callback",
          f"{st} to {loc.scheme}://{loc.netloc}{loc.path}, redirect_uri {q.get('redirect_uri')}")
    runs = [signin(APP, password) for _ in range(SAMPLES)]
    ok = [r for r in runs if r.get("status") == 200]
    timing("signin", "the first GET until the app answers with a session: redirect, login, callback, with the stand-in's proof of work",
           [r["s"] for r in ok])
    timing("signin-pow", "the stand-in's proof of work alone, which rauthy asks of every login", [r["pow_s"] for r in ok])
    first = ok[0] if ok else runs[0]
    echo = first.get("echo", "")
    idt = echoed(echo, "X-Funcd-Id-Token") or ""
    claims = json.loads(base64.urlsafe_b64decode(idt.split(".")[1] + "==")) if idt.count(".") == 2 else {}
    alg = json.loads(base64.urlsafe_b64decode(idt.split(".")[0] + "==")).get("alg") if idt else None
    principal = json.loads(base64.b64decode(echoed(echo, "X-Funcd-Principal") or "e30="))
    check("F261: a signed-in call reaches the app with the principal headers",
          bool(ok) and echoed(echo, "X-Funcd-Principal-Id") == claims.get("sub") and echoed(echo, "X-Funcd-Principal-Name")
          and principal.get("provider") == "sso" and principal.get("claims", {}).get("sub") == claims.get("sub"),
          f"{len(ok)}/{len(runs)} sign-ins; Name {echoed(echo, 'X-Funcd-Principal-Name')}; ID token {alg}; {runs[0].get('error', '')}")
    session = first.get("session", "")
    forged = {"Cookie": f"{SESSION_COOKIE}={session}", "X-Funcd-Principal-Id": "mallory", "X-Funcd-Principal": b64('{"provider":"x"}')}
    st, _, body = edge("GET", APP, "/", forged)
    e = body.decode()
    check("F261: a caller's own principal headers are replaced by the edge's",
          st == 200 and echoed(e, "X-Funcd-Principal-Id") == claims.get("sub") and "mallory" not in e, f"{st}, Id {echoed(e, 'X-Funcd-Principal-Id')}")
    # A Bearer token signs a call in only for an audience the OIDCProvider names: here its own client's ID tokens.
    apply(write("oidcprovider-audiences.yaml", (MAN / "oidcprovider.yaml").read_text().replace(
        "  addon: login\n", f"  addon: login\n  audiences:\n    - \"{sso.get('clientId')}\"\n")))
    wait(lambda: edge("GET", APP, "/", {"Authorization": f"Bearer {idt}"})[0] == 200, 30, 0.2)
    st, _, body = edge("GET", APP, "/", {"Authorization": f"Bearer {idt}"})
    st2, h2, _ = edge("GET", APP, "/", {"Authorization": f"Bearer {idt[:-4]}AAAA"})
    check("F261: a valid Bearer token passes and a forged one gets 401",
          st == 200 and echoed(body.decode(), "X-Funcd-Principal-Id") == claims.get("sub") and st2 == 401,
          f"valid {st}, forged {st2} ({h2.get('WWW-Authenticate')})")
    st, _, _ = edge("POST", APP, "/", body={"a": 1})
    check("F261: a call that cannot be redirected (POST) gets 401 with no session", st == 401, st)
    for mode, want in (("401", 401), ("allow", 200)):
        apply(write(f"echo-{mode}.yaml", (MAN / "echo-oidc.yaml").read_text() + f"      unauthenticated: \"{mode}\"\n"))
        wait(lambda: edge("GET", APP, "/")[0] == want, 15, 0.2)
        st, _, body = edge("GET", APP, "/")
        check(f"F261: unauthenticated: {mode} gives {want}", st == want and (mode == "401" or "X-Funcd-Principal" not in body.decode()), st)
    apply(MAN / "echo-oidc.yaml")
    wait(lambda: edge("GET", APP, "/", NAV)[0] == 302, 15, 0.2)
    st, h, _ = edge("GET", APP, "/.auth/logout", {"Cookie": f"{SESSION_COOKIE}={session}"})
    cleared = "; ".join(h.get_all("Set-Cookie") or [])
    check("F261: /.auth/logout clears the session", st == 302 and f"{SESSION_COOKIE}=;" in cleared, cleared)
    apply(write("echo-plain.yaml", manifest("EchoService", "plain", "  ingress:\n    host: plain.localhost\n")))
    phase("echoservices", "plain", "Ready", 60)
    batches = {}
    for host, hdr in ((f"plain.localhost:{GPORT}", {}), (APP, {"Cookie": f"{SESSION_COOKIE}={session}"})):
        c = http.client.HTTPConnection("127.0.0.1", GPORT, timeout=30)
        edge("GET", host, "/", hdr, conn=c)
        per = []
        for _ in range(SAMPLES):
            t0 = time.monotonic()
            for _ in range(100):
                edge("GET", host, "/", hdr, conn=c)
            per.append((time.monotonic() - t0) / 100)
        c.close()
        batches[host] = per
    timing("call-open", "one GET to whoami through the edge, no sign-in (the mean of 100 on one connection)", batches[f"plain.localhost:{GPORT}"], 6)
    timing("call-session", "one GET to whoami through the edge with a session cookie (the mean of 100 on one connection)", batches[APP], 6)


def f262_keep(data: dict, password: str) -> None:
    old = data["CLIENT_SECRET"]
    apply(write("oidcprovider-rotate.yaml", (MAN / "oidcprovider.yaml").read_text().replace(
        "    registration: {}\n", "    registration:\n      rotate: 1\n")))
    v, took = wait(lambda: status("oidcproviders", "sso").get("observedRotate") == 1 and secret_data("sso").get("CLIENT_SECRET") != old, 60)
    new = secret_data("sso")
    codes = (token_probe(new["CLIENT_ID"], old), token_probe(new["CLIENT_ID"], new["CLIENT_SECRET"]))
    check("F262: raising rotate gets a new secret, and rauthy refuses the old one",
          bool(v) and "Invalid 'client_secret'" in codes[0] and "client_secret" not in codes[1],
          f"after {round(took, 1)} s; a made-up code with the old secret: {codes[0]}; with the new one: {codes[1]}")
    r = signin(APP, password)
    check("F262: a sign-in after the rotation works", r.get("status") == 200, r.get("error", r.get("status")))
    apply(write("echo-ip.yaml", manifest("EchoService", "echo-ip", "  ingress:\n    host: 127.0.0.1\n    auth:\n"
                                         "      mode: oidc\n      provider: sso\n")))
    v, took = wait(lambda: len(status("oidcproviders", "sso").get("callbacks") or []) == 2, 60)
    at = client_at_rauthy(secret_data("sso"))
    check("F262: a new host's callback reaches the client's redirect URIs",
          bool(v) and sorted(at.get("redirect_uris", [])) == sorted(status("oidcproviders", "sso")["callbacks"]),
          f"after {round(took, 1)} s: {at.get('redirect_uris')}")
    phase("echoservices", "echo-ip", "Ready", 60)
    r = signin(f"127.0.0.1:{GPORT}", password)
    check("F261: a second host signs in with the same provider", r.get("status") == 200, r.get("error", r.get("status")))
    before = status("oidcproviders", "sso").get("clientId")
    apply(restore("login-forget"), ADMIN_TOKEN)
    phase("restores", "login-forget", "Succeeded", 180)
    t0 = time.monotonic()
    v, _ = wait(lambda: (c := status("oidcproviders", "sso").get("clientId")) != before and c, 120, 0.5)
    timing("reregister", "a restore that loses funcd's client until funcd registered a new one (spike: polled every 10 s)",
           [time.monotonic() - t0] if v else [])
    wait(lambda: edge("GET", APP, "/", NAV)[0] == 302, 30, 0.5)
    r = signin(APP, password)
    check("F262: funcd registers again when rauthy lost its client, and sign-in works", bool(v) and r.get("status") == 200,
          f"client {before} then {v}; sign-in {r.get('status', r.get('error'))}")


def rauthy_api(method: str, path: str, body=None) -> tuple[int, object]:
    """A call of rauthy's API at its engine's address, with the API key settings that rauthy-keys bootstraps."""
    addr = status("identityproviders", "login").get("address")
    secret = secret_data("rauthy-keys").get("BOOTSTRAP_API_KEY_SECRET", "")
    h = {**UA, "Authorization": f"API-Key settings${secret}"}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(f"http://{addr}{path}", data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw, st = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, st = e.read(), e.code
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)
    try:
        return st, json.loads(raw)
    except ValueError:
        return st, raw.decode(errors="replace")


probe: dict = {}


def set_password(pw: str) -> tuple[int, object]:
    """A password change of a probe user, which rauthy checks against its policy."""
    if not probe:
        st, user = rauthy_api("POST", "/auth/v1/users", {"email": "probe@login.localhost", "given_name": "Probe",
                                                        "language": "en", "roles": []})
        if st != 200 or not isinstance(user, dict):
            return st, user
        probe.update(user)
    return rauthy_api("PUT", f"/auth/v1/users/{probe['id']}", {
        "email": probe["email"], "given_name": probe.get("given_name"), "family_name": probe.get("family_name"),
        "language": "en", "password": pw, "roles": probe.get("roles") or [], "groups": None, "enabled": True,
        "email_verified": False, "user_expires": None, "user_values": None})


def length_min() -> int | None:
    """The minimum length rauthy enforces now: rauthy refuses a password too short for any policy and names it."""
    st, body = set_password("aA1!bC2@")
    m = re.search(r"Minimum password length is (\d+)", str(body))
    return int(m.group(1)) if st == 400 and m else None


def password(n: int) -> str:
    return "aA1" + "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(n - 3))


def f266_settings() -> None:
    """rauthy's settings kind RauthyPasswordPolicy, applied by its Function through the IdentityProvider's binding."""
    rc, out = apply(write("policy-strict.yaml", manifest("RauthyPasswordPolicy", "strict",
                                                         "  config:\n    provider: login\n    lengthMin: 16\n    lengthMax: 128\n")))
    took = phase(POLICIES, "strict", "Ready", 120) if rc == 0 else None
    check("F266: a password policy turns Ready once rauthy's settings Function applied it", took is not None,
          out if rc else status(POLICIES, "strict"))
    s1, b1 = set_password(password(15))
    s2, b2 = set_password(password(16))
    check("F266: rauthy enforces the policy: 15 characters are refused, 16 accepted", s1 == 400 and s2 == 200,
          f"15: {s1} {str(b1)[:120]}; 16: {s2} {str(b2)[:120]}")
    loose = {"length_min": 10, "length_max": 128, "include_lower_case": None, "include_upper_case": None,
             "include_digits": None, "include_special": None, "valid_days": None, "not_recently_used": None}
    st, _ = rauthy_api("PUT", "/auth/v1/password_policy", loose)
    v, took = wait(lambda: length_min() == 16, 30, 0.5)
    check("F266: a policy changed at rauthy's own API is put back at the next resync", st == 200 and v,
          f"PUT {st}, back after {round(took, 1)} s")
    rc, out = apply(write("policy-second.yaml", manifest("RauthyPasswordPolicy", "second",
                                                         "  config:\n    provider: login\n    lengthMin: 12\n    lengthMax: 64\n")))
    check("F266: a second policy for login is refused", rc != 0 and "perTarget" in out, out)
    rc, out = ctl("delete", POLICIES, "strict", "-n", "default")
    v, took = wait(lambda: length_min() == 14, 60, 0.5)
    check("F266: deleting the policy puts rauthy's default back", rc == 0 and v, f"{out}; after {round(took, 1)} s")


def main() -> int:
    (WORK / "logs").mkdir(parents=True, exist_ok=True)
    build()
    test_secrets()
    dev = start_dev()
    try:
        password = rauthy_keys()
        f258_register()
        f260_run()
        _, logs = f259_backup()
        check("F259: each restore start loads the copy: the SIGTERM stop leaves no crash marker",
              "moving into its final place" in logs and "Cannot remove old data dir" not in logs,
              "; ".join(l.split("hiqlite::backup")[-1][:120] for l in logs.splitlines() if "Starting database restore" in l)[:300])
        data = f262_register()
        f261_signin(password)
        f262_keep(data, password)
        rc, out = ctl("rauthy", "urls", "login", "-n", "default")
        check("rauthy plugin: funcdctl rauthy urls prints the issuer", rc == 0 and f"issuer:  http://{IDP}/auth/v1/" in out, out)
        f266_settings()
    finally:
        dev.send_signal(signal.SIGINT)
        try:
            dev.wait(30)
        except subprocess.TimeoutExpired:
            dev.kill()
        clean_docker()
        (WORK / "results.json").write_text(json.dumps({"samples": SAMPLES, "timings": timings, "measures": measures,
                                                       "checks": checks}, indent=2) + "\n")
    failed = [c["name"] for c in checks if not c["pass"]]
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
