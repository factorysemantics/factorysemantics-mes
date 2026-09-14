"""Bring up the ACME bottling plant — the large plant in the multi-plant lab.

    LD -> RD -> Washer -> QI -> Refill -> Palletiser

Thin on purpose: the six-station line already has a seeder inside the product
(`fsmes seed-kepsim`), because it is the twin's own reference line. All this adds
is a released order, so counter deltas have an operation to book against.

**A lab tool, not part of the pack.** Until 2026-09-13 the registry named this
file and `fsmes plant bottling init` ran it as a subprocess. A pack carries no
code (decision 0022), and this plant's line is the product's own rather than
data anybody would write out, so `plant.toml` carries no master data and this
stays a script a person runs:

    fsmes plant bottling init          # schema, pack, accounts
    python labs/multiplant/bottling/init.py

Contrast with `../machining/masterdata/`, which carries that plant's whole
definition as data the validator can read. That asymmetry is the honest one:
one line ships with the product, the other is a customer's.
"""

from fsmes.db import session_scope
from fsmes.seed_kepsim import seed_kepsim_line
from fsmes.services import workorders

ORDER = "WO-ACME-4711"


def main() -> None:
    with session_scope() as session:
        created = seed_kepsim_line(session)
        session.flush()
        try:
            workorders.create(
                session,
                code=ORDER,
                material_code="FG-BOTTLE",
                quantity=4000,
                priority=10,
                actor="lab-seed",
            )
            workorders.release(session, ORDER, actor="lab-seed")
        except Exception as exc:  # already seeded on a previous run
            print(f"order {ORDER}: {exc}")

    what = "Seeded" if created else "Already present:"
    print(f"{what} ACME bottling line — 6 stations, routing RT-BOTTLE, order {ORDER} released.")


if __name__ == "__main__":
    main()
