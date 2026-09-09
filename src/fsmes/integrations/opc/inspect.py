"""Look at an OPC UA server before trusting it: browse what's there, verify
what the tag map claims.

Two tools for the first day at a real plant, both driven entirely by the same
settings the agent uses (MES_OPC_ENDPOINT, MES_OPC_SECURITY, MES_TAG_MAP_FILE),
so nothing verified here can differ from what the agent will do:

    browse   walk the server's address space and print it — the ground truth
             Engineering's worksheet gets checked against
    verify   resolve every mapped tag through the agent's own resolution code,
             read each one twice a few seconds apart, and report per machine:
             does it exist, is it changing, does its State value map

Verify's severities are deliberate. A tag that cannot be read, or a State
value the map cannot translate, is a FAIL — the agent would log errors or
mis-book with that map. A machine where nothing changed over the sample is
only a WARN, because a stopped machine is quiet and being quiet is not a
fault. Run verify while the line is running to get the strongest answer.
"""

import asyncio

from asyncua import Client

from fsmes.config import Settings
from fsmes.integrations.opc.agent import resolve_nodes
from fsmes.integrations.opc.security import apply_security, explain_connection_error
from fsmes.integrations.opc.tag_map import load_tag_map


async def _connect(settings: Settings) -> Client:
    client = Client(settings.opc_endpoint)
    await apply_security(client, settings)
    await client.connect()
    return client


# ---------------------------------------------------------------------- browse


async def browse(settings: Settings, *, depth: int = 3, contains: str | None = None) -> int:
    """Print the server's address space: names, node ids, and current values.

    This is the reality check for a worksheet: the node ids Engineering wrote
    down either appear here or they do not. The namespace table is printed
    first because the `ns=2;` prefix in every node id is an index into exactly
    that table — and it is the first thing that differs between two servers
    that otherwise look identical.
    """
    try:
        client = await _connect(settings)
    except Exception as exc:
        print(f"Could not connect to {settings.opc_endpoint}:\n{explain_connection_error(exc)}")
        return 2

    try:
        namespaces = await client.get_namespace_array()
        print(f"Connected to {settings.opc_endpoint}\n")
        print("Namespaces (the ns=N in every node id):")
        for index, uri in enumerate(namespaces):
            print(f"  ns={index}  {uri}")
        print("\nAddress space under Objects/ "
              f"(depth {depth}{f', filtered to *{contains}*' if contains else ''}):\n")
        shown = await _walk(client.nodes.objects, depth, contains, prefix="  ")
        if not shown:
            print("  (nothing matched)" if contains else "  (empty)")
        return 0
    finally:
        await client.disconnect()


async def _walk(node, depth: int, contains: str | None, prefix: str) -> int:
    if depth < 0:
        return 0
    shown = 0
    for child in await node.get_children():
        try:
            browse_name = (await child.read_browse_name()).Name
        except Exception:
            continue
        if browse_name == "Server":
            continue  # the UA server's own diagnostics tree, never plant data
        node_id = child.nodeid.to_string()
        matches = contains is None or contains.lower() in f"{browse_name} {node_id}".lower()
        node_class = (await child.read_node_class()).name
        line = None
        if node_class == "Variable":
            try:
                value = await child.read_value()
            except Exception as exc:
                value = f"<unreadable: {type(exc).__name__}>"
            line = f"{prefix}{browse_name:<28} = {value!r:<16}  [{node_id}]"
        else:
            line = f"{prefix}{browse_name}/"
        if matches and line:
            print(line)
            shown += 1
        shown += await _walk(child, depth - 1, contains, prefix + "  ")
    return shown


# ---------------------------------------------------------------------- verify


async def verify(settings: Settings, *, seconds: int = 10) -> int:
    """Prove the tag map against the live server. 0 = PASS, 1 = FAIL, 2 = no connection."""
    machines = load_tag_map(settings.tag_map_file)
    try:
        client = await _connect(settings)
    except Exception as exc:
        print(f"Could not connect to {settings.opc_endpoint}:\n{explain_connection_error(exc)}")
        return 2

    try:
        # The agent's own resolution — what passes here is what it subscribes to.
        node_info, order_nodes = await resolve_nodes(client, machines, settings.opc_namespace)
        by_machine: dict[str, dict] = {}
        for node, (spec, tag) in node_info.items():
            by_machine.setdefault(spec.equipment, {"spec": spec, "nodes": {}})["nodes"][tag] = node

        print(f"Connected to {settings.opc_endpoint}")
        print(f"Tag map: {settings.tag_map_file} — {len(machines)} machines, {len(node_info)} tags")
        if order_nodes:
            print(f"NOTE: {', '.join(sorted(order_nodes))} are writable (order_tag set). "
                  f"For a plant you only observe, the tag map should say order_tag: null.")
        print(f"Reading everything twice, {seconds}s apart...\n")

        first = await _read_all(by_machine)
        await asyncio.sleep(seconds)
        second = await _read_all(by_machine)

        failures: list[str] = []
        warnings: list[str] = []
        print(f"{'machine':<10} {'tag':<14} {'first':>14} {'second':>14}  note")
        print("-" * 72)
        for code, info in by_machine.items():
            spec = info["spec"]
            moved = False
            for tag in spec.tags:
                a, b = first[code].get(tag), second[code].get(tag)
                note = ""
                if isinstance(a, Exception) or isinstance(b, Exception):
                    error = a if isinstance(a, Exception) else b
                    failures.append(f"{code}.{tag}: cannot read ({type(error).__name__}: {error})")
                    note = "FAIL: unreadable — is the node id right?"
                    a = b = "-"
                else:
                    moved = moved or a != b
                    if tag == "State":
                        try:
                            note = f"-> {spec.to_state(b).value}"
                        except ValueError as exc:
                            failures.append(str(exc))
                            note = "FAIL: state value not in state_map"
                print(f"{code:<10} {tag:<14} {_short(a):>14} {_short(b):>14}  {note}")
            if not moved and not any(f.startswith(f"{code}.") for f in failures):
                warnings.append(
                    f"{code}: nothing changed in {seconds}s — fine if the machine is stopped, "
                    f"suspicious if the line is running"
                )
        print()
        for warning in warnings:
            print(f"  WARN  {warning}")
        for failure in failures:
            print(f"  FAIL  {failure}")
        if failures:
            print(f"\nFAIL: {len(failures)} problem(s). The agent would misbehave with this map.")
            return 1
        print("PASS: every mapped tag is readable and every observed State value maps."
              + (f" ({len(warnings)} warning(s) above.)" if warnings else ""))
        return 0
    finally:
        await client.disconnect()


async def _read_all(by_machine: dict) -> dict[str, dict]:
    """Read every tag individually, keeping exceptions per-tag: one wrong node
    id must report as one broken tag, not poison the whole sample."""
    out: dict[str, dict] = {}
    for code, info in by_machine.items():
        out[code] = {}
        for tag, node in info["nodes"].items():
            try:
                out[code][tag] = await node.read_value()
            except Exception as exc:
                out[code][tag] = exc
    return out


def _short(value) -> str:
    text = str(value)
    return text if len(text) <= 14 else text[:11] + "..."
