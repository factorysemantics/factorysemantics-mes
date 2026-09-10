"""Actually drop the old bill-of-materials constraint.

Revision ID: e6b3c92d4f17
Revises: d4a7f10c8b52

The previous migration passed the new UNIQUE to batch_alter_table and assumed
that replaced the table's constraints. It does not - it adds to what alembic
reflected, so the table ended up carrying both, and the old
UNIQUE(parent_id, component_id) went on rejecting a component that enters at
two stations.

SQLite cannot drop an unnamed constraint, so the table is rebuilt explicitly:
create the shape we want, copy the rows, swap. Written out rather than left to
a helper, because the failure mode above is exactly what happens when this is
assumed to be handled.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6b3c92d4f17"
down_revision: str | None = "d4a7f10c8b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = "id, parent_id, component_id, quantity, operation_seq"


def _rebuild(unique: sa.UniqueConstraint) -> None:
    op.create_table(
        "bom_items_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=False),
        sa.Column("component_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("operation_seq", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["parent_id"], ["materials.id"]),
        sa.ForeignKeyConstraint(["component_id"], ["materials.id"]),
        unique,
    )
    op.execute(f"INSERT INTO bom_items_new ({COLUMNS}) SELECT {COLUMNS} FROM bom_items")
    op.drop_table("bom_items")
    op.rename_table("bom_items_new", "bom_items")


def _swap_unique(drop_columns: list[str], keep_name: str, keep_columns: list[str]) -> None:
    """On a database that can drop a constraint by name (PostgreSQL), do that
    instead of rebuilding the table: drop whichever unique constraint covers
    `drop_columns`, and make sure the one named `keep_name` exists."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_unique_constraints("bom_items")
    for constraint in existing:
        if constraint["column_names"] == drop_columns and constraint["name"] != keep_name:
            op.drop_constraint(constraint["name"], "bom_items", type_="unique")
    if keep_name not in {c["name"] for c in existing}:
        op.create_unique_constraint(keep_name, "bom_items", keep_columns)


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _rebuild(sa.UniqueConstraint("parent_id", "component_id", "operation_seq",
                                     name="uq_bom_parent_component_operation"))
    else:
        _swap_unique(["parent_id", "component_id"], "uq_bom_parent_component_operation",
                     ["parent_id", "component_id", "operation_seq"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _rebuild(sa.UniqueConstraint("parent_id", "component_id",
                                     name="uq_bom_parent_component"))
    else:
        _swap_unique(["parent_id", "component_id", "operation_seq"], "uq_bom_parent_component",
                     ["parent_id", "component_id"])
