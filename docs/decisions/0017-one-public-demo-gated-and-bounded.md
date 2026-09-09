# 0017 — One public demo, hosted by the maintainer, gated by an emailed link and bounded by limits

- **Status:** accepted
- **Date:** 2026-09-07
- **Deciders:** @kalwei

## Context
Three simulated plants had been used to show the product at different times. Only one of them — a cutlery plant with moulding lines, stackers, wrappers, a palletizer and serialised pieces — is large enough to be worth a stranger's five minutes, and it was chosen as the only public demo on 2026-09-06. That left the harder question: where it runs, who reaches it, and what stops it from falling over. A public demo of a manufacturing system is an API and a database, reachable from anywhere, in front of an audience the project is actively inviting.

## Options considered
| Option | For | Against |
|---|---|---|
| One plant, hosted by the maintainer, behind an emailed one-time link, with limits at the edge, in the service and in the database | no monthly bill; every visitor is a name on a list the project can write to later; the limits are the same knobs a plant would use | availability is one machine's availability, and the gate is a step between a curious visitor and the thing |
| A small always-on server | independent of any one machine; no gate needed | a monthly cost and a second machine to patch for a demo whose traffic is unknown |
| An ungated public URL | lowest friction; anyone can look | every unauthenticated request reaches the origin, and the project learns nothing about who came |
| Provider-hosted access control with an email code | no code to write | a gate, not a funnel: no list, a seat cap on the free plan, and the visitor meets someone else's login screen |

## Decision
One demo plant, running on the maintainer's own machine rather than on rented hardware, serving the API only over a fixed recorded dataset with simulation switched off — nothing is being generated, so a visitor sees measured history rather than a number invented for them. Access is a link signed by the project and emailed on request from a form; the link expires, and requests are checked and capped per address and per day, so a spike degrades to "we will email you tomorrow" instead of to a dead demo. Four bounds keep it from blowing up: a rate limit at the edge, CPU and memory caps on the service, a statement timeout and a connection cap on the database role the demo uses, and a nightly reset from a clean copy. The other two plants retire from public view. The gate lives entirely in front of the product; no demo-specific code goes into the MES, and what the demo plant enables is configuration.

## Consequences
Easy: the cost is a machine that was already running, and the visitor list is the beginning of the project's only mailing list. Hard: the demo is down when that machine is; the first time that matters is the first time a launch post lands, which is the trigger to revisit hosting rather than the limits.

## House rules touched
Rule 1 and rule 2: the demo shows recorded production with simulation off, so no number on the screen was invented to fill a gap. Rule 4: everything about the demo plant is settings, not a code path.
