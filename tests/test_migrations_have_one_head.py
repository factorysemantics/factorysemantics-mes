"""The migration chain has one head."""
from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_the_migration_chain_has_exactly_one_head():
    """Two branches each adding a migration merge into two heads, and a
    plant's `migrate` then refuses to run at all - which is how a release
    failed to promote. A merge migration joins them; this keeps it joined."""
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"migration heads: {heads}"
