"""tag_values: the index the latest-value queries needed

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-03
"""
from collections.abc import Sequence

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_tag_values_equipment_tag_id", "tag_values", ["equipment_id", "tag", "id"])


def downgrade() -> None:
    op.drop_index("ix_tag_values_equipment_tag_id", table_name="tag_values")
