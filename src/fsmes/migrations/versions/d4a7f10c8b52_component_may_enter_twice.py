"""The same component can enter at more than one station.

Revision ID: d4a7f10c8b52
Revises: c9e2a4b71d38

Adding operation_seq to bom_items left the old UNIQUE(parent, component) in
place, which says a material may appear once in a bill - true when a BOM had
no notion of station, and wrong the moment it does. Water goes into a bottling
line at the filler and again at the washer; a line model has to allow it.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7f10c8b52"
down_revision: str | None = "c9e2a4b71d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite cannot alter a constraint in place, so the table is rebuilt.
    # `recreate="always"` keeps the behaviour identical on PostgreSQL, where
    # it would otherwise take the in-place path and skip the copy.
    with op.batch_alter_table(
        "bom_items",
        recreate="always",
        table_args=(
            sa.UniqueConstraint("parent_id", "component_id", "operation_seq",
                                name="uq_bom_parent_component_operation"),
        ),
    ):
        pass


def downgrade() -> None:
    with op.batch_alter_table(
        "bom_items",
        recreate="always",
        table_args=(sa.UniqueConstraint("parent_id", "component_id"),),
    ):
        pass
