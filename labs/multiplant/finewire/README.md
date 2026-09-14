# Fine Wire Drawing Works, Hall 2 — the adversarial pack

**Invented.** There is no such works and no such customer. Every code, name
and number in this directory was made up, in this repository, to put pressure
on the plant-pack format. Nothing here has ever been started, and there is no
line data for it because there is no line.

## Why a third pack

`bottling` and `machining` disagree about the **line**: six stations against
three, different names, analogs, rates, buffers, product, routing and
specification. That is what shakes a hard-coded station name out of the
product, and it did its job.

They agree about everything a `plant.toml` carries. Same modules, same clock,
same words, same ERP mode, same storage, same profile. A pack format proved
only against those two would be the demo plant's shape with a different name
on it.

## What this one disagrees with

| | bottling & machining | finewire |
|---|---|---|
| Clock | `America/Chicago`, `America/New_York` | `Europe/Berlin` |
| Profile | `laptop` | `plant` — so it must say its own name |
| Modules | every one | `serialization` and `coa` off |
| Words | the product's | a **coil**, not a lot; an **alloy**, not a material |
| Storage | one SQLite file | PostgreSQL, password in a file the pack names |
| ERP | `off` | `file` — the confirmation handoff |
| Namespace | `off` | `log`: every topic built, nothing published |
| Inbound | none | a downtime feed from the hall terminal, in Berlin time |
| Tag map | browsable objects | flat node ids per tag, a vendor server's shape |
| Serving | simulates | serves only; there is nothing to replay |
| Accounts | the lab list | one, whose password comes from a named variable |

## What it proved

Two modules off is not a claim in this file; it is checked. `tests/
test_pack_third.py` builds settings from this pack — no plant started, no
database, no broker — and asks:

- the API for `/trace/...` and `/coa/...`, which answer **404**, while
  `/health`, `/workorders` and `/quality/specs` answer as they always did;
- `/openapi.json`, which describes neither module, so an integrator reading
  it is not told about a screen this plant does not serve;
- the MCP server, in a subprocess, which registers **eight** tool files
  where a full plant registers ten.

And `fsmes pack check` reads every file here offline.

## The finding

Writing it changed **nothing** under `src/` — no new setting, no new branch,
no special case. Under the M8 design's own risk table that is the result that
says the format holds. What it did change is three tests, each because the
guard they hold had to learn that a plant can be a pack: `no_tenant_literals`
reads a pack's `plant.toml` and its master data, and the module-wall tests
gained a sibling that switches modules off from a pack rather than from an
environment variable.
