"""Bring up the ACME bottling plant — the large plant in the multi-plant lab.

    LD -> RD -> Washer -> QI -> Refill -> Palletiser

Thin on purpose: the six-station line already has a seeder inside the product
(`fsmes seed-kepsim`), because it is the twin's own reference line. All this adds
is a released order, so counter deltas have an operation to book against.

Contrast with `../machining/seed.py`, which carries its whole plant definition
in the lab. That asymmetry is the honest one: one line ships with the product,
the other is a customer, and only the first belongs in `src/`.
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
