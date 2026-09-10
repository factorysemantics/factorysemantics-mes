"""File adapter — the classic ERP integration: drop files in folders.

Inbox:  ProductionSchedule XML (B2MML-lite) or plain JSON order files.
Outbox: one ProductionPerformance XML per confirmation.
Processed inbox files move to the archive folder, which doubles as the
file-level exchange history.
"""

import json
import os
from pathlib import Path

import structlog

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

    def fetch_orders(self) -> list[ProductionRequest]:
        orders: list[ProductionRequest] = []
        for path in sorted(self.inbox.iterdir()):
            rows: list[dict] = []
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
                path.rename(self.archive / f"{path.name}.rejected")
                continue
            path.rename(self.archive / path.name)
        return orders

    def acknowledge(self, order_code: str) -> None:
        pass  # archiving the inbox file is the acknowledgement

    def send_confirmation(self, confirmation: Confirmation) -> None:
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
        suffix = f"op{confirmation.seq}" if isinstance(confirmation, OperationConfirmation) else "completion"
        target = self.outbox / f"confirmation_{confirmation.order}_{suffix}_{stamp}.xml"
        target.write_text(b2mml.render_confirmation(as_payload(confirmation)), encoding="utf-8")

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
