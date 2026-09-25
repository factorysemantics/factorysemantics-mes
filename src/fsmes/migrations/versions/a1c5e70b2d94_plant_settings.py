"""The settings a plant owns: one additive table, and no data moved.

`plant_settings` starts empty on every existing plant, and that is deliberate
rather than lazy. Each of these settings is read in three layers — this table,
then the setting the pack compiled into the environment, then the literal the
product ships — so a plant upgrading with no rows here goes on running on
exactly the values it was running on a moment before, whatever its own
`plant.toml` says and whether or not it ever applies a pack again.

The alternative was to seed this table from the plant's pack file at migration
time. A migration is handed a database connection and nothing else: finding
the file would mean reading `FSMES_DATA_DIR`, then the apply receipt, then the
pack, any of which may be absent on a plant that was never built from a pack
at all — and the value it would write is the value the fallback already
returns. So nothing is read and nothing is written. The next `fsmes pack
apply` seeds the keys its pack carries, and an administrator editing one from
Quality's Configuration page writes a row for that key alone.

Revision ID: a1c5e70b2d94
Revises: e3c8a17d5b46
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a1c5e70b2d94"
down_revision: str | None = "e3c8a17d5b46"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "plant_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        # The `plant.toml` table and the key inside it, which is the one name
        # for a setting that the pack schema, `fsmes pack check` and the
        # Configuration page already share.
        sa.Column("section", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=60), nullable=False),
        # Text, because a setting is text by the time a plant reads one: the
        # pack compiles a float, a count and a list of rule numbers alike into
        # an environment variable's string, and the product parses each back
        # where it reads it.
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("set_by", sa.String(length=40), nullable=False),
        sa.Column("set_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section", "key", name="uq_plant_setting"),
    )


def downgrade() -> None:
    op.drop_table("plant_settings")
