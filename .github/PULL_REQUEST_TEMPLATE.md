## What
<!-- one paragraph: what a user notices -->

## House rules touched
- [ ] Never invent production — counters, resets, stale notifications handled
- [ ] Unknown ≠ zero — every KPI that can't be computed returns null with a reason
- [ ] Unlabelled stays unlabelled
- [ ] Config not code at plant boundaries — no plant literals in `src/`
- [ ] Tests named as prose
- [ ] Charts looked at with real (simulated) data

## Simulated or real?
<!-- which plant this was run against (`fsmes score <plant>` / `fsmes demo`), and at what speed -->

## Checklist
- [ ] `ruff check .` and `pytest` pass locally
- [ ] Every commit is signed off (`git commit -s`)
- [ ] No real plant data, employer or customer names anywhere in the change
