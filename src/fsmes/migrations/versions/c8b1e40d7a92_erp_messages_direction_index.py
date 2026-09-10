"""erp_messages: index (direction, id)

Both readers of the outbox ask for one direction, oldest first — the ERP
sync for what it still owes, and the unified-namespace publisher's
enrolment for what it has not taken on yet. Neither had an index to read it
by, so both scanned every message the plant had ever exchanged, and that
table only grows.

The index is on the pair because the pair is the question: the filter is
the direction, the order is the id, and one index answers both without a
sort. A filter on direction alone still uses it.

Revision ID: c8b1e40d7a92
Revises: d9a3f61c48e0
Create Date: 2026-09-10 09:50:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'c8b1e40d7a92'
down_revision: str | None = 'd9a3f61c48e0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_erp_messages_direction_id', 'erp_messages', ['direction', 'id'],
                    unique=False)


def downgrade() -> None:
    op.drop_index('ix_erp_messages_direction_id', table_name='erp_messages')
