"""Simulation, ground truth, and scoring.

The simulator scripts the hour it replays, so we know what actually happened:
the mill fails at t+2700s, and the stop at t+1500s is a planned changeover.
That makes a question available that a test suite cannot ask - not "did the
code raise" but "did the MES tell the truth about the plant?"

Everything here reads the MES through its public HTTP API and never through
its database. That is a deliberate constraint: it means every scored run also
exercises the surface an agent would use, so a gap in that surface shows up as
a scenario we cannot express rather than as a silent weakness.
"""

from fsmes.sim.score import score_run
from fsmes.sim.truth import ScriptedEvent, load_truth

__all__ = ["ScriptedEvent", "load_truth", "score_run"]
