"""`fsmes pack migrate`: bring a pack forward one format version at a time.

There is one step so far, and it is the one every pack in this repository was
written by: **format 0 to format 1**.

Format 0 is not a file shape. It is a plant's table in the registry - the
seventeen keys `fsmes.plant` read out of `labs/multiplant/plants.toml` with
`tomllib` and no schema - because that is what a plant was before a pack
existed. Reading one and writing a `plant.toml` is a real migration with a
real receipt, and it is what anybody running plants from a registry today
needs in order to stop.

Three rules, and the third is the one that matters:

* **It says what it moved.** Every key, to the table it landed in.
* **It says what it dropped, and why.** `init` and `post_boot` name code, and
  a pack carries none. `secret_key` is a secret, and a pack carries none.
  `opc_port` became part of an endpoint. None of them is silently lost.
* **It never guesses a value.** A pack needs a time zone and a registry never
  had one, so the migrated pack is written *without* one and the receipt says
  the pack is incomplete until a person sets it. Filling it in from this
  machine's clock would be inventing a fact about a plant, which is house
  rule 2 at the only moment it would be easy to break.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from fsmes import __version__
from fsmes.pack import format as fmt

#: Registry key -> the pack table and key it becomes. The whole of what
#: format 0 could say that format 1 still says.
MOVED: dict[str, tuple[str, str]] = {
    "label": ("plant", "label"),
    "api_host": ("serve", "api_host"),
    "api_port": ("serve", "api_port"),
    "simulate": ("serve", "simulate"),
    "speed": ("serve", "speed"),
    "tag_map": ("files", "tag_map"),
    "replay_dir": ("files", "replay_dir"),
    "database_url": ("storage", "database_url"),
    "database_password_file": ("storage", "database_password_file"),
    "inspect_every": ("floor", "inspect_every"),
    "issue_every": ("floor", "issue_every"),
    "inspect_all": ("floor", "inspect_all"),
    "accounts": ("accounts", ""),
}

#: Registry key -> why a pack does not carry it. Each is a decision, not an
#: omission, and the receipt prints the sentence.
DROPPED: dict[str, str] = {
    "init": ("a pack carries no code (decision 0022). Master data becomes data in "
             "`[files] masterdata`; a plant whose master data is generated keeps its "
             "generator as its own tool and runs it itself."),
    "post_boot": ("a pack carries no code. A scenario script belongs to whoever wrote "
                  "the scenario, and is run by the person running it."),
    "secret_key": ("a pack carries no secret. Put the key in an environment variable and "
                   "name the variable in `[serve] secret_key_env`."),
    "opc_port": ("a port alone could only ever build one endpoint, on loopback, with a "
                 "path this product chose. `[serve] opc_endpoint` is the whole endpoint, "
                 "so a plant can point at a server that is not ours."),
    "agent": ("nothing in the product has ever read it. It was in the registry's "
              "documentation and in no code path, which is the drift a schema exists "
              "to stop."),
}

#: What a registry could not say and a pack must, with the sentence the
#: receipt prints. Never filled in here.
INCOMPLETE: dict[str, str] = {
    "[plant] timezone": ("a registry had no time zone, so this pack has none. Set the "
                         "IANA zone this plant works in; until then every shift boundary "
                         "is drawn in whatever zone the machine happens to be set to."),
}


@dataclass
class Receipt:
    """What a migration did, in the shape `fsmes plant migrate` already uses."""

    plant: str
    written: Path | None = None
    moved: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    unchanged: bool = False

    def render(self) -> list[str]:
        if self.unchanged:
            return [f"  {self.plant}: already at format {fmt.FORMAT}; nothing to do."]
        lines = [f"  {self.plant}: written to {self.written}"]
        lines += [f"      moved     {line}" for line in self.moved]
        lines += [f"      copied    {line}" for line in self.copied]
        lines += [f"      dropped   {line}" for line in self.dropped]
        lines += [f"      set this  {line}" for line in self.incomplete]
        return lines


def migrate(directory: Path) -> Receipt:
    """A pack already in pack form, brought forward. One format exists, so
    this says so rather than pretending to work."""
    pack = fmt.read(directory)
    if pack.format == fmt.FORMAT:
        return Receipt(plant=pack.name or str(directory), unchanged=True)
    raise fmt.PackError(
        f"{directory} states format {pack.format}, and the only step this product knows "
        f"is from a registry entry (format {fmt.REGISTRY_FORMAT}) to format {fmt.FORMAT}. "
        "Pass the registry file and the plant's name instead.")


def from_registry(registry: Path, plant: str, out: Path, *, root: Path | None = None,
                  copy_files: bool = True) -> Receipt:
    """Write a format-1 pack from one plant's table in a registry.

    `root` is the directory the registry's relative paths are written against
    - the repository root for the labs' registries. Files the entry names are
    copied into the pack, because a pack is one directory somebody can hand
    over; the replay directory is not, because it is generated line data that
    is often shared and often enormous.
    """
    registry, out = Path(registry), Path(out)
    root = Path(root) if root is not None else registry.parent
    table = tomllib.loads(registry.read_text(encoding="utf-8"))
    plants = table.get("plants") or {}
    if plant not in plants:
        raise fmt.PackError(
            f"{registry} has no plant called {plant!r}. It lists: {', '.join(plants) or 'none'}.")
    entry = plants[plant]
    receipt = Receipt(plant=plant)

    sections: dict[str, dict] = {
        "pack": {"format": fmt.FORMAT, "requires": f">={_release(__version__)}"},
        "plant": {"name": plant, "profile": "laptop"},
    }
    accounts: list[dict] = []

    for key in sorted(entry):
        if key in DROPPED:
            receipt.dropped.append(f"{key}: {DROPPED[key]}")
            continue
        if key not in MOVED:
            receipt.dropped.append(
                f"{key}: not a key this format has, and nothing in the product read it.")
            continue
        section, into = MOVED[key]
        if section == "accounts":
            accounts = list(entry[key])
            receipt.moved.append(f"{key} -> [[accounts]]")
            continue
        value = entry[key]
        if into in ("tag_map", "replay_dir") and copy_files:
            value = _bring_in(Path(str(value)), out, root, receipt, inside=(into == "tag_map"))
        sections.setdefault(section, {})[into] = value
        receipt.moved.append(f"{key} -> [{section}] {into}")

    if "opc_port" in entry:
        sections.setdefault("serve", {})["opc_endpoint"] = (
            f"opc.tcp://127.0.0.1:{entry['opc_port']}/fsmes/{plant}")
        receipt.moved.append("opc_port -> [serve] opc_endpoint (the whole endpoint)")
    if "secret_key" in entry:
        sections.setdefault("serve", {})["secret_key_env"] = (
            f"FSMES_{plant.upper()}_SECRET_KEY")
        receipt.moved.append("secret_key -> [serve] secret_key_env (the variable, not the key)")
    receipt.incomplete = [f"{where}: {why}" for where, why in INCOMPLETE.items()]

    out.mkdir(parents=True, exist_ok=True)
    (out / fmt.PLANT_FILE).write_text(render(sections, accounts), encoding="utf-8")
    receipt.written = out / fmt.PLANT_FILE
    return receipt


def _bring_in(named: Path, out: Path, root: Path, receipt: Receipt, *, inside: bool) -> str:
    """A file the registry named, as the pack should name it."""
    source = named if named.is_absolute() else root / named
    if not inside:
        # Generated line data: referenced where it lies, never copied. A pack
        # is what a person wrote, and this is what a generator produced.
        return named.as_posix()
    if not source.exists():
        return named.name
    out.mkdir(parents=True, exist_ok=True)
    target = out / source.name
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
        receipt.copied.append(f"{named.as_posix()} -> {source.name}")
    return source.name


def _release(version: str) -> str:
    parts = version.split(".")
    return ".".join(parts[:3]) if len(parts) >= 3 else version


# ------------------------------------------------------------------ writing

def render(sections: dict[str, dict], accounts: list[dict]) -> str:
    """The pack file, written in the schema's own order so two migrated packs
    are diffable against each other."""
    lines = ["# Written by `fsmes pack migrate` from a plant registry entry.",
             "# Check it with `fsmes pack check <this directory>`.",
             ""]
    for section in fmt.SCHEMA:
        if section.repeated or section.name not in sections:
            continue
        table = sections[section.name]
        if not table:
            continue
        lines.append(f"[{section.name}]")
        for key, value in table.items():
            lines.append(f"{key} = {_toml(value)}")
        lines.append("")
    for account in accounts:
        lines.append("[[accounts]]")
        for key, value in account.items():
            lines.append(f"{key} = {_toml(value)}")
        lines.append("")
    return "\n".join(lines)


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'
