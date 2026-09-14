"""core_purity: the kernel does not import a module, and no layer imports upward.

The roadmap has promised this test since M8 was planned. Until now the rule
lived only as a docstring in `src/fsmes/kernel/__init__.py`:

    domain <- services <- {api, connect, modules, cli}

and decision [0002](../docs/decisions/0002-kernel-and-modules.md) said in so
many words that the module boundary was "a discipline, not a wall". This is
the wall. It matters now because a module cannot be switched off while the
kernel imports it: `MES_MODULES` can decline to mount a router, but it cannot
un-import a package the kernel needs to start.

Same shape as the other tests that read the source rather than run it - the
shadow ratchet in `test_shadow_mode.py`, `test_mcp_parity.py`,
`test_route_coverage.py`. An AST walk over every file in `src/fsmes` collects
every `fsmes` import, each is placed on the ladder below, and an import that
goes upward must appear in one of the two allowance tables with a reason.

The ladder, established from the code rather than from the architecture page,
which disagrees with it (the page proposes `packs/` and `modules/` directories
that do not exist, and describes a kernel that actually lives in `domain/` and
`services/`; it says up front that it is intent rather than description).
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "fsmes"

#: The layers, lowest first. Every top-level module and package under
#: `src/fsmes` is on exactly one of them; a new one that is on none fails the
#: last test in this file, which is the point - somebody has to decide where
#: it sits rather than letting it sit nowhere.
LADDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("foundations", (
        # Primitives and settings. Nothing here knows what a work order is,
        # except `db` and `schema`, which know only how to reach one.
        "core", "kernel", "config", "shadow", "db", "schema", "logging",
        "identity",  # the plant's own name and clock: settings-level, imports nothing above
        # Which database a command is looking at, and what that database says
        # about its schema. Beside `schema` and for the same reason: it knows
        # how to reach a database and nothing about what is in one. A pack's
        # database and a plant's are looked up where packs and fleets are
        # read, and land back here.
        "storage",
        "modules", "plant", "__init__",
    )),
    ("domain", (
        # The tables and their relationships. Imports `db` and itself; that
        # is the whole of its dependency list, and it was already true before
        # this test existed.
        "domain",
    )),
    ("services", (
        # Every rule the product enforces. A service may reach down to the
        # domain and sideways to another service; it may not reach up.
        "services",
    )),
    ("edges", (
        # Where the product meets something else: HTTP, OPC UA, MQTT, an ERP,
        # an agent, a simulator, a migration runner, a seed script.
        "api", "integrations", "mcp", "mcp_server", "sim", "migrations",
        "seed", "seed_line", "seed_kepsim", "demo_feed", "backup",
        # The plant pack: a format that reads a directory and writes a plant.
        # An edge, not a foundation, and deliberately so - checking a pack
        # means running each file's *own* validator (the tag map's, the
        # inbound mapping's) and applying one means writing master data
        # through the services, so the module that does it sits where those
        # live. `fsmes.plant` stays below it and never imports it: a pack is
        # compiled into the plain dictionary that module already ran.
        "pack",
        # The fleet tooling: it reads packs, asks plants over HTTP what they
        # say about themselves, and hands starting and stopping back to
        # `fsmes.plant`. An edge for the same reason `pack` is one, and it
        # sits beside it rather than above it because nothing below may
        # import it.
        "fleet",
    )),
    ("cli", (
        # The top of the ladder: everything may be imported by the CLI and
        # the CLI may be imported by nothing.
        "cli",
    )),
)

HEIGHT: dict[str, int] = {
    top: index for index, (_, tops) in enumerate(LADDER) for top in tops}
LAYER_NAME: dict[str, str] = {
    top: name for name, tops in LADDER for top in tops}

#: The kernel, in the sense this milestone needs: the part of the product that
#: is present whatever a plant pack says. A module is switchable only if the
#: kernel can start without it, so nothing here may import a module at all.
KERNEL = ("domain", "kernel", "core", "db", "config", "schema", "shadow",
          "logging", "modules")

#: What a module is, for the same purpose: the packages that hold a module's
#: rules, routes, tools and adapters.
MODULE_PACKAGES = ("services", "api", "mcp", "mcp_server", "integrations",
                   "sim", "cli")

#: Upward imports that are allowed because the thing imported is a *contract*
#: - typed models describing what crosses a border, carrying no behaviour.
#: A service reading `integrations.erp.contract` is reading a shape, not
#: calling an adapter, and the shape has to live with the adapter that
#: implements it. Each is checked, not just asserted: the test below fails if
#: one of these targets ever grows an import of its own layer or above, which
#: is what a contract growing behaviour would look like.
CONTRACTS: dict[tuple[str, str], str] = {
    ("fsmes.services.inbound", "fsmes.integrations.inbound.contract"):
        "the typed inbound contract - what another system may tell this MES",
    ("fsmes.services.erp", "fsmes.integrations.erp.contract"):
        "the typed ERP contract - what crosses that border, in both directions",
    ("fsmes.services.outbox", "fsmes.integrations.events"):
        "the typed domain-event contract - what the outbox carries that no ERP asked for",
    ("fsmes.services.line", "fsmes.integrations.opc.tag_map"):
        "the tag map is the wiring diagram; the line view reads it to lay out what it draws",
    ("fsmes.services.tags", "fsmes.integrations.opc.tag_map"):
        "the same file, read by the service that answers what a machine's tags are called",
}

#: Upward imports that are allowed because they happen at call time rather
#: than import time - the module does not need the thing to be importable in
#: order to be imported itself, which is what keeps the package loadable in a
#: deployment that has not installed it. Checked too: the test below fails if
#: one of these moves to module scope.
DEFERRED: dict[tuple[str, str], str] = {
    ("fsmes.services.agent", "fsmes.mcp_server"):
        "a plant running the assistant in-process calls its own MCP tools; the "
        "server is imported inside the function that calls it, so a plant "
        "installed without the mcp extra still imports fsmes.services",
}


# ------------------------------------------------------------------ reading


def _module_name(file: Path) -> str:
    parts = file.relative_to(SRC).with_suffix("").parts
    return ("fsmes." + ".".join(parts)).removesuffix(".__init__")


def _top(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else "__init__"


def imports() -> dict[str, list[tuple[str, int, bool]]]:
    """Every `fsmes` import in the package: importer -> (imported, line, deferred).

    `deferred` is true for an import inside a function or a method, which is
    a dependency at call time but not at import time. Relative imports are
    resolved against the importing module so `from . import x` is not a hole.
    """
    found: dict[str, list[tuple[str, int, bool]]] = {}
    for file in sorted(SRC.rglob("*.py")):
        me = _module_name(file)
        package = me.rsplit(".", 1)[0] if file.name != "__init__.py" else me
        tree = ast.parse(file.read_text(encoding="utf-8"))
        inside: list[tuple[str, int, bool]] = []

        def walk(node, deferred=False, inside=inside, package=package):
            for child in ast.iter_child_nodes(node):
                nested = deferred or isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef))
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        if alias.name.split(".")[0] == "fsmes":
                            inside.append((alias.name, child.lineno, deferred))
                elif isinstance(child, ast.ImportFrom):
                    base = package if child.level else (child.module or "")
                    if child.level:
                        for _ in range(child.level - 1):
                            base = base.rsplit(".", 1)[0]
                    if base.split(".")[0] == "fsmes":
                        for alias in child.names:
                            inside.append((f"{base}.{alias.name}", child.lineno, deferred))
                walk(child, nested, inside, package)

        walk(tree)
        if inside:
            found[me] = inside
    return found


def _resolves_to(imported: str, modules: set[str]) -> str:
    """An `import` names a module or a name inside one. Longest match wins,
    so `fsmes.services.erp` and `fsmes.services.erp.confirm` both land on the
    module that actually exists."""
    candidate = imported
    while candidate:
        if candidate in modules:
            return candidate
        if "." not in candidate:
            break
        candidate = candidate.rsplit(".", 1)[0]
    return imported


def upward(edges: dict[str, list[tuple[str, int, bool]]]) -> list[str]:
    """Every import that climbs the ladder and is not allowed, as sentences.

    Takes the edges rather than reading them, so the test below can hand it a
    violation that is not in the repository and prove the check catches one.
    """
    out = []
    for importer, items in sorted(edges.items()):
        here = HEIGHT.get(_top(importer))
        if here is None:
            continue
        for imported, line, deferred in items:
            there = HEIGHT.get(_top(imported))
            if there is None or there <= here:
                continue
            pair = (importer, imported)
            if pair in CONTRACTS or pair in DEFERRED:
                continue
            # Allowances are written against the module, not against every
            # name inside it: `from fsmes.integrations.erp.contract import X`
            # is the same dependency as importing the module.
            if any(imported.startswith(target + ".")
                   for source, target in list(CONTRACTS) + list(DEFERRED)
                   if source == importer):
                continue
            kind = "at call time" if deferred else "at import time"
            out.append(
                f"{importer}:{line} imports {imported} {kind} - "
                f"{LAYER_NAME[_top(importer)]} may not import "
                f"{LAYER_NAME[_top(imported)]}")
    return out


# -------------------------------------------------------------------- rules


def test_the_kernel_imports_nothing_from_a_module():
    """The rule this milestone actually needs. A module that the kernel
    imports cannot be switched off: `MES_MODULES` can decline to mount its
    routes, but the kernel would still drag the package in at start-up."""
    modules = {_module_name(f) for f in SRC.rglob("*.py")}
    offences = []
    for importer, items in sorted(imports().items()):
        if _top(importer) not in KERNEL:
            continue
        for imported, line, _ in items:
            if _top(imported) in MODULE_PACKAGES:
                offences.append(
                    f"{importer}:{line} imports {_resolves_to(imported, modules)}")
    assert not offences, (
        "the kernel (" + ", ".join(KERNEL) + ") imports a module. A module the "
        "kernel imports cannot be switched off by a plant pack, which is the "
        f"capability M8 is built on: {offences}")


def test_no_layer_imports_a_layer_above_it():
    """The ladder from `fsmes/kernel/__init__.py`, held. Sideways is fine -
    a service may call another service - and downward is the point."""
    climbing = upward(imports())
    assert not climbing, "\n".join(climbing)


def test_the_checker_catches_a_violation_it_was_not_given():
    """The guard's own guard. A test that reads source and finds nothing is
    indistinguishable from a test that has stopped reading, so hand the check
    the violation the handoff names and watch it fail."""
    pasted = {"fsmes.domain.equipment": [("fsmes.services.line", 12, False)]}
    caught = upward(pasted)
    assert caught and "domain may not import services" in caught[0], caught

    # The same import from a place that is allowed to make it stays quiet.
    assert not upward({"fsmes.api.routers.line": [("fsmes.services.line", 12, False)]})

    # An allowance covers the pair it names and nothing else.
    assert not upward({"fsmes.services.tags": [("fsmes.integrations.opc.tag_map", 38, False)]})
    assert upward({"fsmes.services.tags": [("fsmes.integrations.opc.agent", 38, False)]})


def test_the_scan_still_reads_the_package():
    """A ratchet that has quietly stopped parsing passes forever."""
    edges = imports()
    assert len(edges) > 80, f"only {len(edges)} modules import anything; the scan has broken"
    assert "fsmes.services.tags" in edges and "fsmes.api.app" in edges


# ------------------------------------------------------- the allowances hold


def test_every_contract_a_service_reaches_up_for_is_really_a_contract():
    """An allowance that nothing checks is a hole with a comment on it. A
    contract carries models, not behaviour, and the way that shows in the
    import graph is that it reaches no further up than the domain."""
    modules = {_module_name(f) for f in SRC.rglob("*.py")}
    edges = imports()
    for (importer, target), reason in CONTRACTS.items():
        assert target in modules, f"{importer} is allowed to import {target}, which is gone"
        assert reason
        there = HEIGHT[_top(target)]
        for imported, line, _ in edges.get(target, []):
            assert HEIGHT.get(_top(imported), -1) < there, (
                f"{target} is allowed up the ladder because it is a contract, and it "
                f"now imports {imported} at line {line}. Either that import goes, or "
                "the allowance does.")


def test_every_deferred_allowance_is_still_deferred():
    """The other allowance, held to its own reason: these are call-time
    imports, and an import that moves to module scope is a different
    dependency wearing the same name."""
    edges = imports()
    for (importer, target), reason in DEFERRED.items():
        assert reason
        matching = [d for imported, _, d in edges.get(importer, [])
                    if imported == target or imported.startswith(target + ".")]
        assert matching, f"{importer} no longer imports {target}; remove the allowance"
        assert all(matching), (
            f"{importer} now imports {target} at module scope. It was allowed up the "
            "ladder only because it did not.")


def test_the_app_skeleton_names_no_router_and_the_mcp_server_names_no_tool_file():
    """What makes `MES_MODULES` able to filter anything. If the app imported
    its routers by name, every module would be loaded before the setting was
    read, and switching one off would mount nothing while still importing
    everything."""
    edges = imports()
    for skeleton, forbidden, registry in (
            ("fsmes.api.app", "fsmes.api.routers", "fsmes.modules"),
            ("fsmes.mcp_server", "fsmes.mcp.", "fsmes.modules")):
        named = [f"{imported} (line {line})" for imported, line, _ in edges[skeleton]
                 if imported.startswith(forbidden)]
        assert not named, (
            f"{skeleton} imports {forbidden}* by name: {named}. It mounts what "
            f"{registry} lists, filtered by MES_MODULES, and imports each by name "
            "at the moment it mounts it.")


def test_every_module_in_the_package_is_on_the_ladder():
    """A new top-level package has to be placed, deliberately, by a person.
    One that is on no layer is exempt from every rule in this file, silently,
    which is the failure mode a layering test is most likely to die of."""
    tops = {_top(_module_name(f)) for f in SRC.rglob("*.py")}
    unplaced = sorted(t for t in tops if t not in HEIGHT)
    assert not unplaced, (
        f"{len(tops)} top-level modules under src/fsmes, and these are on no layer: "
        f"{unplaced}. Add each to LADDER at the height it belongs.")
