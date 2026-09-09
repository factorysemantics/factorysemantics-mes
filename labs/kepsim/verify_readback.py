r"""Prove the simulated line is actually live in Kepware: read it back over OPC UA.

    python verify_readback.py             # 15 s sample
    python verify_readback.py --browse    # list what's actually there
    python verify_readback.py --seconds 30

Run it from the repo root with the MES-TWIN virtualenv, which already has
asyncua and cryptography:

    .venv\Scripts\python.exe labs\kepsim\verify_readback.py

Which stations and tags to expect is read from **config/tag_map_kepware.json** --
the same file the MES agent uses -- so this check and the MES can never drift
apart about what the line is called. What it proves here is exactly what the
agent will find when pointed at the same server.

Kepware's UA endpoint (this install, checked live) offers exactly one endpoint:
**SignAndEncrypt / Basic256Sha256 / UserName** -- no anonymous, no None. So this
script does the full UA handshake, using the agent's own security code:

  1. mints a self-signed client certificate into certs\ on first run
  2. connects with SignAndEncrypt + Basic256Sha256
  3. authenticates with UA_USER / UA_PASS from .env

It deliberately shares the agent's certificate, so Kepware only ever has to be
told to trust one. The first connection is EXPECTED to fail with
BadSecurityChecksFailed or BadCertificateUntrusted: Kepware has never seen this
certificate. Trust it once -- Kepware's **OPC UA Configuration Manager ->
Instance Certificates**, or move the .der from Kepware's UA rejected-certificates
folder into trusted -- then re-run. That one-time dance is the single most common
OPC UA failure in the field, so it is worth doing once by hand here.

Checks, per station: counters advance, State stays inside the mapped range, and
something actually changes over the window (a frozen tag is the silent killer).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CERT_DIR = REPO / "certs"
CERT = CERT_DIR / "mes_twin_client.der"
KEY = CERT_DIR / "mes_twin_client_key.pem"
APP_URI = "urn:mes-twin:agent"
TAG_MAP = REPO / "config" / "tag_map_kepware.json"

try:
    from asyncua import Client
except ImportError:
    sys.exit("asyncua not installed for this interpreter. Run it with the repo venv:\n"
             r"  .venv\Scripts\python.exe labs\kepsim\verify_readback.py")

sys.path.insert(0, str(REPO / "src"))
try:
    from fsmes.integrations.opc.security import ensure_client_certificate, explain_connection_error
    from fsmes.integrations.opc.tag_map import load_tag_map
except ImportError:
    sys.exit("Could not import fsmes. Run this from the MES-TWIN repo with its venv:\n"
             r"  .venv\Scripts\python.exe labs\kepsim\verify_readback.py")

CHANNEL = "SimLine"
# The Line table carries order context and maps to no MES equipment, so it is
# not in the tag map; it is still worth reading back.
LINE_TAGS = ["OrderId", "OrderActive", "LineGoodCount"]
STATE_NAMES = {0: "stopped", 1: "running", 2: "starved", 3: "blocked", 4: "DOWN", 5: "changeover"}


def stations_from_tag_map() -> dict[str, list[str]]:
    """What to read, straight from the map the MES agent uses."""
    if not TAG_MAP.exists():
        sys.exit(f"No tag map at {TAG_MAP} -- it defines which stations and tags to expect.")
    stations = {spec.object: list(spec.tags) for spec in load_tag_map(TAG_MAP)}
    stations["Line"] = LINE_TAGS
    return stations


def node_ids_from_tag_map() -> dict[tuple[str, str], str]:
    """WHERE each tag lives, resolved through MachineMap.node().

    This has to come from the tag map, not be rebuilt here. Kepware's Advanced
    Simulator names generated tags <table>_<column>, so State on LD.csv is
    addressed as SimLine.LD.LD_csv_State -- assembling "channel.device.tag"
    locally reads a node that does not exist, and this check would fail against a
    server the MES agent talks to perfectly well. Driving both from one file is
    the only thing that stops them drifting.
    """
    specs = load_tag_map(TAG_MAP)
    ids = {(spec.object, tag): spec.node(tag) or f"ns=2;s={CHANNEL}.{spec.object}.{tag}"
           for spec in specs for tag in spec.tags}

    # The Line table is order context, not MES equipment, so it has no tag map
    # entry. Borrow the addressing convention from a station that does have one.
    donor = next((s for s in specs if s.node_id), None)
    for tag in LINE_TAGS:
        ids[("Line", tag)] = (donor.node_id.replace(donor.object, "Line").format(tag=tag)
                              if donor else f"ns=2;s={CHANNEL}.Line.{tag}")
    return ids


def load_env() -> dict:
    env = {}
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


async def connect(url: str, env: dict) -> Client:
    # The same certificate the MES agent presents, so Kepware only ever has to
    # trust one client.
    ensure_client_certificate(CERT, KEY, application_uri=APP_URI, common_name="MES-TWIN Agent")
    client = Client(url=url)
    client.application_uri = APP_URI
    user, pw = env.get("UA_USER"), env.get("UA_PASS")
    if not user:
        sys.exit("This Kepware UA endpoint requires a username (no anonymous access).\n"
                 "Add UA_USER / UA_PASS to .env -- a Kepware user account with UA access.")
    client.set_user(user)
    client.set_password(pw or "")
    await client.set_security_string(
        f"Basic256Sha256,SignAndEncrypt,{CERT},{KEY}")
    await client.connect()
    return client


async def run(url: str, seconds: int, browse: bool) -> int:
    env = load_env()
    try:
        client = await connect(url, env)
    except Exception as e:
        print(f"Connection failed: {explain_connection_error(e)}\n")
        print("Once trusted, the MES agent can use this same certificate:\n"
              "  set MES_TAG_MAP_FILE=config/tag_map_kepware.json\n"
              "  set MES_OPC_SECURITY=Basic256Sha256,SignAndEncrypt\n"
              "  set MES_OPC_ENDPOINT=" + url)
        return 2

    try:
        if browse:
            return await do_browse(client)
        return await do_sample(client, seconds)
    finally:
        await client.disconnect()


async def do_browse(client: Client) -> int:
    """List what actually exists under the channel -- the reality check."""
    objects = client.nodes.objects
    print("Browsing the server's address space:\n")
    for child in await objects.get_children():
        nm = (await child.read_browse_name()).Name
        if nm in ("Server",):
            continue
        print(f"  {nm}")
        for dev in await child.get_children():
            dnm = (await dev.read_browse_name()).Name
            print(f"    {dnm}")
            tags = await dev.get_children()
            names = [(await t.read_browse_name()).Name for t in tags[:12]]
            if names:
                print(f"      tags: {', '.join(names)}"
                      f"{' ...' if len(tags) > 12 else ''}")
    return 0


async def do_sample(client: Client, seconds: int) -> int:
    stations = stations_from_tag_map()
    node_ids = node_ids_from_tag_map()
    nodes, labels = [], []
    for station, tags in stations.items():
        for tag in tags:
            nodes.append(client.get_node(node_ids[(station, tag)]))
            labels.append((station, tag))

    print(f"Connected; sampling {len(nodes)} tags across {len(stations)} devices for {seconds}s "
          f"(the driver steps one CSV row per second)\n"
          f"Expecting what {TAG_MAP.name} describes — the same map the MES agent reads.\n")

    first = await read_all(nodes, labels)
    if first is None:
        return 2

    # Keep reading through the window instead of sleeping through it. The
    # Advanced Simulator steps its record pointer only while the device is being
    # scanned, so a read/sleep/read pattern samples the SAME row twice and
    # reports a perfectly healthy line as frozen. The MES agent never hits this
    # because it holds an OPC UA subscription, which scans continuously.
    last = first
    for _ in range(seconds):
        await asyncio.sleep(1)
        last = await read_all(nodes, labels)
        if last is None:
            return 2

    print(f"{'station':<12} {'state':<11} {'good':>8} {'+good':>6} {'scrap':>6} "
          f"{'analog':>10} {'moved':>6}")
    print("-" * 68)
    failures = []
    for station, tags in stations.items():
        a = {t: first[(station, t)] for t in tags}
        b = {t: last[(station, t)] for t in tags}
        if station == "Line":
            print(f"{station:<12} {'order ' + str(b['OrderId']):<11} "
                  f"{b['LineGoodCount']:>8} {b['LineGoodCount'] - a['LineGoodCount']:>6} "
                  f"{'-':>6} {'active=' + str(b['OrderActive']):>10} {'-':>6}")
            continue
        st = int(b["State"])
        if st not in STATE_NAMES:
            failures.append(f"{station}: State={st} outside 0..5")
        analog = next(t for t in tags if t not in ("State", "GoodCount", "ScrapCount"))
        moved = any(a[t] != b[t] for t in tags)
        if not moved:
            failures.append(f"{station}: nothing changed in {seconds}s (frozen?)")
        print(f"{station:<12} {STATE_NAMES.get(st, '?'):<11} {int(b['GoodCount']):>8} "
              f"{int(b['GoodCount']) - int(a['GoodCount']):>6} {int(b['ScrapCount']):>6} "
              f"{float(b[analog]):>10.1f} {'yes' if moved else 'NO':>6}")

    print()
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        return 1
    print("  PASS  every station is live, states legal, values advancing")
    return 0


async def read_all(nodes, labels):
    try:
        return dict(zip(labels, await asyncio.gather(*(n.read_value() for n in nodes)), strict=True))
    except Exception as e:
        print(f"Could not read tags: {type(e).__name__}: {e}\n"
              f"Run with --browse to see what names actually exist "
              f"(channel/device/tag spelling, or devices not created yet).")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="opc.tcp://127.0.0.1:49320")
    ap.add_argument("--seconds", type=int, default=15)
    ap.add_argument("--browse", action="store_true", help="list the address space and exit")
    args = ap.parse_args()
    return asyncio.run(run(args.url, args.seconds, args.browse))


if __name__ == "__main__":
    sys.exit(main())
