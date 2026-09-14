"""The console: one page that shows what every plant says about itself.

It reads. That is the whole of it, and it is meant to be checkable by
reading this file rather than by trusting this sentence:

* the app below declares two routes, both `GET`, and no other verb appears
  in it;
* it imports `observe` and `owned` and **never `commands`** - there is no
  path from the page to a verb that could change a plant, because the verbs
  are not reachable from here;
* every request it makes to a plant goes through `fsmes.fleet.observe`,
  which sends GETs to `/health` and `/pack`, endpoints a plant answers
  without a credential. The console therefore holds **no credential at
  all**. A console is a long-running process on a port, and whatever it can
  do, whoever reaches that port can do; the safest credential is the one
  that does not exist.
* `tests/test_fleet_console.py` holds all three by reading this source.

What the page refuses to do, from decision 0023:

* **a plant that did not answer is `unknown`** - never healthy, never down.
  It is also not owned while it is silent, because nothing can corroborate
  the instance id, and the page says that rather than carrying the last
  answer forward as if it were current;
* **it states its total**: "3 plants, 2 answered, 1 unknown";
* **it aggregates nothing across plants that would be a lie** - no fleet
  OEE, no fleet availability, no single number of any kind. Twelve plants
  are twelve rows;
* **it holds no plant data.** The last answer is cached for display and
  nothing else. Orders, serials, people and events stay in the plant that
  made them.

Where the list comes from: the packs this machine runs (the fleet file), the
plants this installation created (`ownership.toml`), and the `[[observe]]`
entries a person added to that file for plants elsewhere. Their union, one
row each, with the totals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fsmes.fleet import observe, owned

#: Where the page and its script live.
WEB_DIR = Path(__file__).parent.parent / "web"

PAGE = WEB_DIR / "fleet.html"


@dataclass(frozen=True)
class Listed:
    """One plant the console was told about, before anybody asked it anything."""

    name: str
    base: str
    label: str = ""
    entry: owned.Entry | None = None
    """The ownership entry claiming it, when there is one. A claim, not a
    fact: the plant still has to corroborate it."""

    about: str = ""


def listed(root: Path) -> list[Listed]:
    """Every plant this console shows, from the three places they come from.

    A plant recorded as created here wins over the same name in the fleet
    file - the ownership entry knows where the plant's data is and the fleet
    file does not.
    """
    from fsmes import plant as plants
    from fsmes.pack import fleet as packfleet

    where = plants.data_dir(root)
    record = owned.load(where)
    rows: dict[str, Listed] = {}

    try:
        for name, cfg in packfleet.load(root).items():
            rows[name] = Listed(name=name,
                                base=observe.base(cfg.get("api_host", ""), cfg.get("api_port")),
                                label=str(cfg.get("label") or ""),
                                about="a pack this machine runs")
    except (FileNotFoundError, packfleet.FleetError):
        # A console outside a checkout has no fleet file, and that is not an
        # error: the ownership file is the other list and may be the only one.
        pass

    for entry in record.owned:
        rows[entry.name] = Listed(name=entry.name, base=entry.base,
                                  label=rows[entry.name].label if entry.name in rows else "",
                                  entry=entry, about="created by this installation")
    for watched in record.observed:
        rows[watched.name] = Listed(name=watched.name, base=watched.url,
                                    about=watched.about or "watched only")
    return [rows[name] for name in sorted(rows)]


class Console:
    """The reading half of the fleet, and the one thing it remembers.

    The cache is a display convenience and nothing more: when each plant was
    last heard from. It is never used to answer *what is this plant doing
    now* - a plant that has gone quiet shows `unknown` and its last-heard
    time beside it, which is the honest pair.
    """

    def __init__(self, root: Path, *, health=None, pack=None):
        self.root = Path(root)
        # Injected so a test can put real plants behind it without a port.
        self._health = health or observe.health
        self._pack = pack or observe.pack
        self.last_answered: dict[str, str] = {}

    def look(self) -> dict:
        """Ask every plant, and say what came back. Reads only."""
        rows = [self._one(entry) for entry in listed(self.root)]
        answered = sum(1 for r in rows if r["answered"])
        owned_now = sum(1 for r in rows if r["owned"] == "yes")
        claimed = sum(1 for r in rows if r["owned"] == "unknown")
        totals = {"plants": len(rows), "answered": answered,
                  "unknown": len(rows) - answered, "owned": owned_now,
                  "claimed_but_silent": claimed,
                  "observed": len(rows) - owned_now - claimed}
        return {
            "asked_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "totals": totals,
            "says": (f"{totals['plants']} plants, {totals['answered']} answered, "
                     f"{totals['unknown']} unknown"),
            "ownership_says": (
                f"{totals['owned']} owned, {totals['claimed_but_silent']} recorded here "
                f"but not answering, {totals['observed']} observed"),
            "plants": rows,
        }

    def _one(self, plant: Listed) -> dict:
        answer = self._health(plant.base) if plant.base else observe.Answer(
            plant.base, False, why="nothing says where this plant answers")
        said = answer.body if answer.answered else None
        if answer.answered:
            self.last_answered[plant.name] = datetime.now(UTC).isoformat(timespec="seconds")

        if plant.entry is None:
            owned_state, why = "no", "this installation did not create it"
        else:
            agreed, why = owned.agrees(plant.entry, said)
            owned_state = "yes" if agreed else ("unknown" if said is None else "no")

        pack_said = self._pack(plant.base).body if answer.answered else None
        pack_said = pack_said or {}
        schema = pack_said.get("schema") or {}
        modules = pack_said.get("modules") or {}
        return {
            "name": plant.name,
            "label": plant.label or (said or {}).get("label") or "",
            "base": plant.base,
            "about": plant.about,
            "answered": answer.answered,
            # Never "down". A plant nobody heard from is a plant nobody
            # heard from, and that is a different fact from a plant that
            # said it was stopping.
            "state": "answered" if answer.answered else "unknown",
            "why": answer.why,
            "owned": owned_state,
            "ownership": why,
            "profile": (said or {}).get("profile"),
            "timezone": (said or {}).get("timezone_says"),
            "shadow": (said or {}).get("shadow"),
            "pack": pack_said.get("pack"),
            "pack_applied_at": pack_said.get("applied_at"),
            "product_version": pack_said.get("product_version"),
            "drifted": pack_said.get("drifted"),
            "schema_revision": schema.get("revision"),
            "schema_at_head": schema.get("at_head"),
            "modules_on": modules.get("on"),
            "modules_off": modules.get("off"),
            "modules_total": modules.get("total"),
            "pack_unknown": pack_said.get("unknown") or {},
            "last_answered": self.last_answered.get(plant.name),
        }


def create_app(root: Path, *, console: Console | None = None):
    """The console's own application: a page and the JSON behind it.

    Two routes, both GET. It mounts the product's stylesheets so the page
    looks like the rest of the product, and nothing else from the plant's
    application is imported here - this app has no database, no session, no
    account and no write path.
    """
    from fastapi import FastAPI
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from fsmes import __version__

    watching = console or Console(root)
    app = FastAPI(title="FactorySemantics MES — fleet console", version=__version__,
                  description="Observes every plant in the list. Writes nothing, anywhere.")

    @app.get("/", include_in_schema=False)
    def page() -> FileResponse:
        """The one page."""
        return FileResponse(PAGE)

    @app.get("/fleet.json")
    def fleet() -> dict:
        """What every plant in the list said, and what it did not."""
        return watching.look()

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
