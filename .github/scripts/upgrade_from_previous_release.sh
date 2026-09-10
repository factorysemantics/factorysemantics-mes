#!/usr/bin/env bash
# Prove the upgrade a plant will actually perform: a database made by the
# release that is on PyPI now, moved to the current schema by the built wheel.
#
# 0.1.2 shipped no migration scripts inside the wheel, so `fsmes init-db`
# outside a source checkout created any missing tables with create_all, ran no
# ALTER at all, and printed "Database schema is up to date" either way. A plant
# that installed from PyPI and upgraded would have run new code against an old
# schema and been told everything was fine. That is an honesty bug caused by
# packaging, and packaging bugs are invisible to every test that runs in a
# checkout — so this runs on the bytes that ship.
#
# Usage: upgrade_from_previous_release.sh <wheel>
set -euo pipefail

wheel_arg="${1:?usage: upgrade_from_previous_release.sh <wheel>}"
wheel="$(cd "$(dirname "$wheel_arg")" && pwd)/$(basename "$wheel_arg")"

# An empty directory: no config/, no alembic.ini, no src/ to import instead of
# the installed package. The same nothing a plant PC has.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

# Counts every row in the database, so the upgrade can be shown not to have
# lost any. A plant's history is the thing a migration must never trade away.
cat > rows.py <<'PY'
import sqlite3

conn = sqlite3.connect("fsmes.db")
tables = [row[0] for row in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' "
    "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'")]
print(sum(conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables))
PY

# The release a plant is upgrading *from*: whatever PyPI serves today. Named
# rather than pinned, so this keeps testing the real starting point as
# releases go by instead of testing 0.1.2 forever.
python -m venv .venv-previous
.venv-previous/bin/python -m pip install --quiet --upgrade pip
.venv-previous/bin/python -m pip install --quiet factorysemantics-mes
previous="$(.venv-previous/bin/python -c "from importlib.metadata import version; print(version('factorysemantics-mes'))")"
echo "previous release, from PyPI: ${previous}"

# A database, made the way that release makes one, with rows in it.
.venv-previous/bin/fsmes init-db
.venv-previous/bin/fsmes seed
before_rows="$(python rows.py)"
echo "rows in the ${previous} database: ${before_rows}"
if [ "$before_rows" -lt 1 ]; then
    echo "::error::the ${previous} wheel produced an empty database, so this would prove nothing"
    exit 1
fi

# The wheel under test, in its own environment, pointed at that same database.
python -m venv .venv-built
.venv-built/bin/python -m pip install --quiet --upgrade pip
.venv-built/bin/python -m pip install --quiet "$wheel"
built="$(.venv-built/bin/python -c "from importlib.metadata import version; print(version('factorysemantics-mes'))")"
echo "built wheel: ${built}"

.venv-built/bin/fsmes init-db 2>&1 | tee upgrade.log

# `db-status` exits non-zero unless the stamp equals the head the wheel ships.
# That is the promise being made to the plant, asked of the database itself.
if ! .venv-built/bin/fsmes db-status; then
    echo "::error::a ${previous} database upgraded with the built wheel is not at head"
    exit 1
fi

after_rows="$(python rows.py)"
echo "rows after the upgrade: ${after_rows}"
if [ "$after_rows" -lt "$before_rows" ]; then
    echo "::error::the upgrade lost rows: ${before_rows} before, ${after_rows} after"
    exit 1
fi

# And the software still works on the database it just moved: the demo, run
# against the upgraded file rather than a fresh one. That is the difference
# between "the migrations ran" and "the plant can carry on".
set +e
timeout 900 .venv-built/bin/fsmes demo --duration 300 2>&1 | tee demo.log
exit_code="${PIPESTATUS[0]}"
set -e
if [ "$exit_code" -ne 0 ]; then
    echo "::error::fsmes demo exited ${exit_code} on a database upgraded from ${previous}"
    exit 1
fi
if ! grep -q "Demo result: full loop closed" demo.log; then
    echo "::error::the upgraded database ran the demo, but the loop never closed"
    exit 1
fi

echo "A ${previous} database upgraded to head with the built wheel, kept its ${after_rows} rows, and ran the demo."
