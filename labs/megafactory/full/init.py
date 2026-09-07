"""The full mega-factory's init step: the spike's seeder pointed at this
directory's line.json / tag_map.json. Runs before the API exists, through the
database, like every plant's init script; everything the API can reach is
seeded by seed_breadth.py instead.

    python3 labs/megafactory/full/init.py     (with MES_DATABASE_URL set)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import init as spike_init  # noqa: E402

if __name__ == "__main__":
    spike_init.main(HERE)
