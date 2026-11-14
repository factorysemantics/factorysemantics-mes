"""Routers do not grow: a router parses the request and calls a service.

Scott, 2026-11-14, on the nightly card, draft and judge loop: it "scares me
that some of these PRs might be bloating the system ... piecemeal adding
massive amounts of bloat". The week before had put 634 lines into
`src/fsmes/api/routers/` and 225 into `src/fsmes/services/`, and written the
walk from a machine up to its line four times. The rule that a router only
parses and calls was a docstring in `src/fsmes/services/__init__.py`; this
is the fence that holds it, in the shape of `test_core_purity.py`: it reads
the source, needs nothing installed, and runs in the normal suite.

Every router has a ceiling: its length on 2026-11-14 plus five per cent,
rounded up. A router that passes it fails, and the way out is to move the
rule into a service and call it from the router. If the router really must
be longer, raise its ceiling in `CEILINGS` and write the reason beside it,
in the same pull request, where a reviewer reads it. A router that is not in
the table yet is a new file and gets `NEW_ROUTER_CEILING`.

`quality.py` is over a thousand lines today; the ceiling does not say that
is right, only that it stops there. Shrinking it is its own piece of work.
"""

from pathlib import Path

ROUTERS = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "api" / "routers"

#: A new router starts small: parse, call a service, answer.
NEW_ROUTER_CEILING = 150

#: file -> (most lines it may have, why it may have that many). The reason is
#: empty for every ceiling set on 2026-11-14 from the length that day + 5 %.
#: Raising a number means writing a reason.
CEILINGS: dict[str, tuple[int, str]] = {
    "__init__.py": (0, ""),           # 0 lines on 2026-11-14
    "adjustments.py": (74, ""),        # 70 lines on 2026-11-14
    "admin.py": (210, ""),              # 200 lines on 2026-11-14
    "analysis.py": (623, ""),           # 593 lines on 2026-11-14
    "assist.py": (506, ""),             # 481 lines on 2026-11-14
    "auth.py": (108, ""),               # 102 lines on 2026-11-14
    "coa.py": (72, ""),                # 68 lines on 2026-11-14
    "dashboard.py": (882, ""),          # 840 lines on 2026-11-14
    "design.py": (242, ""),             # 230 lines on 2026-11-14
    "documents.py": (145, ""),          # 138 lines on 2026-11-14
    "equipment.py": (237, ""),          # 225 lines on 2026-11-14
    "erp.py": (28, ""),                # 26 lines on 2026-11-14
    "execution.py": (223, ""),          # 212 lines on 2026-11-14
    "kpis.py": (39, ""),               # 37 lines on 2026-11-14
    "line.py": (67, ""),               # 63 lines on 2026-11-14
    "maintenance.py": (556, ""),        # 529 lines on 2026-11-14
    "masterdata.py": (266, ""),         # 253 lines on 2026-11-14
    "ops.py": (258, ""),                # 245 lines on 2026-11-14
    "quality.py": (1061, ""),            # 1010 lines on 2026-11-14
    "rate_orders.py": (50, ""),        # 47 lines on 2026-11-14
    "reasons.py": (132, ""),            # 125 lines on 2026-11-14
    "scheduling.py": (108, ""),         # 102 lines on 2026-11-14
    "serialization.py": (132, ""),      # 125 lines on 2026-11-14
    "severities.py": (135, ""),         # 128 lines on 2026-11-14
    "system.py": (564, ""),             # 537 lines on 2026-11-14
    "triggers.py": (113, ""),           # 107 lines on 2026-11-14
    "workorders.py": (319, ""),         # 303 lines on 2026-11-14
}

MESSAGE = "move the logic to `src/fsmes/services/` or raise the ceiling in this table with a reason."


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _routers() -> list[Path]:
    return sorted(ROUTERS.glob("*.py"))


def test_no_router_is_longer_than_its_ceiling():
    over = []
    for path in _routers():
        ceiling, _reason = CEILINGS.get(path.name, (NEW_ROUTER_CEILING, "a new router"))
        length = _lines(path)
        if length > ceiling:
            over.append(f"{path.name} is {length} lines, its ceiling is {ceiling}")
    assert not over, "; ".join(over) + f" - {MESSAGE}"


def test_every_ceiling_names_a_router_that_exists():
    """A router deleted or renamed takes its line out of the table with it,
    or the table stops describing the code."""
    present = {path.name for path in _routers()}
    stale = sorted(set(CEILINGS) - present)
    assert not stale, f"in CEILINGS but not in {ROUTERS}: {stale}"
