"""The fleet as a deployment script needs it: one answer, in JSON.

`deploy/promote.sh` moves a whole fleet from one release tag to the next. To
do that safely it has to know four things about every plant: where its pack
is, where its database is and what kind it is, whether it simulates a line
worth regenerating, and where to ask it whether it came back. All four are in
the fleet file and its packs - and the product already has the reader for
both, in `fsmes.pack.fleet` and `fsmes.pack.format`.

This module is that reader with a shell script for a caller. It exists
because the alternative is a second reader written in bash, and the last one
of those read `plants` - the table a fleet file stopped having - which is why
the 0.2.0 promote script could not promote a fleet at all.

**A plan holds no secret unless it is asked for one.** `storage.url` is
always rendered with the password hidden, the way a person would paste it
into an issue. `--with-password` adds `secret_url` and `secret_password`
beside it, because `pg_dump` needs a password and a promote that cannot back
a plant up is not one you want to run; the names say what they are so a use
of one is visible in a diff. Nothing is written to a file here: a caller
that asks for the password gets it on stdout and is expected to keep it in a
variable.

**A database this tooling cannot back up says so.** `storage.backup` is
`copy` for a SQLite file, `pg_dump` for PostgreSQL, and `none` for anything
else, with `why` saying what it is instead. None is not "no backup needed";
it is the fact a promote should refuse on.
"""

from __future__ import annotations

import os.path
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from fsmes import __version__
from fsmes import plant as plants
from fsmes.fleet import observe
from fsmes.pack import fleet as packs

#: The two database kinds this product's own deployment tooling knows how to
#: copy and put back. Anything else is reported, not guessed at.
BACKS_UP = {"sqlite": "copy", "postgresql": "pg_dump"}

#: What a plant listening on every interface is asked on. A plant that serves
#: `0.0.0.0` is not reachable at `0.0.0.0`; loopback is where the machine it
#: runs on finds it, and a promote runs on that machine.
EVERYWHERE = {"", "0.0.0.0", "::", "*"}


def plan(root: Path, *, with_password: bool = False) -> dict:
    """Every plant this fleet runs, as the facts a promote acts on.

    The totals are on the envelope, not left to be counted: `count` is how
    many packs the fleet lists and `can_back_up` how many of those this
    tooling could copy before migrating. A fleet where those two differ is a
    fleet with a plant nobody can roll back.
    """
    fleet_file = packs.path(root)
    registry = packs.load(root)
    data = plants.data_dir(root)
    rows = [_plant(name, cfg, root, with_password=with_password)
            for name, cfg in sorted(registry.items())]
    return {
        "product_version": __version__,
        "fleet": str(fleet_file),
        "data_dir": str(data),
        "count": len(rows),
        "can_back_up": sum(1 for r in rows if r["storage"]["backup"] != "none"),
        "plants": rows,
    }


def _plant(name: str, cfg: dict, root: Path, *, with_password: bool) -> dict:
    host = str(cfg.get("api_host") or "")
    return {
        "name": name,
        "label": cfg.get("label") or name,
        "pack": str(cfg.get("pack") or ""),
        "simulate": bool(cfg.get("simulate", True)),
        # The generated line data a simulated plant replays is deterministic
        # and gitignored, so a promote regenerates it for the tag it is
        # moving to. `line.json` beside the replay directory is the ground
        # truth the generator writes; a plant that does not simulate has none.
        "line_data": _line_data(cfg),
        "health": observe.base("127.0.0.1" if host in EVERYWHERE else host, cfg.get("api_port")),
        "storage": _storage(name, cfg, root, with_password=with_password),
    }


def _line_data(cfg: dict) -> str | None:
    """The ground truth beside a simulated plant's replay directory.

    Normalised rather than resolved: a pack's `replay_dir` is usually written
    relative (`../../labs/kepsim/out`), and a path with `..` in the middle
    reads in a promote's log as though the script had lost its way. Nothing
    here touches the disk - the directory a promote is about to regenerate
    need not exist yet.
    """
    replay = cfg.get("replay_dir")
    if not cfg.get("simulate", True) or not replay:
        return None
    return Path(os.path.normpath(Path(replay).parent / "line.json")).as_posix()


def _storage(name: str, cfg: dict, root: Path, *, with_password: bool) -> dict:
    """Where this plant's data is, and how a promote would copy it.

    The URL comes from `fsmes.plant.database_url`, which is the one place
    that knows both the SQLite default and how a pack names the file its
    PostgreSQL password lives in. Reading that file can fail; that is a fact
    about this plant, reported here, and not an exception thrown through a
    fleet-wide plan.
    """
    try:
        url = make_url(plants.database_url(name, cfg, root))
    except (ArgumentError, OSError, ValueError) as exc:
        return {"kind": "unreadable", "backup": "none", "url": None,
                "why": f"this plant's database cannot be located from its pack: {exc}"}

    kind = url.drivername.split("+", 1)[0]
    out: dict = {
        "kind": kind,
        "backup": BACKS_UP.get(kind, "none"),
        "url": url.render_as_string(hide_password=True),
    }
    if kind == "sqlite":
        out["path"] = url.database
        if not url.database:
            out["backup"] = "none"
            out["why"] = "this plant's database is in memory, so there is nothing to copy"
    elif kind == "postgresql":
        # No password is not a problem to report: a plant on a local socket
        # with peer authentication has none and `pg_dump` needs none. What a
        # caller must not do is assume one - so the fact is stated, and a
        # `pg_dump` that then wants a password fails in pg_dump's own words
        # rather than in a guess made here.
        out.update(host=url.host or "127.0.0.1", port=url.port or 5432,
                   user=url.username or "", dbname=url.database or "",
                   password_known=bool(url.password))
    else:
        out["why"] = (f"this product's deployment tooling copies SQLite files and PostgreSQL "
                      f"databases; {url.drivername} is neither, so back this plant up yourself "
                      f"before promoting it.")

    if with_password and url.password:
        out["secret_url"] = url.render_as_string(hide_password=False)
        out["secret_password"] = url.password
    return out
