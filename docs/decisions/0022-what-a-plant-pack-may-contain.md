# 0022 — A plant pack is data, carries no code and no secrets, and cannot change what a number means

- **Status:** proposed
- **Date:** 2026-09-10
- **Deciders:** @kalwei

## Context
House rule 4 says config, not code, at plant boundaries, and names the plant
pack as where "what a plant enables" lives. `README.md` principle 7 says a
second demo pack exists to prove adding a plant needs no code change.
Decision [0002](0002-kernel-and-modules.md) says "a module is code, a plant
pack is config; the seam keeps them apart".

**As of `2474a8b` there is no pack.** No `plant.toml` exists, no `packs/`
directory exists, and the only mention of a pack in `src/` is a sentence in
`src/fsmes/mcp/__init__.py` describing a mechanism that was never built. The
full inventory is in [the M8 design](../design/m8-packs-and-fleet.md); the
parts that force this decision are these.

A plant's identity is spread across **five artefacts that do not know about
each other**: the registry (`labs/multiplant/plants.toml`), a tag map, a seed
script, 66 `MES_*` settings, and four boundary mapping files.

**The registry has no schema.** Its seventeen keys are read into a plain dict
by `tomllib`. Nothing distinguishes a key from a typo, and the file and
[its documentation](../operate/registry.md) have already drifted apart in both
directions inside two weeks: `inspect_every`, `issue_every` and `inspect_all`
are read by `plant_env` and undocumented; `agent` is documented and read by
nothing.

**A plant's master data is a Python script.** The `init` key names a file that
`fsmes plant <name> init` runs as a subprocess with the plant's environment.
`labs/multiplant/machining/seed.py` is that script for the second plant. The
lab's README defends its location and is right to — it belongs outside `src/`
— but its *form* is arbitrary code that nobody can review before it touches a
plant.

**A pack carries a secret today.** `secret_key` sits in the registry in plain
text; the lab default is `lab-<name>-do-not-use-in-production`.

**Nothing can be enabled or disabled per plant.** `src/fsmes/api/app.py`
includes every router unconditionally; `src/fsmes/mcp_server.py` registers
every tool file at import; `fsmes info` reports one entry-point module
(`erpnext`) because everything else ships inside the kernel wheel. So the
milestone's own *done when* — "two packs with **different modules enabled**"
— cannot be met by any configuration that exists.

And the first real plant will need one of these. It is the artefact a controls
engineer drafts on site, from [the engineers' front
door](../operate/first-plant.md).

## Options considered
| Option | For | Against |
|---|---|---|
| **A pack is a directory of declarative data with a versioned `plant.toml`, validated by `fsmes pack check`; no code, no secrets, no renaming of anything a number depends on** | reviewable before it touches a plant; a typo is an error rather than a silent default; a pack is safe to attach to a support thread; it can be versioned and migrated the way the schema is | more product surface — a format, a validator, a migrator; converting the labs is real work; master data must move from a script into data |
| Keep the registry and grow it | nothing to build; it works today | it is the *lab's* file: it hard-codes `MES_ERP_MODE="off"`, holds a secret in the clear, has no schema, and has already drifted from its own documentation. Growing it makes each of those worse |
| Let a pack carry a small expression language for the awkward cases | every real plant has one awkward case | every configuration format that allowed one expression now has a language, an evaluator and a security surface. The escape hatch that already exists is a module, and a module is code and gets reviewed |
| Let a pack rename anything, including states and KPIs | plants have their own words and it is genuinely what they ask for | two plants' events stop meaning the same thing, and a fleet view is then comparing dialects. It also breaks the [scorecard](../operate/shadow-scorecard.md), whose whole discipline is that a difference is a difference and not a translation |
| No pack: a plant is a set of environment variables, as now | simplest; the multiplant lab proves it works | sixty variables with no schema, no version, nothing to check, nothing to migrate, and no way to hand a plant's configuration to anyone. It is what a first real plant would be given, and it is not good enough to give them |

## Decision
**A plant pack is one directory of declarative data, owned by the plant, that
completely answers "which plant is this?".** It contains `plant.toml`
(identity, profile, enabled modules, time zone, display words, storage,
serving, ERP/UNS/inbound modes, and a `[pack]` block naming its format
version and the MES versions it requires), the tag map, master data as data,
the boundary mapping files, optional line geometry, and a README.

Four things a pack **may not** contain:

1. **No code.** No Python, no hook, no expression to evaluate. Master data
   becomes data that `fsmes pack apply` seeds; the `init` script goes away.
   The escape hatch for anything a pack cannot express is a module.
2. **No secrets.** A pack *names* where a secret lives — the pattern
   `database_password_file` and `password_env` already use — and never holds
   one. A pack should be safe to paste into an issue.
3. **Nothing that changes what a number means.** `[words]` renames a label on
   a screen or in a report. It may not rename a state, a KPI, a capability, a
   role, an audit action, an MCP tool, an event `kind`, or any field an API
   response or an MQTT topic is built from. The protected list is enumerated
   in code and held by a test.
4. **No schema changes.** A pack cannot add a table or a column. That is a
   module.

`fsmes pack check` validates a pack with no database and no network and exits
non-zero: an unknown key is an error, a bad time zone is an error, a renamed
protected term is an error, a `requires` this version does not satisfy is an
error. What it cannot prove without a plant — that the OPC endpoint answers,
that the database is reachable — it reports as **unknown**, never as passing.

A pack carries a format version; the product carries a pack-format head the
way it carries an Alembic head. `fsmes pack migrate` moves a pack forward one
version at a time, backing it up and printing a receipt, in the same shape as
`fsmes plant migrate`. When both must move, the order is **pack first, then
database**, because a migration may need a value the pack now carries.

## Consequences
Easier: a plant's whole configuration is one reviewable directory that can be
version-controlled by the plant, diffed, checked before a change, and sent to
a maintainer without sending a password. A typo becomes a refusal with a
sentence instead of a default nobody noticed. The milestone's *done when*
becomes reachable, because `[modules]` has somewhere to live.

Harder: master data must be expressible as data. Some of what
`labs/multiplant/machining/seed.py` does today is generation, not
declaration, and generating a plant is exactly what a pack must not do — so
the lab keeps its generator as a lab tool that *writes* a pack, and the pack
is what the product reads. The registry's seventeen keys must each be placed
or dropped, including the three nobody documented. And there is now a second
migration chain to keep honest.

Not decided here: whether packs are discovered from a directory, listed in a
file, or both; and whether a pack may be a single file for the laptop case.
Both are shape rather than substance, and the answer should come from
converting the two lab plants.

To revisit when the first real plant's engineer has drafted one. If a pack
that describes a real plant needs a key that is not in the format, the format
was designed against simulations — and that is the finding, not the plant's
problem.

## House rules touched
Rule 4, config not code at plant boundaries, is the whole decision — and the
"no code" clause is what makes it true rather than aspirational, because a
pack that can run a script is a code change wearing a config file's name.

Rule 2, unknown is a valid answer: `fsmes pack check` reports what it could
not prove as unknown rather than counting it as passing, the same way
`fsmes erp check` does under decision
[0020](0020-what-supported-means-for-an-erp-connector.md).

Rule 1, never invent production, is what clause 3 protects. A plant that can
rename a state or a KPI can make two plants' numbers look comparable when
they are not, and a fleet view built on that invents production across a
company rather than at one machine.
