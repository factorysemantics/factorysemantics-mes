"""no_tenant_literals: no lab plant's identity is hiding inside the product.

The other test the roadmap has promised since M8 was planned. It holds house
rule 4 - *config, not code, at plant boundaries* - and the failure it is here
to catch is the one `labs/multiplant/README.md` names out loud:

    the moment a second plant needs a `seed_northgate.py` shipped inside the
    product, the product has a tenant literal in it.

The forbidden list is **built from the labs, not typed here**, so a new lab
plant extends the guard instead of escaping it. Four sources, and the test
below states the total it built:

* every plant's own name, from every `*.toml` under `labs/` - a pack's
  `[plant] name` and a fleet file's entries alike
* every plant's label, from the `label` the pack or the entry carries
* every equipment code, from every `labs/**/tag_map.json`
* every master-data code a lab writes - the `code` fields in a pack's
  `masterdata/*.json`, and `code="FG-BRACKET"` and the `*_CODE` constants in
  what lab Python is left

M8 piece 3 turned every lab plant into a pack, which moved two of those four
sources: a plant's name is in its own `plant.toml` now rather than in a
shared registry, and its master data is `masterdata/*.json` rather than a
seed script. The collector reads both shapes, because a fleet somebody has
not converted yet is still a fleet whose names must not be in the product.

**What counts as "in the product".** Code, not prose. A comment or a
docstring saying *"found sizing the cutlery plant's day"* is provenance, and
the house rules ask for provenance; deleting it would make the codebase worse
and the guard would be teaching the wrong lesson. What is forbidden is a lab
plant's identity in something that *runs*: a string literal, a dict key, a
default, an identifier. Every `.py` file is parsed and its comments and
docstrings dropped before the scan; web assets are scanned whole, because
there is no lab plant in any of them today and a rule that never fires is
better stated than softened.

**What it does not see.** File names. One migration is named after the lab
whose chain it merged (`..._merge_cutlery_and_walkthrough_chains.py`); nothing
reads that name, and renaming a landed migration to improve a file name is a
worse trade than saying so here. Piece 3's answer to the question it was left:
a pack's name may appear in a file name under `labs/`, and nowhere under
`src/` - `fsmes.pack` names no pack, and a pack directory is found by being
listed in a fleet file, never by being called anything in particular.
"""

import ast
import json
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "fsmes"
LABS = REPO / "labs"

#: Literals the collector picks up that are not a plant's identity, and why.
#: Keep it short and keep every reason concrete: an entry here is a claim that
#: a reader should not worry about this one, and the list is the first place
#: somebody will try to hide a real tenant literal.
NOT_TENANT: dict[str, str] = {
    "ACME Beverages":
        "the shipped demo plant's own name (src/fsmes/seed.py). The lab's bottling "
        "entry reuses it, which is why it lands in the list; the demo plant is "
        "product - `fsmes demo` is the product demonstrating itself - not a tenant.",
    "FG-BOTTLE":
        "the demo plant's finished material, for the same reason: seeded by "
        "src/fsmes/seed_kepsim.py, borrowed by the lab, not owned by a plant.",
    "ADMIN":
        "the built-in administrator account. Product vocabulary that every plant "
        "gets; it is collected only because a lab seed passes it as a `code=`.",
}

#: The demo plant's own six stations. They are in `src/fsmes/seed_kepsim.py`
#: because the six-station bottling line *is* the product's reference line -
#: `fsmes demo` is the product demonstrating itself - and they are collected
#: because the lab's bottling pack runs that same line and therefore carries
#: that same tag map. Same reason as "ACME Beverages" and "FG-BOTTLE" above,
#: one entry each so a seventh station cannot arrive unnoticed.
for _station in ("LD01", "RD01", "WASH01", "QI01", "FILL01", "PAL01"):
    NOT_TENANT[_station] = (
        "a station of the demo plant's own reference line (src/fsmes/seed_kepsim.py). "
        "The lab's bottling pack replays that line, which is why its tag map lands in "
        "the list; the reference line is product, not a tenant.")


#: The rest of that same reference line, collected for the same reason and
#: from 2026-09-14 onwards. Until then the bottling pack carried no master
#: data: its line was seeded by `fsmes seed-kepsim` and by a lab script the
#: registry had stopped naming, so `fsmes fleet create`, `fsmes plant bottling
#: init` and `fsmes score bottling` all built a plant with no machines on it.
#: Putting the line in the pack is what fixed that, and it is what brings
#: these codes into the collector's reach - they are in `src/fsmes/
#: seed_kepsim.py` because the six-station bottling line *is* the product's
#: reference line, not because a tenant's identity leaked into the product.
#:
#: One entry each, and grouped by what they are, so that a genuinely new
#: tenant code cannot arrive inside a group unnoticed.
for _rung, _what in (("ACME", "the enterprise"), ("KC1", "the site"),
                     ("PKG", "the area"), ("SIMLINE", "the line")):
    NOT_TENANT[_rung] = (
        f"{_what} of the demo plant's own reference line (src/fsmes/seed_kepsim.py, "
        "src/fsmes/seed.py). The lab's bottling pack runs that line and therefore "
        "carries the same tree; the reference line is product, not a tenant.")

for _material in ("RAW-PREFORM", "RAW-WATER", "RAW-CAP", "RAW-LABEL", "RAW-CARTON"):
    NOT_TENANT[_material] = (
        "a component of the demo plant's own reference line (src/fsmes/seed_kepsim.py), "
        "same reason as FG-BOTTLE above.")

for _lot in ("LOT-PREFORM-001", "LOT-WATER-001", "LOT-CAP-001", "LOT-LABEL-001",
             "LOT-CARTON-001"):
    NOT_TENANT[_lot] = (
        "the opening stock the demo plant's own reference line starts with "
        "(src/fsmes/seed_kepsim.py); it is seeded by the product, not by a plant.")

for _plan in ("PM-FILL-SEALS", "PM-LD-BELT", "PM-WASH-NOZZLE", "PM-PAL-GREASE",
              "PM-RD-BEARING"):
    NOT_TENANT[_plan] = (
        "a maintenance plan on the demo plant's own reference line "
        "(src/fsmes/seed_kepsim.py); deliberately short intervals so a "
        "demonstration line comes due within a shift.")

NOT_TENANT["RT-BOTTLE"] = (
    "the routing of the demo plant's own reference line (src/fsmes/seed_kepsim.py), "
    "same reason as FG-BOTTLE, the material it makes.")

for _shift in ("DAY", "NIGHT"):
    NOT_TENANT[_shift] = (
        "a shift code. Product vocabulary that every plant gets - the two patterns "
        "src/fsmes/seed_kepsim.py seeds and src/fsmes/web/schedule.html renders - "
        "collected only because the lab's bottling pack now carries the same two.")


# --------------------------------------------------------- the forbidden list


def forbidden() -> dict[str, str]:
    """Every literal that belongs to a lab plant, mapped to what it is."""
    found: dict[str, str] = {}

    def add(kind: str, value) -> None:
        if value and str(value).strip():
            found.setdefault(str(value).strip(), kind)

    def named(name: str, label) -> None:
        add("a lab plant's registry name", name)
        if isinstance(label, str) and label:
            # "Northgate Machining / Cell A - 3-station machining cell": the
            # plant's name is the head of the label, before the first
            # separator. What follows describes the line, not the plant.
            add("a lab plant's name", re.split(r"[—/(]", label)[0])

    for written in sorted(LABS.rglob("*.toml")):
        table = tomllib.loads(written.read_text(encoding="utf-8"))
        # A pack: one plant, which says its own name.
        if isinstance(table.get("plant"), dict) and table["plant"].get("name"):
            named(table["plant"]["name"], table["plant"].get("label"))
        # A registry from before packs: several plants in one file.
        for name, entry in (table.get("plants") or {}).items():
            named(name, entry.get("label") if isinstance(entry, dict) else None)

    for data in sorted(LABS.rglob("masterdata/*.json")):
        rows = json.loads(data.read_text(encoding="utf-8"))
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                add("a lab plant's master-data code", row.get("code"))

    for tag_map in sorted(LABS.rglob("tag_map.json")):
        machines = json.loads(tag_map.read_text(encoding="utf-8")).get("machines") or []
        for machine in machines:
            if isinstance(machine, dict):
                add("a lab plant's equipment code", machine.get("equipment"))

    written = re.compile(r'code\s*[=:]\s*"([A-Za-z0-9][A-Za-z0-9._-]{2,})"')
    constant = re.compile(r'^[A-Z_]*CODE[A-Z_]*\s*=\s*"([^"]+)"', re.M)
    for seed in sorted(LABS.rglob("*.py")):
        text = seed.read_text(encoding="utf-8")
        for code in written.findall(text) + constant.findall(text):
            add("a lab plant's master-data code", code)

    for excused in NOT_TENANT:
        found.pop(excused, None)
    return found


# ------------------------------------------------------------ what runs


def _code_only(path: Path) -> str:
    """A Python file with its comments and docstrings removed.

    What is left is what runs: string literals, names, attributes. Built from
    the AST rather than by stripping `#` lines, so a `#` inside a string does
    not take the rest of the line with it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))

    pieces: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                pieces.append(node.value)
        elif isinstance(node, ast.Name):
            pieces.append(node.id)
        elif isinstance(node, ast.Attribute):
            pieces.append(node.attr)
        elif isinstance(node, ast.arg):
            pieces.append(node.arg)
    return "\n".join(pieces)


def product() -> dict[str, str]:
    """Every file under `src/fsmes` a literal could hide in, as searchable text."""
    scanned: dict[str, str] = {}
    for path in sorted(SRC.rglob("*")):
        if not path.is_file():
            continue
        # `as_posix`, not `str`: the key is a path a person reads in a failure
        # message and a test asserts on, and it must say the same thing on
        # Windows as it does on Linux.
        where = path.relative_to(REPO).as_posix()
        if path.suffix == ".py":
            scanned[where] = _code_only(path)
        elif path.suffix in {".js", ".html", ".css", ".json"}:
            scanned[where] = path.read_text(encoding="utf-8", errors="replace")
    return scanned


def _pattern(literal: str) -> re.Pattern:
    """A whole-word match. Case-insensitive for a registry name, which is a
    lowercase word somebody could capitalise; exact for a code, where case is
    part of the code."""
    flags = re.IGNORECASE if literal.islower() else 0
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(literal) + r"(?![A-Za-z0-9_])", flags)


def hits(literals: dict[str, str], files: dict[str, str]) -> list[str]:
    """Every forbidden literal found in something that runs, as sentences."""
    out = []
    for literal, kind in sorted(literals.items()):
        pattern = _pattern(literal)
        for where, text in sorted(files.items()):
            if pattern.search(text):
                out.append(f"{where} contains {literal!r} - {kind}")
    return out


# -------------------------------------------------------------------- rules


def test_no_lab_plants_name_or_code_appears_in_the_product():
    """House rule 4, as a ratchet. If adding a plant needs a change under
    `src/`, that change is the bug - and this is the measurement."""
    literals = forbidden()
    found = hits(literals, product())
    assert not found, (
        f"a lab plant's identity is inside the product. {len(literals)} literals were "
        "read from labs/ and these are in code that runs. Move the value into the "
        "plant registry (or, in piece 3, the plant pack) and read it from there; if "
        "it genuinely belongs to the product rather than to a plant, add it to "
        "NOT_TENANT with the reason:\n  " + "\n  ".join(found))


def test_the_forbidden_list_is_read_from_the_labs_and_not_typed_here():
    """A hand-typed list stops growing the day somebody adds a plant. This
    one is built from the labs, so a new lab plant extends the guard on the
    day it lands."""
    literals = forbidden()
    assert len(literals) > 100, (
        f"only {len(literals)} literals were read from labs/; the collector has "
        "stopped finding things")

    kinds = {kind for kind in literals.values()}
    assert kinds == {"a lab plant's registry name", "a lab plant's name",
                     "a lab plant's equipment code",
                     "a lab plant's master-data code"}, kinds

    # The lab plants this repository ships, by name, so a lab that disappears
    # or is renamed is noticed here rather than by the guard quietly relaxing.
    # `finewire` is the third pack, invented in M8 piece 3 to disagree with
    # the pack format: it extended this guard on the day it landed, which is
    # what building the list from the labs is for.
    names = {literal for literal, kind in literals.items()
             if kind == "a lab plant's registry name"}
    assert names == {"bottling", "machining", "finewire", "cutlery",
                     "megafactory"}, names


def test_the_check_catches_a_plant_name_pasted_into_a_service():
    """The guard's own guard, and the exact failure the handoff names: a lab
    plant's name pasted into `src/fsmes/services/line.py`. Handed to the
    checker rather than written to disk, so the demonstration is in the suite
    instead of in a commit somebody has to remember to revert."""
    literals = forbidden()

    pasted = {"src/fsmes/services/line.py": 'if plant == "machining":\n    buffer = 6\n'}
    caught = hits(literals, pasted)
    assert caught and "machining" in caught[0], caught
    assert "services/line.py" in caught[0]

    # An equipment code from a lab's tag map is caught the same way.
    assert hits(literals, {"src/fsmes/services/line.py": 'SAW01'})

    # And a file that says nothing about any plant stays quiet.
    assert not hits(literals, {"src/fsmes/services/line.py": "def layout(session):\n    pass\n"})


def test_prose_is_not_a_tenant_literal():
    """Provenance is not a boundary violation. A comment saying which plant a
    finding came from is the codebase explaining itself, and the house rules
    ask for exactly that - so the scan drops comments and docstrings first.
    This pins the distinction, because the cheapest way to make the test above
    pass would be to delete the sentences that make the code readable."""
    module = SRC / "services" / "serialization.py"
    assert "cutlery" in module.read_text(encoding="utf-8"), (
        "this test is pinned to a real comment that names the lab the module was "
        "sized against; if it moved, point the test at another one rather than "
        "removing the test")
    assert "cutlery" not in _code_only(module)


def test_the_scan_still_reads_the_product():
    """A ratchet that has quietly stopped reading passes forever."""
    files = product()
    assert len(files) > 100, f"only {len(files)} files scanned under src/fsmes"
    assert "src/fsmes/services/line.py" in files
    assert "src/fsmes/web/app.js" in files
