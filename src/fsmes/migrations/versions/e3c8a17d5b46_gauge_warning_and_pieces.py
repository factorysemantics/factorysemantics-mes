"""Two facts that were judgments in code: a gauge's warning window, and
whether a material is counted in pieces on a certificate.

`gauges.warn_days` — how many days before it falls due a gauge is called
*due soon*. Thirty for every gauge that already exists, which is exactly what
the gauges screen had invented for itself in JavaScript, so nothing anybody
sees changes. From here it is the gauge's own: a quarterly calibration wants
a fortnight and an annual one wants two months.

`materials.counted_in_pieces` — whether a pallet certificate counts this
material in pieces and prints a capability block for it. Until now
`services/coa.py` decided it by testing whether the material code started
`UT-`, which is one plant's numbering convention living in product code: a
plant not numbering its pieces that way got an empty capability block on
every certificate and no error anywhere. This sets the flag true for exactly
the materials that prefix chose and false for the rest, which is what each
of them gets today — the same answer, given honestly by a flag instead of
guessed from a name.

Revision ID: e3c8a17d5b46
Revises: d2b7e51c9a04
Create Date: 2026-09-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e3c8a17d5b46"
down_revision: str | None = "d2b7e51c9a04"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("gauges") as batch:
        batch.add_column(sa.Column("warn_days", sa.Integer(), nullable=False,
                                   server_default="30"))
    with op.batch_alter_table("materials") as batch:
        batch.add_column(sa.Column("counted_in_pieces", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    # The prefix this product used to test for, applied once, so every plant
    # already issuing pallet certificates keeps issuing exactly the ones it
    # was issuing yesterday.
    op.execute("UPDATE materials SET counted_in_pieces = 1 WHERE code LIKE 'UT-%'"
               if op.get_bind().dialect.name == "sqlite" else
               "UPDATE materials SET counted_in_pieces = true WHERE code LIKE 'UT-%'")


def downgrade() -> None:
    with op.batch_alter_table("materials") as batch:
        batch.drop_column("counted_in_pieces")
    with op.batch_alter_table("gauges") as batch:
        batch.drop_column("warn_days")
