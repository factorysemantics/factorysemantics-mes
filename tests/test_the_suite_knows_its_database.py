"""The suite's own database, named out loud.

The PostgreSQL cell in CI proves nothing if the suite quietly runs on SQLite
inside it. A typo in `MES_TEST_DATABASE_URL`, an `env:` block that moved, a
driver that is not installed and falls back — any of those leaves the cell
green while the claim behind it is false. This is the tripwire: it fails
instead.
"""

import os

from sqlalchemy.engine import make_url


def test_the_suite_runs_on_the_database_the_environment_names(session):
    asked = os.environ.get("MES_TEST_DATABASE_URL", "").strip()
    running_on = session.get_bind().dialect.name
    if not asked:
        assert running_on == "sqlite", (
            "nothing named a database, so the suite should be on its default "
            f"in-memory SQLite, and it is on {running_on}")
        return
    named = make_url(asked).get_backend_name()
    assert running_on == named, (
        f"MES_TEST_DATABASE_URL names {named}; the suite is running on {running_on}")
