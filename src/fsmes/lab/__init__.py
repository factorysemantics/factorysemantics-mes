"""The lab: one plan, one command, one directory somebody else can read.

An experiment is a **pack**, a **scenario** and a **set of measurements**. The
pieces it needs all existed before this package did - packs and the fleet, the
generator whose line description is ground truth by construction, the scorer
that reads the MES through its own HTTP API, the ephemeral runner that builds a
plant and tears it down again. What was missing was the join: running them as
one thing, and writing the result somewhere a person can still read next week.

So nothing here simulates, scores or serves. `plan` reads the experiment,
`build` turns each pack and the scenario into a plant to run, `truth` reads
what the line actually did out of the data the replay obeyed, `measure` puts
the MES's answer beside that truth, and `report` renders the lot. The run
itself is `fsmes.sim.runner.scored_run`, unchanged.

Standing order 4 is why this is a tool and not a service: it never starts a
plant on anybody's machine by itself. A person types `fsmes lab run`.
"""

from fsmes.lab.plan import Plan, PlanError, read_plan

__all__ = ["Plan", "PlanError", "read_plan"]
