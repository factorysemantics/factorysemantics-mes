#!/usr/bin/env bash
# Prove a built wheel runs the demo, with nothing but the wheel to run it from.
#
# 0.1.0 installed cleanly from PyPI and did not work: the wheel carried no
# config/tag_map.json, so the simulator had no line, nothing was ever booked,
# and `fsmes demo` exited 0 regardless. The Release Checklist's clean-machine
# install was the line that would have caught it, and it was skipped for
# speed. This is that line, automated, so speed cannot skip it.
#
# Usage: demo_from_wheel.sh <wheel> [duration-seconds]
set -euo pipefail

wheel_arg="${1:?usage: demo_from_wheel.sh <wheel> [duration-seconds]}"
wheel="$(cd "$(dirname "$wheel_arg")" && pwd)/$(basename "$wheel_arg")"
# The demo stops watching after this many seconds; it exits as soon as the
# order completes, so a generous number costs nothing when the wheel is good.
duration="${2:-300}"

# An empty directory containing none of the repository's files. That is the
# whole point: no config/, no .env, no fsmes.db, no src/ to import instead of
# the installed package.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

python -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet "$wheel"
# What the wheel says it is, asked two ways: the distribution metadata pip
# installed, and the answer a user gets from the command line. These were a
# version apart in 0.1.2 — the metadata said 0.1.2, `fsmes --version` said
# 0.1.0 — because the number was written down twice. It is written down once
# now (src/fsmes/__init__.py, which hatchling stamps the metadata from), and
# this is the line that keeps it that way: a wheel whose two answers disagree
# never reaches PyPI.
echo "wheel: $(basename "$wheel")"
metadata_version="$(.venv/bin/python -c "from importlib.metadata import version; print(version('factorysemantics-mes'))")"
reported_version="$(.venv/bin/fsmes --version)"
echo "installed: ${metadata_version}"
echo "fsmes --version: ${reported_version}"
if [ "$reported_version" != "fsmes ${metadata_version}" ]; then
    echo "::error::the wheel's metadata says ${metadata_version} but 'fsmes --version' says '${reported_version}'"
    exit 1
fi

set +e
timeout 900 .venv/bin/fsmes demo --duration "$duration" 2>&1 | tee demo.log
exit_code="${PIPESTATUS[0]}"
set -e

# Two separate questions, and both have to be asked. The exit code only became
# meaningful when the demo learned to fail; an older wheel exits 0 while
# booking nothing, and only the output tells you which one you have.
if [ "$exit_code" -ne 0 ]; then
    echo "::error::fsmes demo exited ${exit_code} from the built wheel"
    exit 1
fi

if ! grep -q "Demo result: full loop closed" demo.log; then
    echo "::error::the wheel installed but the demo never closed its loop"
    exit 1
fi

echo "The wheel ran the demo to a closed loop."
