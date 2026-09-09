# 0009 — The project lives in a GitHub organisation named `factorysemantics`

- **Status:** accepted
- **Date:** 2026-09-06
- **Deciders:** @kalwei

## Context
The repository was developed under the maintainer's personal account alongside unrelated personal projects. A product name that appears in a PyPI distribution, a container image path, a documentation domain and an MCP namespace has to resolve to the same string everywhere, and the account name is part of that string. Both `factorysemantics` and `factory-semantics` were registered while the question was open (2026-09-06 and 2026-09-07).

## Options considered
| Option | For | Against |
|---|---|---|
| Organisation `factorysemantics` | survives a maintainer change; per-module teams; a verified-domain badge; separates the product from personal repositories | five minutes of setup, and every URL that already pointed at the personal account has to move |
| Stay on the personal account | nothing to do; existing links keep working | ties the product to one person's login; no teams; reads as a hobby repository to an integrator |
| Organisation `factory-semantics` | the hyphen reads more easily | it is the only name in the table that would need a hyphen; the domain, the distribution name, the image path and the MCP namespace have none |

## Decision
The product lives at `factorysemantics/factorysemantics-mes`. One spelling is used everywhere: organisation `factorysemantics`, distribution `factorysemantics-mes`, import and CLI `fsmes`, MCP namespace `com.factorysemantics`. The hyphenated organisation is kept, empty except for a profile that points here, so the obvious misspelling stays under the project's control; it never holds product repositories. Two-factor authentication is required for members, and the base permission for members is read.

## Consequences
Easy: adding a second maintainer, or a second repository, without renaming anything. Hard: the personal account's redirect is the only thing keeping old links alive, so anything published from now on cites the organisation.

## House rules touched
None.
