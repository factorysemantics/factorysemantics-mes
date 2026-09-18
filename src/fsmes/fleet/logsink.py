"""A size cap on `logs/<plant>/plant.log`.

A plant started by `fsmes fleet start` is four processes sharing one open
file. Python's own rotating handler cannot cap that: four processes each
holding the same descriptor would each roll it, and each would go on
appending at its own offset into a file the others had renamed.

So one process owns the file. The four write down a pipe; this reads the
pipe and writes the file, rolls it at `max_bytes` and keeps `keep` of
them. That is the only arrangement in which a cap is a cap.

WHY THERE HAS TO BE A CAP. A lab plant logged 1.1 GB in six hours — about
ten gigabytes a day, on a box with 22 GB free. A plant is a thing that is
left running; the failure that fills the disk is not the logging, it is
the absence of a ceiling on it. 200 MB across four files is roughly a week
of a healthy plant at the lab's replay speed and a few hours of one that
has gone wrong, which is the trade a ceiling always is: keep enough to
diagnose, never enough to stop the plant.
"""

from __future__ import annotations

import os
from pathlib import Path

# 50 MB a file, four files: 200 MB is the most a plant's console log can
# ever occupy. Deliberately a fixed ceiling rather than a setting - a plant
# that could be configured to fill its own disk still can.
MAX_BYTES = 50_000_000
KEEP = 3
CHUNK = 65_536


def roll(path: Path, keep: int = KEEP) -> None:
    """plant.log -> plant.log.1 -> plant.log.2 ... and the oldest falls off."""
    oldest = path.with_suffix(path.suffix + f".{keep}")
    if oldest.exists():
        oldest.unlink()
    for n in range(keep - 1, 0, -1):
        older = path.with_suffix(path.suffix + f".{n}")
        if older.exists():
            older.rename(path.with_suffix(path.suffix + f".{n + 1}"))
    if path.exists():
        path.rename(path.with_suffix(path.suffix + ".1"))


def pump(source, path: Path, max_bytes: int = MAX_BYTES, keep: int = KEEP,
         chunk: int = CHUNK) -> int:
    """Copy `source` into `path`, rolling it over at `max_bytes`.

    Returns the number of bytes written. Ends when the source reaches EOF,
    which is when the last process holding the other end of the pipe has
    gone - so the sink outlives the plant it serves by exactly as long as
    it takes to write what the plant said last.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    size = path.stat().st_size if path.exists() else 0
    out = open(path, "ab")  # noqa: SIM115 - closed below; it is rolled, not scoped
    try:
        while True:
            block = source.read(chunk)
            if not block:
                return written
            out.write(block)
            out.flush()
            size += len(block)
            written += len(block)
            if size >= max_bytes:
                out.close()
                roll(path, keep)
                out = open(path, "ab")  # noqa: SIM115
                size = 0
    finally:
        out.close()


def main(argv: list[str]) -> int:
    """`fsmes run-log-sink <path>`: the plant's console, capped.

    Reads stdin as bytes, because what it carries is whatever four
    processes wrote, in whatever encoding, and a decode error here would
    lose a plant's log to a stray byte.
    """
    import sys

    path = Path(argv[0])
    max_bytes = int(argv[1]) if len(argv) > 1 else MAX_BYTES
    # Nothing must ever kill the plant because its log sink died; a broken
    # pipe or a full disk ends the sink quietly and the plant runs on.
    try:
        pump(sys.stdin.buffer, path, max_bytes)
    except OSError:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - the CLI is the entry point
    import sys

    raise SystemExit(main(sys.argv[1:]) if len(sys.argv) > 1 else os.EX_USAGE)
