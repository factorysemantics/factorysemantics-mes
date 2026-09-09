"""Controlled documents: the work instructions people actually follow.

An MES without controlled instructions is half a system. Operators work from
them, auditors ask for them by revision, and an agent answering a floor
question should quote the approved text rather than invent procedure.

Two things make this a manufacturing document rather than a wiki page.

Revisions are immutable. Approving a document freezes it; changing it creates
the next revision and leaves the old one readable, because "what did the
instruction say in March" is a question a plant has to be able to answer.

And a document is *anchored*. It binds to a material, a characteristic, an
operation or a machine, which is what lets the right instruction appear beside
the very form that does the work instead of in a folder nobody opens.
"""

from __future__ import annotations

import enum
import json
from datetime import datetime

from sqlalchemy import Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow
from fsmes.domain.common import str_enum


class DocumentStatus(enum.StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class Document(Base):
    """One revision of one controlled document.

    The row is the revision, not the document: `code` identifies the
    instruction, `revision` counts up, and the current instruction is the
    highest-numbered approved revision. Superseding rather than overwriting is
    what makes the history real.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("code", "revision", name="uq_document_revision"),
        Index("ix_document_anchor", "anchor_material", "anchor_characteristic"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60))
    revision: Mapped[int] = mapped_column(default=1)
    title: Mapped[str] = mapped_column(String(200))
    # Markdown. Plain text in the database rather than a file on disk, because
    # an instruction that can go missing from a share is not controlled.
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus), default=DocumentStatus.DRAFT)

    # What this instruction is *about*. Any combination, all optional: an
    # instruction may cover a whole material, one characteristic of it, one
    # operation, or one machine.
    anchor_material: Mapped[str | None] = mapped_column(String(40))
    anchor_characteristic: Mapped[str | None] = mapped_column(String(60))
    anchor_operation: Mapped[str | None] = mapped_column(String(60))
    anchor_equipment: Mapped[str | None] = mapped_column(String(40))

    # What kind of document: a written `instruction` (markdown body) or a
    # recorded `walkthrough` - steps against real controls on real screens,
    # played by the assistant, with the recorder's own why at each step.
    kind: Mapped[str] = mapped_column(String(20), default="instruction")
    # The walkthrough's steps as JSON: [{page, anchor, title, body, fill?, tab?, open?}].
    steps: Mapped[str | None] = mapped_column(Text)
    # The capability a person needs to follow it - never teach a task the
    # person will be refused at the last step.
    needs: Mapped[str | None] = mapped_column(String(40))

    created_by: Mapped[str] = mapped_column(String(40), default="system")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(40))
    approved_at: Mapped[datetime | None] = mapped_column()
    # Where the first draft came from. A plant is entitled to know whether a
    # human or a model wrote the words it is following.
    drafted_by_model: Mapped[str | None] = mapped_column(String(60))

    def steps_list(self) -> list[dict]:
        if not self.steps:
            return []
        try:
            data = json.loads(self.steps)
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def anchors(self) -> dict[str, str]:
        return {
            k: v for k, v in {
                "material": self.anchor_material,
                "characteristic": self.anchor_characteristic,
                "operation": self.anchor_operation,
                "equipment": self.anchor_equipment,
            }.items() if v
        }
