"""The test suite never reaches the chat model running on this machine.

The steward, from the fixer, 2026-11-23: on this box the suite loaded the
real local chat model - a test that removed the cloud key fell back to it on
127.0.0.1:11434, two real generate calls held 5.5 GB on the graphics card for
five minutes after the suite ended, and a voice build on the same box failed.
CI has no local model, so nothing there showed it. `conftest.py` points every
module that asks the local model at a port where nothing listens; this keeps
that true, including for the next module that learns to ask it.
"""

import re
from pathlib import Path

from conftest import NO_LOCAL_MODEL
from fsmes.services import ai_status, assistant, design, drafting
from fsmes.sim import rollup, triage

SRC = Path(__file__).resolve().parents[1] / "src" / "fsmes"
REAL = "11434"


def test_every_address_of_the_local_model_points_nowhere_during_a_test():
    addresses = {"ai_status.OLLAMA_URL": ai_status.OLLAMA_URL,
                 "assistant.OLLAMA": assistant.OLLAMA,
                 "design.OLLAMA": design.OLLAMA,
                 "drafting.OLLAMA": drafting.OLLAMA,
                 "sim.rollup.OLLAMA": rollup.OLLAMA,
                 "sim.triage.OLLAMA": triage.OLLAMA}
    reaching = {name: url for name, url in addresses.items() if url != NO_LOCAL_MODEL}
    assert reaching == {}, f"these reach a real local model during the suite: {reaching}"


def test_no_other_module_knows_where_the_local_model_is():
    knowing = sorted(path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py")
                     if re.search(REAL, path.read_text(encoding="utf-8")))
    assert knowing == ["services/ai_status.py", "services/assistant.py",
                       "services/design.py", "services/drafting.py",
                       "sim/rollup.py", "sim/triage.py"], (
        f"{knowing}: a module that knows the local model's address must also be "
        "pointed at NO_LOCAL_MODEL in conftest's _the_suite_never_wakes_a_real_local_model")
