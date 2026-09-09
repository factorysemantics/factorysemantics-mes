"""File adapter — the classic ERP integration: drop files in folders.

Inbox:  ProductionSchedule XML (B2MML-lite) or plain JSON order files.
Outbox: one ProductionPerformance XML per confirmation.
Processed inbox files move to the archive folder, which doubles as the
file-level exchange history.
"""

import json
from pathlib import Path

import structlog

from fsmes.db import utcnow
from fsmes.integrations.erp import b2mml
from fsmes.integrations.erp.contract import Confirmation, OperationConfirmation, ProductionRequest, as_payload

log = structlog.get_logger("erp.file")


class FileErpAdapter:
    def __init__(self, inbox: Path, outbox: Path, archive: Path):
        self.inbox, self.outbox, self.archive = Path(inbox), Path(outbox), Path(archive)
        for folder in (self.inbox, self.outbox, self.archive):
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
