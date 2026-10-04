# What is in `plant-shape.toml`, and what FactorySemantics does with it

Two audiences. A plant engineer who wants to know exactly what left their
building reads the first half. Whoever writes the next piece — the one that
turns this file into a running simulated plant — reads the second.

Nothing in this folder generates a plant. Producing the file is the whole job of
this skill; `labs/kepsim/line.json` and the plant pack are where it lands next,
and the mapping below is the contract between the two.

## Every section, and what it is allowed to contain

| Section | What it holds | What it can never hold |
|---|---|---|
| `[meta]` | when it was written, the window it measured, rows read, how many tables were understood and how many were not, the leak check's verdict | the database name, the server, a user |
| `[plant]` | how many machines; the naming grammar of their paperwork as patterns | a real order, lot, pallet or document number |
| `[[stations]]` | `ST-01`… in line order, rate a minute, scrap percent, good and scrap totals, seconds spent running | their machine codes, names or descriptions |
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

## The second half: how this maps onto a FactorySemantics plant

`labs/kepsim/line.json` is the generator's input — the file that describes the
bottling line as data. These are the fields it already understands, and where
each one comes from in a plant shape.

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

What a plant shape carries that `line.json` has **no field for yet**, and which
is therefore a decision for somebody rather than a conversion:

- `kind = "counter"` and `kind = "discrete"` tags. The generator writes its own
  counters and `ReadyBit`; a plant whose MES has eleven counters per machine is
  describing something the generator does not model.
- `[stops].reasons[].generic_wording` and the category shares. The downtime
  vocabulary is a plant pack's business (decision 0036's `hold_rules` shape), not
  the generator's — the generated floor has to *use* the vocabulary, which is the
  second simulation fix in `STATE.md` and not done.
- `[quality]`'s dispositions. Same: the simulator raises non-conformances and
  dispositions none of them today.
- `[shifts]`, `[documents]`, `[erp]`. These belong in a plant pack, not in the
  line description.
- `state_correlation` per tag. Nothing reads it yet. It is in the file because
  it is the cheapest honest answer to "which of this machine's eleven signals
  actually tells you it is running", and a generator that wanted to place a
  precursor drift would want it.

So the next piece of work is a converter with a short, stated list of things it
has to ask a person for — buffers first — and not a silent set of defaults.

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
