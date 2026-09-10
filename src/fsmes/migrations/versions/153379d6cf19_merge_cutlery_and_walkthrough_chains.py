"""merge the cutlery chain with the walkthrough chain

Revision ID: 153379d6cf19
Revises: a7c2e5d1b9f0, c4e8f1a2d5b6
Create Date: 2026-09-07 05:15:24.816174

"""
from collections.abc import Sequence

revision: str = '153379d6cf19'
down_revision: str | None = ('a7c2e5d1b9f0', 'c4e8f1a2d5b6')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
