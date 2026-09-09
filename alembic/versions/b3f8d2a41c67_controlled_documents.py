"""Controlled documents.

Revision ID: b3f8d2a41c67
Revises: a7c1e93b5f20

Work instructions the floor follows, with revisions that are immutable once
approved and anchors that bind an instruction to the material, characteristic,
operation or machine it is about.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3f8d2a41c67"
down_revision: str | None = "a7c1e93b5f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.Enum("draft", "approved", "superseded", "withdrawn",
                                    name="documentstatus"), nullable=False),
        sa.Column("anchor_material", sa.String(length=40), nullable=True),
        sa.Column("anchor_characteristic", sa.String(length=60), nullable=True),
        sa.Column("anchor_operation", sa.String(length=60), nullable=True),
        sa.Column("anchor_equipment", sa.String(length=40), nullable=True),
        sa.Column("created_by", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("approved_by", sa.String(length=40), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("drafted_by_model", sa.String(length=60), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "revision", name="uq_document_revision"),
    )
    op.create_index("ix_document_anchor", "documents",
                    ["anchor_material", "anchor_characteristic"])


def downgrade() -> None:
    op.drop_index("ix_document_anchor", table_name="documents")
    op.drop_table("documents")
