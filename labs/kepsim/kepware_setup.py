r"""Build the SimLine channel + 6 Advanced Simulator devices via Kepware's Config API.

    python kepware_setup.py --discover   # print the driver's real property schema, change nothing
    python kepware_setup.py              # create/update channel + devices
    python kepware_setup.py --delete     # remove the channel (and its devices)

Credentials come from .env beside this script (never committed):

    KEP_USER=Administrator
    KEP_PASS=your-password
    KEP_HOST=127.0.0.1
    KEP_PORT=57412

Design note: property names inside a Kepware driver's payload are driver-specific
and undocumented in public material, so this script does NOT hardcode guesses --
it reads the schema from the API's own /config/v1/doc/... endpoints and matches
properties by meaning (data source, table, interval, loop). --discover prints
exactly what it found, which is how the vault note gets its work-observed facts.

Stdlib only, so it runs under any Python on the box.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHANNEL = "SimLine"
DRIVER = "Advanced Simulator"
DSN = "KepSimCSV"
INTERVAL_MS = 1000
DEVICES = ["LD", "RD", "Washer", "QI", "Refill", "Palletiser", "Line"]
# How the ODBC text driver names the tables it finds in the DSN's folder.
TABLE_SUFFIX = ".csv"


# --------------------------------------------------------------------------- config
def load_env() -> dict:
    env = {"KEP_HOST": "127.0.0.1", "KEP_PORT": "57412"}
    f = HERE / ".env"
    if not f.exists():
        sys.exit(f"No .env found at {f}\n"
                 f"Copy .env.example to .env and fill in your Kepware admin credentials.")
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for required in ("KEP_USER", "KEP_PASS"):
        if not env.get(required):
            sys.exit(f".env is missing {required}")
    return env


class Api:
    def __init__(self, env: dict):
        self.base = f"http://{env['KEP_HOST']}:{env['KEP_PORT']}/config/v1"
        token = base64.b64encode(f"{env['KEP_USER']}:{env['KEP_PASS']}".encode()).decode()
        self.auth = f"Basic {token}"

    def _call(self, method: str, path: str, body: dict | None = None):
        url = path if path.startswith("http") else f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self.auth)
        # Kepware's Config API content-negotiates, and its default is the
        # human-readable HTML documentation. Without this header /doc/drivers/...
        # returns a web page, and asking for a driver by name 404s -- which looks
        # like "the driver isn't installed" when it is.
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                detail = json.loads(raw)
            except Exception:
                detail = raw.decode(errors="replace")
            if e.code == 401:
                sys.exit("401 Unauthorized - check KEP_USER / KEP_PASS in .env.\n"
                         "(The Config API user is the Kepware administrator account "
                         "set during installation.)")
            return e.code, detail
        except urllib.error.URLError as e:
            sys.exit(f"Cannot reach the Config API at {self.base} ({e.reason}).\n"
                     f"Is the 'Kepware Server Config API Service' running?")

    get = lambda self, p: self._call("GET", p)                      # noqa: E731
    post = lambda self, p, b: self._call("POST", p, b)              # noqa: E731
    delete = lambda self, p: self._call("DELETE", p)                # noqa: E731


# ----------------------------------------------------------------- schema discovery
def properties_for(api: Api, kind: str) -> list[dict]:
    """kind: 'channels' or 'devices'. Returns the driver's property definitions."""
    driver = urllib.parse.quote(DRIVER)
    for path in (f"/doc/drivers/{driver}/{kind}", f"/doc/drivers/{driver}"):
        status, body = api.get(path)
        if status == 200 and body:
            if isinstance(body, dict) and "property_definitions" in body:
                return body["property_definitions"]
            if isinstance(body, list):
                return body
    return []


def find_prop(props: list[dict], *keywords: str) -> str | None:
    """Match a property by keywords in its symbolic name or display name."""
    for p in props:
        hay = f"{p.get('symbolic_name', '')} {p.get('name', '')} {p.get('display_name', '')}".lower()
        if all(k.lower() in hay for k in keywords):
            return p.get("symbolic_name") or p.get("name")
    return None


def describe(props: list[dict]) -> str:
    out = []
    for p in props:
        sym = p.get("symbolic_name") or p.get("name") or "(unnamed)"
        disp = p.get("display_name") or ""
        default = p.get("default_value")
        out.append(f"    {sym:<55} {disp}  (default={default!r})")
    return "\n".join(out) or "    (none returned)"


# ------------------------------------------------------------------------- commands
def cmd_discover(api: Api) -> int:
    print(f"Driver: {DRIVER}\n")
    for kind in ("channels", "devices"):
        props = properties_for(api, kind)
        print(f"  {kind} properties ({len(props)}):")
        print(describe(props))
        print()
    status, body = api.get("/project/channels")
    print(f"  existing channels: "
          f"{[c.get('common.ALLTYPES_NAME') for c in body] if status == 200 and body else body}")
    return 0


def cmd_delete(api: Api) -> int:
    status, body = api.delete(f"/project/channels/{CHANNEL}")
    print(f"delete {CHANNEL}: HTTP {status} {body if status >= 400 else 'ok'}")
    return 0


def cmd_create(api: Api) -> int:
    ch_props = properties_for(api, "channels")
    dev_props = properties_for(api, "devices")

    dsn_key = find_prop(ch_props, "data", "source") or find_prop(dev_props, "data", "source")
    table_key = find_prop(dev_props, "table")
    interval_key = find_prop(dev_props, "interval")
    loop_key = (find_prop(dev_props, "loop") or find_prop(dev_props, "first", "record")
                or find_prop(dev_props, "wrap"))

    print("Discovered property names:")
    print(f"  data source : {dsn_key}")
    print(f"  table       : {table_key}")
    print(f"  interval    : {interval_key}")
    print(f"  loop        : {loop_key}")
    if not table_key:
        print("\nCould not identify the table property from the API schema. "
              "Run --discover and share the output; the payload needs one correction.")
        return 2

    # ---- channel ----
    channel = {"common.ALLTYPES_NAME": CHANNEL, "servermain.MULTIPLE_TYPES_DEVICE_DRIVER": DRIVER}
    if dsn_key and dsn_key.startswith(("advanced_simulator", "servermain")) and "channel" not in dsn_key:
        channel[dsn_key] = DSN
    status, body = api.post("/project/channels", channel)
    if status in (200, 201):
        print(f"\nchannel '{CHANNEL}' created")
    elif status == 400 and "already exists" in json.dumps(body).lower():
        print(f"\nchannel '{CHANNEL}' already exists - reusing")
    else:
        print(f"\nchannel create: HTTP {status} {body}")
        return 3

    # ---- devices ----
    made = 0
    for name in DEVICES:
        dev = {"common.ALLTYPES_NAME": name,
               "servermain.MULTIPLE_TYPES_DEVICE_DRIVER": DRIVER,
               # The Microsoft Text driver exposes each file as a table named
               # WITH its extension -- "LD.csv", not "LD". Getting this wrong
               # is near-silent: the channel and devices are created happily,
               # then every device logs "not responding" and generates no tags.
               # Confirmed by enumerating the DSN from 32-bit ODBC.
               table_key: f"{name}{TABLE_SUFFIX}"}
        if dsn_key:
            dev.setdefault(dsn_key, DSN)
        if interval_key:
            dev[interval_key] = INTERVAL_MS
        if loop_key:
            dev[loop_key] = True
        status, body = api.post(f"/project/channels/{CHANNEL}/devices", dev)
        if status in (200, 201):
            print(f"  device {name:<11} created")
            made += 1
        elif status == 400 and "already exists" in json.dumps(body).lower():
            print(f"  device {name:<11} already exists")
        else:
            print(f"  device {name:<11} HTTP {status} {body}")

    status, body = api.get(f"/project/channels/{CHANNEL}/devices")
    if status == 200 and body is not None:
        names = [d.get("common.ALLTYPES_NAME") for d in body]
        print(f"\nchannel '{CHANNEL}' now holds {len(names)} devices: {', '.join(names)}")
        print("Tags auto-generate from each table's columns; browse them in the "
              "Kepware config or with OPC Quick Client.")
    return 0 if made or status == 200 else 4


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--discover", action="store_true", help="print driver schema, change nothing")
    ap.add_argument("--delete", action="store_true", help="delete the SimLine channel")
    args = ap.parse_args()

    api = Api(load_env())
    if args.discover:
        return cmd_discover(api)
    if args.delete:
        return cmd_delete(api)
    return cmd_create(api)


if __name__ == "__main__":
    sys.exit(main())
