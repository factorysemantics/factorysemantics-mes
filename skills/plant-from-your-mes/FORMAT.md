# What is in `plant-shape.toml`, and what FactorySemantics does with it

Two audiences. A plant engineer who wants to know exactly what left their
building reads the first half. Whoever writes the next piece — the one that
turns this file into a running simulated plant — reads the second.

Nothing in this folder generates a plant. Producing the file is the whole job of
this skill; `fsmes shape plant` and `fsmes demo from-shape` read the file this
folder writes and turn it into a plant pack, and the mapping below is the
contract between the two.

`[meta]` opens with the version of this format the file was written to:
`shape_format = 2`. A file with no such key, or `shape_format = 1`, is a
format-1 shape and is still read exactly as it always was — see **Format 2**
below for what the second version adds and what a format-1 file is taken to
mean.

## Every section, and what it is allowed to contain

| Section | What it holds | What it can never hold |
|---|---|---|
| `[meta]` | when it was written, the window it measured, rows read, how many tables were understood and how many were not, the leak check's verdict | the database name, the server, a user |
| `[plant]` | how many machines; the naming grammar of their paperwork as patterns | a real order, lot, pallet or document number |
| `[[stations]]` | `ST-01`… in line order, rate a minute, scrap percent, good and scrap totals, seconds spent running, and in format 2 which area and cell each one sits in | their machine codes, names or descriptions |
| `[hierarchy]` *(format 2)* | how many areas, `AR-01`… and the cells `CL-01`… under each, and which parent each area has | their area, line, department or cell names |
| `[tag_nodes]` *(format 2)* | how the tags are organised above the machines: `ND-01`… , how deep the path is, which stations sit under each node, how many tags each holds | the channel, device or folder names; any part of a tag path |
| `[routings]` *(format 2)* | how many routings, `RT-01`… , the station sequence each one runs through, and which table they were read from | their routing codes, operation names or item numbers |
| `[[stations.analogs]]` | `AN-`/`CT-`/`DS-` by kind, unit, base, noise, decimals, whether it only moves while running, sampling interval, observed range, correlation with the machine's state | the tag path, the tag name, a single stored reading |
| `[stops]` | share of time in each state, stop duration distributions, reason categories with their shares, the unlabelled share, generic wording at the measured length | their reason codes, their reason text |
| `[quality]` | checks, fail share, non-conformances per thousand, disposition shares | a non-conformance number, an inspector, a measured value against a lot |
| `[orders]` | how many, typical and large quantity, typical open hours, how many are still open | an order number, item, customer |
| `[shifts]` | how many codes, each shift's start time of day and length | a crew name |
| `[documents]` | how many, the numbering patterns, the revision pattern | a document number or title |
| `[erp]` | calls in the window, each touchpoint's verb and path *shape*, calls an hour, byte range, failure share | the endpoint's own words, a reference number, a payload |
| `[[also_tracked]]` | one entry per table that could not be used: rows, columns, and which generic engineering words we recognised | their table name, their column names |

Two conventions that run through all of it:

- **`not measured` means not measured.** It never means zero. A section that
  could not be read says `found = false` and gives the reason, and
  `queries_not_run` in `[meta]` lists every query that did not run and why.
- **Every list states its total.** A pattern row says `seen` *and* `of_total`;
  a station count says how many of how many had production.

### Patterns

`#` is a digit, `A` a capital letter, `a` a lower-case one, and everything else
is itself. So `AAA-AA-###` is `SOP-QA-017`'s shape, and `AA-####-#######` is a
work-order number's. The values the patterns came from are discarded in the same
breath — they are read, counted, turned into a shape and handed to the leak
check.

### Vocabularies

Three of them — machine states, stop categories, quality dispositions — are
mapped onto a fixed standard list, so the customer's own words stay in the
customer's database:

- states: `running`, `stopped`, `starved`, `blocked`, `down`, `changeover`,
  `maintenance`, `other`
- stop categories: `mechanical`, `electrical`, `material`, `quality`,
  `operator`, `changeover`, `maintenance`, `tooling`, `process`, `other`, and
  `unlabelled` for the stops that carry no reason at all
- dispositions: `open`, `rework`, `scrap`, `accepted`, `rejected`, `use-as-is`,
  `closed`, `other`

`starved` and `blocked` are reported but are **not** counted as stops that need
a reason. They are the machine upstream or the one downstream, not somebody's
reason code, and counting them as unlabelled overstates the unlabelled share
badly — on the bottling line it was the difference between 79% and 20%.

`profile.py --keep-vocabulary` carries their own wording instead. It is off by
default and should only ever be on because the person said so, out loud, having
understood that their words then leave the building.

## Format 2

Format 1 could only describe one straight line of machines with one OPC node
each. Real plants are not that: a machine sits in an area and often in a cell
inside it, two cells feed one shared station, and the tags are organised by
PLC rather than by machine. Format 2 adds three sections for exactly those
three facts, and nothing else.

**A format-1 file is read as a format-2 file that measured none of them**, and
the converter says so out loud rather than pretending the source system was
asked:

| What format 2 can say | What a format-1 file means |
|---|---|
| `[hierarchy]` — areas and cells | one area, every machine in it |
| `[routings]` — what feeds what | one routing, every station in sequence order |
| `[tag_nodes]` — the grouping above the machines | one node per machine, directly under `Objects` |

The three sections are **each independent and each optional**, and each obeys
the `found = false` convention: a shape whose source MES has an area table but
no routing table says `[hierarchy] found = true` and `[routings] found =
false`, and the plant then has that plant's real areas and one straight
routing with a note saying the routings were never measured. Nothing is
guessed in between.

### `[hierarchy]`

```toml
[hierarchy]
found = true
basis = "a parent column on the asset table"
areas_seen = 2
areas = [
  { name = "AR-01", parent = "", cells = ["CL-01", "CL-02"] },
  { name = "AR-02", parent = "", cells = [] },
]
```

`name` and `cells` are generated codes in position order, never the plant's
own words. Each `[[stations]]` entry then carries `area = "AR-01"` and, when
the source said one, `cell = "CL-02"`. A station whose area is not in the
list, or that names none, goes in the first area — which is the honest
reading of "the source system did not say", not an invented third area.

### `[tag_nodes]`

```toml
[tag_nodes]
found = true
basis = "the first two segments of the tag path"
nodes_seen = 3
depth_p50 = 2
nodes = [
  { name = "ND-01", path = ["ND-01"], stations = ["ST-01"], tags = 7 },
  { name = "ND-02", path = ["ND-02"], stations = ["ST-02", "ST-03"], tags = 14 },
]
```

`path` is the *shape* of the browse path — how many levels there are and
which machines share each level — with generated names at every level. The
real segment text never leaves the building; what the simulated plant needs
is the grouping and the depth, and it serves the address space that way (two
machines under one node become two objects under one folder, as a Kepware
channel or an Ignition device folder holds a PLC's worth of machines).

### `[routings]`

```toml
[routings]
found = true
basis = "a routing header and operation table keyed by item"
routings_seen = 2
routings = [
  { name = "RT-01", steps = ["ST-01", "ST-03", "ST-05", "ST-06"] },
  { name = "RT-02", steps = ["ST-02", "ST-03", "ST-04", "ST-06"] },
]
```

`steps` is a station sequence. Two routings whose steps meet at one station
are two cells feeding a shared machine; a station on no routing is in the
plant and on the floor page with no order running through it, and the
converter says how many of how many that is.

The generator's own physics is a serial line with a buffer between each pair,
so the converter walks the graph these routings imply in topological order and
simulates *that* chain, while the routings an MES reader sees keep the real
shape. On a plant with two cells feeding one station the two cells are
therefore simulated one after the other rather than side by side. This is
written into the generated `plant.toml` under `[from_shape].notes`, because it
is the one place the look-alike plant is knowingly simpler than the plant it
was taken from.

## The second half: how this maps onto a FactorySemantics plant

The converter is `fsmes shape plant <shape> --out <dir>`, and it does not go
straight from this file to a generator input. It reads the shape into a typed
plant model — plant, areas, cells, stations, the ordered edges between them,
the tag nodes with their grouping, the vocabularies, the order book, the
shifts — and writes a whole plant pack out of that model: `line.json`,
`tag_map.json`, `floor.json` and `masterdata/*.json` are all *outputs*. That
middle model is why a new routing style or a new way of organising tags is a
new field here rather than a rewrite there.

`line.json` is the generator's own input — the file that describes a line as
data. These are the fields it already understands, and where each one comes
from in a plant shape.

| `line.json` | from `plant-shape.toml` |
|---|---|
| `stations[].name` | `stations[].name` (`ST-01`; the generator needs letters, digits and underscore, so `ST_01`) |
| `stations[].rate_per_min` | `stations[].rate_per_min` |
| `stations[].scrap_pct` | `stations[].scrap_pct` |
| `stations[].analogs[].name` | `stations[].analogs[].name` where `kind = "analog"` (`AN_01`) |
| `stations[].analogs[].base` | `…analogs[].base` |
| `stations[].analogs[].noise` | `…analogs[].noise` |
| `stations[].analogs[].decimals` | `…analogs[].decimals` |
| `stations[].analogs[].unit` | `…analogs[].unit`, when one was carried |
| `stations[].analogs[].running_only` | `…analogs[].running_only` |
| `duration_s` | `meta.window_hours × 3600`, or whatever window is wanted |
| `buffers` | **not in a plant shape.** Buffer capacity is not recorded by any MES we have seen; it has to be asked. Default and say so. |
| `events` | from `[stops]`: one `down` event per station scaled to its share of stopped time, `changeover` from the changeover share, `micro_stops` from the short-stop end of the duration distribution |
| `orders` | count from `[orders].orders_seen`, size from `qty_p50` |
| `seed` | chosen by whoever generates, not by the plant |

And the pack's other files, which are not the generator's business:

| pack file | from `plant-shape.toml` |
|---|---|
| `masterdata/equipment.json` | `[hierarchy]`'s areas and cells as ISA-95 `level`/`parent`, with the stations as work units under them |
| `masterdata/routings.json` | `[routings].routings[].steps` |
| `masterdata/downtime_reasons.json` | `[stops].reasons[]` — the categories, with the generic wording |
| `masterdata/nc_severities.json` | `[quality]`'s dispositions |
| `masterdata/shifts.json` | `[shifts]` — each code's start time of day and length |
| `tag_map.json` `machines[].group` | `[tag_nodes].nodes[].path`, which is how several machines come to sit under one node |
| `tag_map.json` `machines[].analog` | the station's process value: an analog that holds a level whether or not the machine is running is preferred over one that collapses to zero, because the second kind is almost always a cycle time |
| `floor.json` `stops.mix` / `stops.leave_unlabelled` | `[stops].reasons[].share_of_stops` and the unlabelled share, so a shift's stops come out in the plant's own proportions |
| `floor.json` `inspection.*.defects` | `[quality]`'s disposition shares |
| `plant.toml` `[from_shape]` | what the converter used, what it asked, and what it dropped — see below |

What the converter has to **ask** a person for, because no MES we have seen
records it:

- **buffer size** — how many pieces fit between two machines. One question,
  default 20, `--buffer 20 --yes` to answer it from a script. It is also what
  decides how much starving and blocking the plant shows, so it is asked and
  never defaulted in silence.

What a plant shape carries that nothing downstream uses, and which the
converter therefore **drops out loud**. Every one of these is listed in the
generated `plant.toml` under `[from_shape].dropped` with one line saying why,
and `fsmes shape plant` prints the list:

- `kind = "counter"` and `kind = "discrete"` tags. The generator writes its own
  counters and `ReadyBit`; a plant whose MES has eleven counters per machine is
  describing something the generator does not model.
- `state_correlation` per tag. Nothing reads it yet. It is in the file because
  it is the cheapest honest answer to "which of this machine's eleven signals
  actually tells you it is running", and a generator that wanted to place a
  precursor drift would want it.
- `[documents]` and `[erp]`. A pack holds no documents and calls no ERP.
- `[quality].nonconformances_per_1000_checks`. The plant raises findings from
  its own control rules on its own readings; a rate imposed on top would
  overwrite what the charts actually find.
- `[plant].naming_grammar`. Deliberately not used: the generated plant names
  its own orders and lots in the product's own generic form, so nothing shaped
  like your numbering appears on a screen anybody demonstrates.
- each tag's `observed_min`/`observed_max` and `modal_state`, and the
  `[orders]` open-hours figures — each for a stated reason, printed with the
  rest.
- anything in `[[also_tracked]]`: by definition the tables the profiler could
  not use. Each is a candidate feature for this product, which is why the
  skill carries them at all.

And what the converter knowingly does **not** generate, each said in
`[from_shape].notes`: no maintenance plans or jobs (the shape carries no
maintenance table), no setpoints, and no planted drift or scrap burst.

## Running it against our own bottling line

The proof this skill works at all is that it recovers a plant it was not written
for. `tests/unknown_mes.py` builds a deliberately foreign SQL MES out of the
bottling line's own generated history — different table names, a state column of
three-letter words, tag history in one tall table — and
`tests/test_a_plant_shape_built_blind_from_an_unknown_sql_mes_is_the_bottling_line.py`
asserts that the mapping is proposed correctly from the catalogue alone, that all
six stations come back in line order with their rates and scrap shares, that
every analog's base and spread come back within tolerance of `line.json`, and
that not one order code, lot, pallet, person code, non-conformance number or
free-text note from that database appears in the output.

`example/plant-shape.toml` is what that run produces. Read it alongside this
page.
