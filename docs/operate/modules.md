# How-to — switch a module off

A plant installs what it needs and no more. This page is how a plant that
does not do serialised traceability, or does not have an ERP, stops serving
those parts of the MES — and what exactly "off" means, because the answer is
narrower than it sounds.

Nothing here changes anything for a plant that does not use it. With no
setting, every module is on, which is what every plant had before the setting
existed.

## The setting

One environment variable, `MES_MODULES`, read left to right:

| Value | Means |
|---|---|
| `all` | every module. This is the default |
| `all,-quality` | every module except quality |
| `all,-quality,-erp` | every module except those two |
| `quality,maintenance` | the kernel, plus those two, and nothing else optional |

A name this version does not have is **refused when the plant starts**, with
the list of module names in the message. It is not ignored: a typo that
silently changes nothing is how a plant comes to believe it switched a module
off while it is still serving it.

It is read once, at start-up, like shadow mode. Changing which modules a plant
serves is a restart.

## What "off" means

Off means **not served**:

- its API routes are gone, and answer **404** — not 403, and not an error
  page. A caller asking a plant for something it does not do is told the
  route does not exist;
- it is absent from `/openapi.json`, so an integrator reading the plant's own
  API document sees what that plant actually offers;
- its dashboard screens are not served. A page that loaded and then failed
  every fetch would look broken rather than absent;
- its agent tools are not registered, so an MCP client listing this plant's
  tools does not see them.

## What "off" does not mean

Off does **not** mean *not stored*.

The database schema is one migration chain for every plant
([0021](../decisions/0021-one-database-per-plant.md),
[0022](../decisions/0022-what-a-plant-pack-may-contain.md)). A disabled
module's tables are still created by `fsmes migrate`, its existing rows are
untouched, and everything is exactly where it was when the module is switched
back on. Nothing about this setting drops a table or deletes a row.

That is deliberate. A plant that switches quality off for a quarter and back
on should find its specifications and its calibration history waiting, not a
hole. It also means switching a module off does **not** reclaim disk.

!!! warning "Switching a module off does not remove its data"
    If a module must go for a reason other than "we do not use it" — a
    retention rule, a regulator — turning it off is not the tool. Its rows are
    still in the database and still in every backup.

## The modules

Twenty-three modules, of which **nine are the kernel** and cannot be switched
off — master data, routings, orders, dispatch, execution, equipment, audit,
auth and the dashboard front door. A plant without those is not an MES, and
asking for it is refused.

The **fourteen a plant may switch** are:

| Module | What goes with it |
|---|---|
| `quality` | specifications, checks, non-conformances, SPC, the gauge register |
| `maintenance` | plans, orders due on use, the maintenance workspace |
| `scheduling` | the schedule board and the working calendar |
| `serialization` | serialised units, genealogy by serial, the trace screen |
| `documents` | controlled work instructions and their revisions |
| `coa` | certificates of analysis |
| `adjustments` | the recommendation queue — the only path to a PLC |
| `triggers` | what the plant does when a signal crosses a line |
| `erp` | the ERP connector and its settings (`MES_ERP_*`, `MES_ERPNEXT_*`) |
| `analysis` | the shift analysis screen |
| `line` | the line view, including the 3D scene |
| `kpis` | the OEE and order KPI routes |
| `assist` | the in-screen assistant |
| `design` | the design partner (a development tool) |

The authoritative list is `src/fsmes/modules.py`, which also says, per module,
which routers it mounts, which screens it serves, which agent tools it
registers and which tables its rows live in.

## Turning one off

```bash
MES_MODULES=all,-serialization fsmes serve
```

or, in a plant's `.env`:

```
MES_MODULES=all,-serialization,-coa
```

Check it took:

```bash
curl -s localhost:8000/openapi.json | grep -c '"/trace'
# 0
```

## What this is for

M8's goal is two plant packs with different modules enabled running from one
codebase. This setting is the mechanism; the plant pack is where it is going
to live. When a `plant.toml` gains a `[modules]` table it will compile down to
exactly this string, so a plant that sets `MES_MODULES` today is setting the
same thing by hand.

## How the boundary is kept

Two tests, and they are worth knowing about because they are what stops this
capability quietly rotting:

- **`tests/test_core_purity.py`** forbids the kernel importing a module. A
  module the kernel imports cannot be switched off — the setting could decline
  to mount its routes, but the kernel would still drag the package in at
  start-up.
- **`tests/test_no_tenant_literals.py`** forbids a plant's name, equipment
  code or material code appearing in code under `src/`. It builds its list
  from the lab plants in `labs/` rather than from a list somebody typed, so a
  new plant extends the guard instead of escaping it. This is house rule 4 —
  *config, not code, at plant boundaries* — as a measurement.
