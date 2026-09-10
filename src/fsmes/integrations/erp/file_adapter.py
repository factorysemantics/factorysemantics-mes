"""File adapter — the classic ERP integration: drop files in folders.

Inbox:  ProductionSchedule XML (B2MML-lite) or plain JSON order files.
Outbox: one ProductionPerformance XML per confirmation.
Processed inbox files move to the archive folder, which doubles as the
file-level exchange history.

The outbound layout, because a plant's ERP team has to build a collector
against it:

* **Name** — `000123_20260910T041500Z_WO-2026-0041_op10.xml`: a six-digit
  sequence number, the UTC minute-and-second it was written, the order,
  and either `op<seq>` for one operation or `completion` for the order's
  close. Anything outside `A-Z a-z 0-9 _ -` in an order code becomes a
  dash, so an order code can never decide where a file lands.
* **Order** — the sequence number is the order the MES wrote the files,
  and sorting the folder by name replays it. Timestamps alone could not:
  two confirmations written in the same second used to sort by order code
  and, worse, to overwrite each other when they were the same step.
* **Delivered** — for a file exchange, written *is* delivered. The MES
  marks the message sent once the file is closed on disk, and never opens
  it again. Collecting, moving or deleting the file is the ERP side's, and
  the MES neither requires it nor notices it.
* **Half-written files** — never seen. Each confirmation is written to a
  `.part` file and renamed into place, so a collector polling the folder
  either sees a whole document or no document.
* **Restart** — the sequence number is read back from the folder at
  start-up (highest one there, plus one), so a restart continues the run
  rather than colliding with it. A folder the ERP has emptied starts again
  at one, which is correct: the numbers order the files that exist
  together, and are not an audit sequence. The audit sequence is the
  outbox in the database, which survives both.

In shadow mode nothing moves. The inbox may be a folder the plant's own MES
is also reading, and renaming a file out of it would take that MES's orders
away - so each file is read and left exactly where it was, and the adapter
remembers what it has already read. The outbox is still written: producing
confirmations somebody can compare against the incumbent's is the point of
a shadow run. Give it a folder no ERP is watching.
"""

import json
import os
import re
from pathlib import Path

import structlog

from fsmes import shadow
from fsmes.db import utcnow
from fsmes.integrations.erp import b2mml
from fsmes.integrations.erp.base import CheckResult, ErpConnector, Requirement, SetupOutcome
from fsmes.integrations.erp.contract import Confirmation, OperationConfirmation, ProductionRequest, as_payload

log = structlog.get_logger("erp.file")


class FileErpAdapter(ErpConnector):
    def __init__(self, inbox: Path, outbox: Path, archive: Path):
        self.inbox, self.outbox, self.archive = Path(inbox), Path(outbox), Path(archive)
        # Making them here rather than in `setup()` is deliberate: a worker
        # that starts before anyone has run `fsmes erp setup` must still
        # work. `setup()` reports what this made, so the command tells the
        # truth about a folder that did not exist a moment ago.
        self._made_here: set[Path] = set()
        for folder in (self.inbox, self.outbox, self.archive):
            if not folder.exists():
                self._made_here.add(folder)
            folder.mkdir(parents=True, exist_ok=True)
        # Shadow mode only: what has been read and left in place, by name,
        # size and modification time. A file that changes is read again; a
        # restart reads the inbox once more, and importing an order twice
        # updates it rather than duplicating it.
        self._read_in_place: set[tuple[str, int, int]] = set()

    def _consume(self, path: Path, target: Path) -> None:
        """Move a handled inbox file to the archive, unless shadow mode."""
        if shadow.enabled():
            self._read_in_place.add(self._stamp(path))
            return
        path.rename(target)

    @staticmethod
    def _stamp(path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return (path.name, stat.st_size, stat.st_mtime_ns)

    def fetch_orders(self) -> list[ProductionRequest]:
        orders: list[ProductionRequest] = []
        for path in sorted(self.inbox.iterdir()):
            rows: list[dict] = []
            if shadow.enabled() and self._stamp(path) in self._read_in_place:
                continue
            try:
                if path.suffix.lower() == ".json":
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    rows = payload if isinstance(payload, list) else [payload]
                elif path.suffix.lower() == ".xml":
                    rows = b2mml.parse_production_schedule(path.read_text(encoding="utf-8"))
                else:
                    continue
                orders.extend(ProductionRequest.from_payload(row) for row in rows)
            except Exception as exc:  # a bad file must not stall the interface
                # Kept beside the good ones, under a name that says so: a
                # schedule nobody could read used to vanish into the archive.
                log.error("unreadable ERP file, kept as .rejected", file=path.name, error=str(exc))
                self._consume(path, self.archive / f"{path.name}.rejected")
                continue
            self._consume(path, self.archive / path.name)
        return orders

    def acknowledge(self, order_code: str) -> None:
        # Archiving the inbox file is the acknowledgement - and in shadow
        # mode there is deliberately none: the plant's own MES owns that
        # folder, and an order this MES merely watched is not received.
        pass

    # ------------------------------------------------------------- outbound

    _NUMBERED = re.compile(r"^(\d{6})_")
    # A dot is not safe either: `..` in an order code is how a file
    # exchange gets talked out of its own folder.
    _UNSAFE = re.compile(r"[^A-Za-z0-9_-]")

    def _next_number(self) -> int:
        """One past the highest number already in the outbox.

        Read from the folder rather than remembered, so two workers and a
        restart all see the same picture, and a folder the ERP has emptied
        simply starts again.
        """
        highest = 0
        for path in self.outbox.iterdir():
            found = self._NUMBERED.match(path.name)
            if found:
                highest = max(highest, int(found.group(1)))
        return highest + 1

    def send_confirmation(self, confirmation: Confirmation) -> None:
        """Write one confirmation into the outbox, whole and in order.

        Written is delivered: there is no acknowledgement in a file
        exchange, and pretending otherwise would make the outbox report a
        delivery nobody made.
        """
        stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
        suffix = f"op{confirmation.seq}" if isinstance(confirmation, OperationConfirmation) else "completion"
        order = self._UNSAFE.sub("-", confirmation.order)
        document = b2mml.render_confirmation(as_payload(confirmation))

        number = self._next_number()
        while True:
            target = self.outbox / f"{number:06d}_{stamp}_{order}_{suffix}.xml"
            if not target.exists():
                break
            number += 1
        # Written beside it and renamed in: a collector polling this folder
        # must never read half a document.
        part = self.outbox / f".{target.name}.part"
        part.write_text(document, encoding="utf-8")
        os.replace(part, target)

    # ------------------------------------------------------------- the far side
    # Three folders, and this connector makes them itself. It is here as the
    # simplest possible worked example of the three methods: a connector that
    # needs almost nothing still has to say what that nothing is, and still
    # has to be able to tell a person whether it would work.

    _FOLDERS = (
        ("inbox", "schedules the ERP writes, as B2MML-lite XML or JSON, one order or a list per file"),
        ("outbox", "one ProductionPerformance XML per confirmation, for the ERP to collect"),
        ("archive", "inbox files after they are read; a file nobody could parse keeps its name plus .rejected"),
    )

    def _paths(self) -> list[tuple[str, Path, str]]:
        return [(name, getattr(self, name), what) for name, what in self._FOLDERS]

    def requirements(self) -> list[Requirement]:
        return [
            Requirement(
                name=str(path),
                where=f"the {name} folder, on a filesystem both sides can reach",
                what=what,
                why="the exchange is the folder; there is nothing else between the two systems",
            )
            for name, path, what in self._paths()
        ]

    def setup(self) -> list[SetupOutcome]:
        """Make the three folders, and say which ones did not exist.

        Idempotent: the second run makes nothing and says so. A folder this
        connector created when it was built counts as created the first time
        `setup` is asked, because that is what happened.
        """
        outcome = []
        for _name, path, _what in self._paths():
            created = path in self._made_here or not path.is_dir()
            path.mkdir(parents=True, exist_ok=True)
            self._made_here.discard(path)
            outcome.append(SetupOutcome(name=str(path), outcome="created" if created else "already there"))
        return outcome

    def check(self) -> CheckResult:
        """Can this connector read and write where it was pointed?

        A folder that is missing, is a file, or cannot be written to is the
        whole failure surface of a file exchange, and every one of them
        currently shows up as a confirmation stuck in the outbox.
        """
        lines: list[tuple[str, str]] = []
        for name, path, _ in self._paths():
            if not path.exists():
                lines.append(("not ok", f"the {name} folder {path} does not exist"))
                continue
            if not path.is_dir():
                lines.append(("not ok", f"the {name} folder {path} is a file, not a folder"))
                continue
            if not os.access(path, os.W_OK | os.X_OK):
                lines.append(("not ok", f"the {name} folder {path} cannot be written to by this user"))
                continue
            lines.append(("ok", f"the {name} folder {path} exists and can be written to"))
        if all(status == "ok" for status, _ in lines):
            waiting = [p for p in self.inbox.iterdir() if p.suffix.lower() in (".json", ".xml")]
            other = len(list(self.inbox.iterdir())) - len(waiting)
            lines.append(("ok", f"{len(waiting)} schedule files waiting in the inbox"
                                + (f", and {other} files of other kinds, which are skipped" if other else "")))
        return CheckResult.of(*lines)
