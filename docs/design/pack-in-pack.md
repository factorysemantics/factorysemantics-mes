# A pack inside a pack

**Asked by Scott, 2026-10-13:** *"I want this to be extremely robust and ensure
scalable complexity with pack in pack scenarios, different styles of routings,
different OPC organizational structures."*

Routings and OPC organisation are answered: they are fields in the shape file
(format 2) and objects in the plant model, so a branching routing or a plant
whose tags live under channel/device folders is **data**, not a code change.
See `skills/plant-from-your-mes/FORMAT.md` and
`docs/operate/plant-from-your-mes.md`.

Pack-in-pack is the third one, and it is a question rather than a feature. This
page answers it in one screen.

## What "a pack inside a pack" could mean

Three different things get called that, and they have three different answers.

1. **A plant with areas and cells inside it** — one site, several areas, cells
   under them, machines under those. A bottling hall and a packing hall under
   one roof, one dashboard, one shift calendar.
2. **Several plants that roll up** — two factories, each with its own
   dashboard, its own shifts, its own database, reporting to one place.
3. **A pack that includes another pack as a part** — a vendor ships
   `filler-line-v3` as a folder, and a site pack lists it the way a BOM lists a
   sub-assembly.

## What already works

**(1) is done, today, with no nesting at all.** A pack's
`masterdata/equipment.json` is the ISA-95 hierarchy as rows: every node has a
`level` (`enterprise`, `site`, `area`, `work_center`, `work_unit`) and a
`parent`. The tree is as deep as the rows say. The grouped reference shape in
this release is exactly that case — two areas, four cells, six machines — and it
comes out of one shape file, into one pack, onto one dashboard:

    fsmes shape plant skills/plant-from-your-mes/example/plant-shape-grouped.toml --out ./acme

OPC organisation is separate from it and nests independently: `tag_map.json`
gives each machine a `group`, the folders above its own object, so two machines
can share one device node while sitting in different cells. A plant's address
space and a plant's org chart are not the same tree, and in brownfield sites
they routinely disagree.

**(2) is the fleet, not a pack.** Decision
[0021](../decisions/0021-one-database-per-plant.md) gives each plant its own
database; `fsmes fleet` runs several packs side by side and the console observes
them. Two factories are two packs and two ports, which is what a plant engineer
expects when one site's database goes down and the other keeps running.

**(3) does not exist, and nothing in this release needs it.**

## What would force real nesting

Only one thing: **a part of the plant that needs its own copy of something a
pack holds exactly one of.** Concretely — its own time zone, its own shift
calendar, its own enabled module set, its own database, its own serving port, or
its own `[words]`. A hall in a different country is the honest example. Another
is a contract line that must stop and start without touching the rest of the
site.

Everything short of that — more areas, deeper cells, more routings, stranger tag
folders, forty stations instead of six — is more rows in files a pack already
has, and this release carries a 40-station, five-area, three-routing shape
through to a running plant to show it.

## Recommendation

**Do not nest packs. Keep decision
[0022](../decisions/0022-what-a-plant-pack-may-contain.md) as it stands: a pack
is one folder.** Answer the three readings this way:

- **Inside one plant**, go deeper in `equipment.json`. No format change, no new
  concept, and `fsmes pack check` already validates that every node's parent
  exists.
- **Across plants**, use the fleet. If roll-up reporting is wanted later, that
  is a console feature reading several plants' APIs — not a pack that contains
  another pack.
- **For a vendor-shipped line**, the thing to add when somebody actually asks is
  an *include* at build time, not nesting at run time: `fsmes pack compose`
  reading two folders and writing one. The pack the plant runs stays one folder,
  so nothing downstream — `pack check`, `pack apply`, the fleet, the console —
  learns a new shape.

The cost of being wrong here is small and the cost of being early is not. A pack
that can contain a pack doubles the number of states every reader of a pack has
to handle, forever, and `fsmes pack check`'s whole value is that it can read a
pack with no database and no network and say "usable". Nesting is a feature with
one customer request and no customer; the hierarchy inside one pack has two
reference shapes and a test.

## What is left open for it on purpose

The plant model keeps `areas` as its own objects with a `parent` of their own
(`src/fsmes/shape/model.py`), and the shape file's `[hierarchy]` table carries a
`parent` per area. Both are unused today — every area the converter writes sits
straight under the site — and both are there so that an area under an area, or
later a pack composed of two, needs no change to the shape file a customer has
already written on their own MES box.
