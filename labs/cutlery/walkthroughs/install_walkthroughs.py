#!/usr/bin/env python3
"""Create (or revise) and approve the cutlery walkthroughs on a plant.

    export MES_ADMIN_PASSWORD=...        # or: set -a; . ~/.config/fsmes/prod/env; set +a
    python3 install_walkthroughs.py --base http://127.0.0.1:9030 --user ADMIN

Idempotent: an existing code gets a new revision, which is then approved and
supersedes the old one. Steps are validated by the plant at save time against
its own screens; a bad step is refused with its number, and nothing partial
is stored.
"""
import argparse
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:9030")
ap.add_argument("--user", default="ADMIN")
ap.add_argument("--password-env", default="MES_ADMIN_PASSWORD")
ap.add_argument("--file", default=str(Path(__file__).with_name("walkthroughs.json")))
ap.add_argument("--dry-run", action="store_true", help="only validate the file locally")
a = ap.parse_args()

docs = json.loads(Path(a.file).read_text(encoding="utf-8"))
if a.dry_run:
    from fsmes.services import walkthroughs
    for d in docs:
        walkthroughs.validate_steps(d["steps"])
        walkthroughs.validate_needs(d.get("needs"))
        print("ok", d["code"], len(d["steps"]), "steps")
    sys.exit(0)

pw = os.environ.get(a.password_env)
if not pw:
    sys.exit(f"set {a.password_env} (the {a.user} password) in the environment")
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def call(method, path, body=None):
    req = urllib.request.Request(a.base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with op.open(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")

s, _ = call("POST", "/auth/login", {"code": a.user, "password": pw})
if s != 200:
    sys.exit(f"login as {a.user} failed: {s}")

for d in docs:
    payload = {"title": d["title"], "body": d.get("body", ""), "kind": "walkthrough",
               "steps": d["steps"], "needs": d.get("needs", "plant.read")}
    s, out = call("POST", "/documents", {"code": d["code"], **payload})
    if s == 409:
        s, out = call("POST", f"/documents/{d['code']}/revise", payload)
    if s not in (200, 201):
        sys.exit(f"{d['code']}: {s} {out}")
    rev = out["revision"]
    s, out = call("POST", f"/documents/{d['code']}/approve/{rev}")
    if s != 200:
        sys.exit(f"{d['code']} approve rev {rev}: {s} {out}")
    print(f"{d['code']} rev {rev} {out['status']} — {d['title']}")
