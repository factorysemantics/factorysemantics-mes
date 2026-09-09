# 0002 — A kernel plus modules that register through entry points

- **Status:** accepted
- **Date:** 2026-08-28
- **Deciders:** @kalwei

## Context
The north star is Opcenter-level coverage from one person's evenings. A monolith cannot get there; a plugin architecture designed in advance usually ships nothing. Plants install what they need and no more (principle 1).

## Options considered
| Option | For | Against |
|---|---|---|
| Kernel + entry-point modules | one install path (`pip install`), `fsmes info` says what is present, a module can live in its own package | the module boundary is a discipline, not a wall; the kernel wheel still ships most modules today |
| Microservices per module | isolation | operational cost a five-person shop cannot carry |
| One package, no modules | simplest | every plant carries every module; no path for third-party connectors |

## Decision
A small kernel (master data, orders, routing, dispatch, execution, events, audit, auth) is always present. Modules register in the `fsmes.modules` entry-point group; the kernel reads that group rather than a hard-coded list. As of 2026-09-07 the ERPNext connector is the one registered module and the ERP factory resolves connectors by entry-point name; the other modules ship inside the kernel wheel and become separately installable as the seam hardens.

## Consequences
Easy: a connector in its own package (`fsmes-sap`) with no change to the kernel. Hard: keeping the seam honest while most modules still live in one wheel; the architecture page says where intent and code differ. Revisit when a second module moves out.

## House rules touched
Rule 4 (config not code at plant boundaries): a module is code, a plant pack is config; the seam keeps them apart.
