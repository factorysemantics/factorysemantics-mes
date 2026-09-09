# Contributing

Thanks for looking. This project is pre-alpha and moving fast — see [ROADMAP.md](ROADMAP.md) for where it's going and which milestone is open, and [GOVERNANCE.md](GOVERNANCE.md) for how decisions are made (short version: one maintainer, in public).

## Where to talk

- **Questions and ideas:** [GitHub Discussions](https://github.com/factorysemantics/factorysemantics-mes/discussions). Search first; say your `fsmes info` output and whether your plant is simulated or real.
- **Bugs:** [Issues](https://github.com/factorysemantics/factorysemantics-mes/issues), using the form. A plant that needed a code change to come up is a bug by house rule 4 — there is a form for that too.
- **Security:** never in a public issue. See [SECURITY.md](SECURITY.md).
- **Design changes:** an Ideas discussion first, then a decision record in [docs/decisions/](docs/decisions/) once agreed. Small fixes need neither — open the pull request.

The maintainer answers within a few days, not hours. This is an evenings-and-weekends project; that is not going to be hidden.

## Developer Certificate of Origin (DCO)

Every commit must be signed off. Add `-s` to your commit:

```bash
git commit -s -m "Fix the thing"
```

That appends a `Signed-off-by:` line, which means you certify the [Developer Certificate of Origin](https://developercertificate.org/) — in plain English: *you wrote this, or you have the right to contribute it under this project's license.*

Why a DCO rather than a contributor licence agreement: a DCO is one line per commit and needs no paperwork, while still keeping the copyright record clean. That record matters — it's what keeps every future licensing option open.

## Getting set up

Linux or macOS:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev,mcp,agent]"
```

Windows:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,mcp,agent]"
```

Then run the checks — both must pass before a pull request (CI runs them on Ubuntu and Windows, Python 3.12 and 3.13):

```bash
python -m ruff check .
python -m pytest
```

Docs are MkDocs: `pip install -e ".[docs]"` then `mkdocs serve`. `mkdocs build --strict` must pass; a broken link fails the build on purpose.

## Pull requests

- One change per pull request, with a title a user would understand — squash-merge turns it into the changelog line.
- The template's checklist is the house rules below; tick what you touched.
- Say which plant you ran it against (`fsmes score <plant>` or `fsmes demo`), and at what speed. "It passed the tests" is necessary; "I looked at the screen with the simulated line running" is what house rule 6 asks for.
- Every commit signed off (`git commit -s`). The DCO check is automated and will tell you which commit is missing it.

## House rules

These are load-bearing, not style preferences. Most exist because breaking them once produced a convincing wrong number.

1. **Never invent production.** Counter deltas book production; a counter falling toward zero is a PLC reset that re-baselines and books nothing; stale or duplicate notifications are ignored.
2. **Unknown is a valid answer; zero is not.** A KPI that cannot be computed honestly returns `null` and says why. An OEE of 0% and an OEE of "we weren't watching" are different facts, and only one of them is actionable.
3. **Unlabelled data is reported as unlabelled.** A downtime pareto that files unlabelled stops under "other" is how a plant convinces itself it has data it doesn't have.
4. **Config, not code, at plant boundaries.** Which tags a machine exposes belongs in a tag map; what a site enables belongs in a plant pack. If adding a plant needs a code change, that's a bug.
5. **Tests are prose.** Name a test after the behaviour it pins (`test_the_window_never_reaches_back_before_the_mes_was_watching`), not after the function it calls.
6. **Charts get checked by looking at them.** A rendered chart can be completely convincing and completely wrong. If you add a visualisation, look at it with real data before you call it done, then pin what you saw with a test.

## Provenance rule

Contributions must derive only from your own work, public standards, and public documentation. Do not contribute material derived from any commercial MES product's internals, schemas, or documentation, and never include real plant data — use the simulator in `src/fsmes/sim/`. The same applies to your employer's and your customers' names: this project describes plants as "a real plant" and vendors by what their public documentation says, and asks contributors to do the same.

## Conduct

[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Reports go to conduct@factorysemantics.com.
