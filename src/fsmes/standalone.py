"""A plant from the package: the bottling plant from a pip install, no checkout.

On 2026-10-12 the honest answer to *how much of this could be spun up on a
customer's site?* was **none of it from pip**. The 0.2.0 wheel carried no file
under `labs/`; `fsmes plant` wanted a repository root and
`labs/multiplant/fleet.toml`; `docs/operate/deploy.md` opened with `git clone`.
Everything the lab demonstrates - the eight-hour shift, the four-link chain,
the cold lot's defects, the stop you can click - lived only in a checkout.

This module closes that, and it closes it without inventing a second way to
run a plant. The wheel now carries the two lab packs as package data
(`fsmes/data/packs/{bottling,machining}`, see `pyproject.toml`), and what
follows is the small amount of *arranging* an install with no checkout needs:

1. a **home** - one per-user directory, because site-packages is read-only and
   must stay that way (`~/.local/share/fsmes`, `%LOCALAPPDATA%\\fsmes`,
   `~/Library/Application Support/fsmes`, or `FSMES_HOME`);
2. a **writable copy of the pack** under it, because a pack's `replay_dir` is
   a path inside the pack and the replay is generated, not shipped;
3. a **fleet file** under it listing those copies, with its `[environment]
   data_dir` pointing at the home - which is the one thing
   `FSMES_PLANT_REGISTRY` has always meant, so nothing downstream changes;
4. the **replay**, generated once from `line.json` on first start.

Then it runs `fsmes fleet create`, `fsmes fleet start` and `fsmes fleet stop` -
the commands a checkout runs, unchanged, as child processes, so each gets its
own Settings built from the environment the plant actually has. Nothing here
is a second code path for starting a plant: if this file were deleted, the
checkout path would be exactly as it is.

What it deliberately does **not** do: write anything under site-packages,
ship the replay (9.7 MB for bottling, and deterministic from a seed in
`line.json`), or let a shipped pack become a second source of truth - the
copies in the wheel are diffed against `labs/multiplant/<name>` by the
`wheel-plant` CI job and by `tests/test_the_wheel_ships_the_lab_packs.py`.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

from fsmes import plant as plants

#: Where the wheel keeps its copies of the lab packs, under `fsmes/data/`.
PACKS_IN_PACKAGE = ("data", "packs")

#: The one variable that moves everything a packaged plant writes. Set it and
#: the home below, the pack copies, the databases, the logs and the generated
#: replay all move together; leave it and each platform's own per-user data
#: directory is used.
HOME_ENV = "FSMES_HOME"

#: What the fleet file this module writes says about itself. A person who finds
#: it has to be able to tell that a command wrote it and which one.
FLEET_HEADER = """\
# The fleet of a FactorySemantics MES installed from a package.
#
# Written by `fsmes demo <plant>`, and rewritten by it on every start from the
# pack directories beside this file. It is a normal fleet file: `fsmes plant`,
# `fsmes fleet` and the systemd units all read it when FSMES_PLANT_REGISTRY
# points here, which is what `fsmes demo <plant>` sets for its children.
#
# The packs are copies of the ones the package ships, because a pack names its
# generated line data by a path inside itself and nothing may be written under
# site-packages. Edit them freely - ports, host, time zone - and
# `fsmes demo <plant>` will say that yours differ and keep yours. `--refresh`
# replaces them with the package's.
"""


# --------------------------------------------------------------------- the home


def home() -> Path:
    """The per-user directory a packaged plant lives in.

    One place, chosen by the platform's own convention rather than by a
    dot-directory in `$HOME` on every system, and overridable by `FSMES_HOME`
    for the two cases that need it: a CI job that must leave nothing behind,
    and a plant PC whose user profile is on a network share.
    """
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "fsmes"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "fsmes"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "fsmes"


def packs_dir(where: Path) -> Path:
    return where / "packs"


def data_dir(where: Path) -> Path:
    return where / "data"


def fleet_file(where: Path) -> Path:
    return where / "fleet.toml"


# ------------------------------------------------------------- what is shipped


def shipped() -> list[str]:
    """The packs this installation carries, by directory name, sorted.

    Read from the package rather than from a list in code, so a pack added to
    `pyproject.toml`'s force-include appears here with no second edit. An
    install that carries none - which is every release up to and including
    0.2.0 - gets an empty list and a command that says so.
    """
    try:
        root = resources.files("fsmes").joinpath(*PACKS_IN_PACKAGE)
        return sorted(entry.name for entry in root.iterdir()
                      if entry.joinpath("plant.toml").is_file())
    except (OSError, FileNotFoundError, NotADirectoryError, ModuleNotFoundError):
        return []


def _copy(source: Path, target: Path) -> None:
    """The pack, as a directory, with nothing of the old copy left behind."""
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


def _same(source: Path, target: Path) -> bool:
    """Do these two pack directories hold the same files, byte for byte?

    `out/` is left out of the comparison on purpose: it is generated, it is
    never shipped, and a plant that has run has one while the package never
    does. Everything else being equal is what "this copy is the package's" means.
    """
    def listing(where: Path) -> set[str]:
        return {p.relative_to(where).as_posix() for p in where.rglob("*")
                if p.is_file() and not p.relative_to(where).as_posix().startswith("out/")}

    names = listing(source)
    if names != listing(target):
        return False
    return all(filecmp.cmp(source / n, target / n, shallow=False) for n in names)


def install_pack(name: str, where: Path, *, refresh: bool = False, echo=print) -> Path:
    """A writable copy of a shipped pack under the home, and say what happened.

    Absent: copied. Already there and identical: nothing said. Already there
    and different - the person edited it, or the package was upgraded under
    it - **theirs is kept** and the difference is stated. A command that
    silently reverted the port somebody had changed would be worse than one
    that is one line longer.
    """
    available = shipped()
    if name not in available:
        total = f"{len(available)} pack(s)" if available else "no packs"
        known = f": {', '.join(available)}" if available else (
            " - this release ships none, and `fsmes demo <plant>` needs one")
        raise LookupError(f"This installation carries {total}{known}. There is no {name!r}.")

    target = packs_dir(where) / name
    with resources.as_file(
            resources.files("fsmes").joinpath(*PACKS_IN_PACKAGE, name)) as source:
        if not (target / "plant.toml").is_file():
            _copy(Path(source), target)
            echo(f"  pack: copied {name} out of the package into {target}")
        elif refresh:
            _copy(Path(source), target)
            echo(f"  pack: replaced {target} with the one this installation ships")
        elif not _same(Path(source), target):
            echo(f"  pack: {target} differs from the one this installation ships "
                 f"(edited here, or the package was upgraded under it) - keeping yours; "
                 f"`--refresh` replaces it")
        else:
            echo(f"  pack: {target}")
    return target


def write_fleet(where: Path) -> Path:
    """The fleet file, listing every pack copy under the home.

    Rewritten on every start from what is on disk rather than appended to, so
    a pack directory somebody deleted stops being listed and a second plant
    installed later needs no second edit. Paths stay relative to the fleet
    file - that is what makes a fleet file and its packs movable together -
    and `data_dir` is absolute, because it is a statement about this machine.
    """
    packs = packs_dir(where)
    packs.mkdir(parents=True, exist_ok=True)
    listed = sorted(p.name for p in packs.iterdir() if (p / "plant.toml").is_file())
    file = fleet_file(where)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        FLEET_HEADER
        + "\npacks = [" + ", ".join(f'"packs/{n}"' for n in listed) + "]\n"
        + "\n[environment]\n"
        + f'data_dir = "{data_dir(where).as_posix()}"\n',
        encoding="utf-8")
    return file


# ------------------------------------------------------------------ the replay


def line_description(pack: Path) -> Path | None:
    """`line.json` in the pack, which is what the replay is generated from."""
    candidate = pack / "line.json"
    return candidate if candidate.is_file() else None


def replay_dir(pack: Path) -> Path:
    """Where this pack's generated line data goes.

    Read out of the pack rather than assumed to be `out`, because a pack says
    so in `[files] replay_dir` and the one place that decides is the pack.
    """
    from fsmes.pack import format as fmt

    read = fmt.read(pack)
    stated = read.table("files").get("replay_dir")
    return read.path(str(stated)) if stated else pack / "out"


def has_replay(pack: Path) -> bool:
    out = replay_dir(pack)
    return out.is_dir() and any(out.glob("*.csv"))


def _through(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
             echo=print, indent: str = "    ") -> tuple[int, str]:
    """Run one `fsmes` command, passing its words through as they are written.

    Line by line rather than `capture_output=True`, because first start spends
    a couple of minutes generating a shift and a command that says nothing for
    two minutes is a command a person kills. Returns the exit code and
    everything it said, for the caller that needs to quote it in an error.
    """
    said: list[str] = []
    with subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, bufsize=1) as child:
        assert child.stdout is not None
        for line in child.stdout:
            said.append(line.rstrip())
            if line.strip():
                echo(f"{indent}{line.rstrip()}")
    return child.returncode, "\n".join(said)


def generate_replay(pack: Path, *, echo=print) -> float | None:
    """Generate this pack's line data, and return how long it took.

    `None` when there was nothing to generate: a pack with no `line.json` is
    not broken, it is a pack whose plant does not simulate, and one that
    already has its CSVs is one that has been started before.

    Generated rather than shipped because it is large (six stations x 28,800
    rows is 9.7 MB for bottling, against a 2 MB wheel) and because the
    generator is deterministic - `line.json` carries the seed, so the same
    description gives byte-identical output on every machine that runs it.
    A shipped CSV would be a second copy of a thing a seed already states.
    """
    description = line_description(pack)
    if description is None or has_replay(pack):
        return None
    out = replay_dir(pack)
    echo(f"  replay: generating {out} from {description.name} (once; it is not shipped)")
    started = time.monotonic()
    code, said = _through([plants.fsmes_bin(), "sim-generate", str(description),
                           "--out", str(out)], echo=echo, indent="      ")
    took = time.monotonic() - started
    if code != 0:
        raise RuntimeError(
            f"`fsmes sim-generate {description}` exited {code}:\n{said.strip()}")
    echo(f"  replay: {out} written in {took:.0f} s")
    return took


# ------------------------------------------------------------------- the plant


def prepare(name: str, *, where: Path | None = None, refresh: bool = False,
            echo=print) -> tuple[Path, Path]:
    """Everything a packaged plant needs before a process starts: the home, the
    pack copy, the fleet file, the replay. Returns the home and the pack."""
    where = (where or home()).expanduser()
    where.mkdir(parents=True, exist_ok=True)
    data_dir(where).mkdir(parents=True, exist_ok=True)
    echo(f"  home: {where}")
    pack = install_pack(name, where, refresh=refresh, echo=echo)
    file = write_fleet(where)
    echo(f"  fleet: {file}")
    # Set before anything reads it: `fsmes.plant.environment()` caches the
    # table it finds, and every child process below inherits this.
    os.environ[plants.REGISTRY_ENV] = str(file)
    plants.environment.cache_clear()
    generate_replay(pack, echo=echo)
    return where, pack


def _fsmes(where: Path, *args: str, echo=print) -> int:
    """One `fsmes` command against this home, with its output passed through.

    A child process rather than a call into the same interpreter, for the
    reason `fsmes plant <name> init` already spawns children: a plant's
    settings live in its environment, `Settings` is built once per process and
    cached, and a command that has already built one cannot honestly run
    another plant's.
    """
    code, _ = _through([plants.fsmes_bin(), *args], cwd=where,
                       env={**os.environ, plants.REGISTRY_ENV: str(fleet_file(where))},
                       echo=echo)
    return code


def is_built(name: str, where: Path) -> bool:
    """Has this installation already built this plant in this home?

    Asked of the ownership record, which is the file `fsmes fleet` itself
    reads - not of the database file, which exists the moment anything touches
    it and says nothing about whether the pack was ever applied.
    """
    from fsmes.fleet import owned

    return owned.load(data_dir(where)).entry(name) is not None


def start(name: str, *, where: Path | None = None, speed: float | None = None,
          refresh: bool = False, echo=print) -> int:
    """Bring a packaged plant up, building it first if this home has not got it."""
    where, pack = prepare(name, where=where, refresh=refresh, echo=echo)
    if not is_built(name, where):
        echo(f"  building {name} from {pack} (once)")
        if (code := _fsmes(where, "fleet", "create", str(pack), "--root", str(where),
                           echo=echo)):
            return code
    args = ["fleet", "start", name, "--root", str(where)]
    if speed is not None:
        args += ["--speed", str(speed)]
    return _fsmes(where, *args, echo=echo)


def stop(name: str, *, where: Path | None = None, echo=print) -> int:
    where = (where or home()).expanduser()
    if not fleet_file(where).is_file():
        echo(f"  nothing to stop: no fleet file at {fleet_file(where)}")
        return 1
    os.environ[plants.REGISTRY_ENV] = str(fleet_file(where))
    plants.environment.cache_clear()
    return _fsmes(where, "fleet", "stop", name, "--root", str(where), echo=echo)


def status(name: str, *, where: Path | None = None, echo=print) -> int:
    where = (where or home()).expanduser()
    if not fleet_file(where).is_file():
        echo(f"  no fleet file at {fleet_file(where)} - this home has no plant yet")
        return 1
    os.environ[plants.REGISTRY_ENV] = str(fleet_file(where))
    plants.environment.cache_clear()
    return _fsmes(where, "fleet", "status", name, "--root", str(where), echo=echo)


# ------------------------------------------------------- what to say afterwards


def how_to_sign_in(name: str, pack: Path) -> list[str]:
    """What a person needs on screen once the plant is up: the address, an
    account, and what is switched off on this machine.

    The accounts are the lab list and are named here as the lab passwords they
    are - `fsmes.plant.LAB_ONLY_*` says the same thing in the same words. A
    plant anybody can reach needs a pack with its own `[[accounts]]`; this is
    an evaluation plant on loopback.
    """
    from fsmes.pack import format as fmt

    serve = fmt.read(pack).table("serve")
    host = serve.get("api_host", "127.0.0.1")
    port = serve.get("api_port")
    lines = [
        f"  {name} is on http://{host}:{port}/dashboard",
        "  sign in as SCOTT / operator (an operator), ADMIN / admin, or "
        "FLOOR-SUP / supervisor",
        "  these are lab passwords in the open; a plant anybody else can reach "
        "names its own accounts in its pack",
    ]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        lines.append("  no ANTHROPIC_API_KEY here, so the AI tab's chat will not answer "
                     "and the floor assistant is off; every other screen is unaffected")
    lines.append(f"  stop it with: fsmes demo {name} stop")
    return lines
