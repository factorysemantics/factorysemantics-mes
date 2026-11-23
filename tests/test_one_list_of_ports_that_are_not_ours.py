"""One list of the ports a scratch plant must never take.

The structure report of 2026-11-18 ranked it second: "ports that are not
ours", six copies, drifted. `scripts/record-scene.py` refused 8111 and
4941-4943 but not 8140, 4944 or 4945; every class test kept its own
`NOT_OURS`, which had those three and not the recorder's four, and class
0.1's lacked 4944 and 4945 as well. A class test could pass on a port the
recorder refuses, and the recorder could start a take on one the tests
forbid. The recorder's map is now the one list, it has every port either
side named, and the class tests read it.
"""

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RECORDER = REPO / "scripts" / "record-scene.py"

# Every port any of the old copies named, recorder's and tests' alike.
NAMED_BEFORE = {8090, 8110, 8111, 8120, 8140, 9030, 4941, 4942, 4943, 4944, 4945, 5433, 5434}


def _recorder():
    spec = importlib.util.spec_from_file_location("record_scene_ports", RECORDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_recorder_refuses_every_port_any_copy_named():
    missing = NAMED_BEFORE - set(_recorder().PORTS_THAT_BELONG_TO_SOMETHING_ELSE)

    assert not missing, f"the recorder would start a take on {sorted(missing)}"


def test_no_test_keeps_its_own_list_of_ports():
    copies = [path.name for path in sorted((REPO / "tests").glob("test_*.py"))
              if re.search(r"^NOT_OURS\s*=", path.read_text(encoding="utf-8"), re.M)]

    assert copies == [], f"{len(copies)} tests keep their own list: {copies}"
