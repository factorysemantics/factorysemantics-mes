"""FactorySemantics MES — an open-source, modular, agent-native MES.

A small kernel (master data, routings, orders, dispatch, execution, events,
audit, auth) that is always present, plus optional modules for downtime/OEE,
quality/SPC, gauges, maintenance, scheduling and traceability. See ROADMAP.md
for what exists today and what is still a promise.
"""

# The one place the project's version is written. `pyproject.toml` declares the
# version dynamic and hatchling reads this line, so this is also the wheel's,
# the sdist's and the container tag's version. Bumping a release means editing
# this line and nothing else; `fsmes --version` and the installed
# distribution's metadata are then the same fact by construction, and a test
# pins that they still are.
__version__ = "0.1.2"

__all__ = ["__version__"]
