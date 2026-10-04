# Your plant in FactorySemantics, without sending anyone your data

**What it is.** A folder — `plant-from-your-mes.zip` on the release page — that
you unpack next to your own AI assistant. You ask it *"I want to put my plant
into FactorySemantics without giving away anything proprietary — how?"*, and it
walks you through read-only queries against your own MES database, in widening
steps, and writes **one file: `plant-shape.toml`**.

You read that file before it goes anywhere. Then it is yours to send, or not.

Nothing in this folder runs a simulation. Producing the file is the whole job.
Turning the file into a running plant you can click around is the next piece of
work and is not built yet.

## What leaves the building

One text file. It holds counts, shares, distributions and patterns:

- how many machines, in line order, as `ST-01`, `ST-02` …
- how fast each one runs, how much it scraps, how long it actually ran
- every tag as `AN-01`, `CT-01`, `DS-01` — what kind of signal it is, its unit,
  where it sits, how much it wobbles, how often it is sampled, and whether it
  only moves while the machine runs
- the share of time in each state, how long stops last, what kind of thing goes
  wrong as a category, and **how many stops carry no reason at all**
- your quality loop: checks, failures, non-conformances, what happens to them
- order sizes, how long an order stays open, your shift pattern
- how you number your paperwork, as a shape: `AAA-AA-###`
- what your MES says to your ERP — how often, how big, how often it fails — as
  a shape, not as endpoint names
- and a list of **things your MES tracks that FactorySemantics does not model
  yet**, described by shape. Tool changes and their cost, energy, whatever you
  have. That list is a feature request written by your own plant.

### What does not leave

No order number, work order, job, batch, lot, serial, pallet or container id. No
person, operator, customer, supplier or crew name. No item code or description.
No price. No address. No free text of any kind — not a downtime note, not a
comment, not a title. No recipe values. Not one of your table or column names.

Your downtime wording stays in your database. The file carries the *category* —
mechanical, electrical, material, quality, operator, changeover, maintenance,
tooling, process — and a sentence we wrote, trimmed to the same length as yours,
so a simulated plant reads like a plant and not like a form.

### Three things that make that checkable rather than a promise

1. **Every query is `SELECT` only and bounded.** They are short, they are in
   `queries/`, and you can read all thirty-two of them in ten minutes. A test in
   this repository fails if one of them ever contains `INSERT`, `UPDATE`,
   `DELETE`, `DROP`, `CREATE`, `EXEC` or a second statement.
2. **Nothing phones home and nothing installs.** The three scripts use the
   Python standard library and nothing else — no database driver, no package, no
   network code at all. A test fails if one of them ever imports anything else.
3. **The leak check can refuse.** Before the file is written, every identifying
   string in your database is compared, case-insensitively, against the finished
   text. If one appears, **the file is not written**. Its verdict is the first
   thing in the file: *"passed: 0 of 1,015 identifying values appear in this
   file"*.

## How to run it

SQL Server first, because that is what the first plant to try this runs. SQLite
works too and is what this repository's own test uses.

```bash
unzip plant-from-your-mes.zip
cd plant-from-your-mes
```

Your assistant reads `SKILL.md` and does the rest. If you would rather drive it
yourself, it is three commands:

```bash
# 1. Guess which table is which, from names alone. Read this back out loud —
#    it will get some lines wrong, and correcting it is the whole skill.
python scripts/profile.py --sqlite plant.db --propose-mapping mapping.toml

# 2. Write the one file. This runs the leak check and refuses on a hit.
python scripts/profile.py --sqlite plant.db --mapping mapping.toml --out plant-shape.toml

# 3. Read it back in plain words.
python scripts/explain.py plant-shape.toml
```

### On SQL Server, where there is no driver

The folder deliberately installs nothing, so it cannot connect to SQL Server
itself. Two ways round it:

```bash
# Get the exact SQL to paste into SSMS, Azure Data Studio or sqlcmd
python scripts/profile.py --mapping mapping.toml --dialect mssql --emit-sql ./to-run

# Save each result as <query name>.csv, then
python scripts/profile.py --csv-dir results/ --dialect mssql \
    --mapping mapping.toml --out plant-shape.toml
```

Twelve of the sixteen queries come out ready to paste. The other four are
per-table or per-column templates — you run them once per table you care about —
and they come out as `*.template.sql` with a line saying what to substitute.

In CSV mode the leak check has no database to sample, so save the output of
`04b_column_sample.sql` as one value per line and pass `--samples values.txt`.
If you do not, the file says the check ran against a smaller sample. It says so
in the file, where you can see it.

### Before you point step 5 at a production box

`05b_tag_samples.sql` reads your tag history, which is the big table. Look at
the plan: it should be a backward scan of an index on the timestamp, not a sort.
Run it out of hours. Lower `--row-cap` if you need to. On SQL Server,
`01_inventory.sql` deliberately uses the row count the engine already keeps
rather than `COUNT(*)` on every table, because an exact count over a historian
can take minutes and escalate locks — `01b_row_count.sql` is there for the
handful where the exact number matters.

## What we have actually proven, and what we have not

**Proven.** The skill is run blind, in this repository's test suite, against a
SQL MES it was not written for: `tests/unknown_mes.py` builds a deliberately
foreign schema — its own table names, a state column of three-letter words, tag
history in one tall table — and fills it from the bottling line's own generated
hour. With no hint about that schema, the skill proposes the mapping from the
catalogue alone, and the plant it describes is bottling: six stations in line
order, every rate within a tenth of nameplate, every scrap share within a third
of a percentage point, and **all twenty analog tags recovered with the right base
and the right spread**. Not one order code, lot, pallet, operator code,
inspector code, non-conformance number or technician's note from that database
appears in the output. See
`tests/test_a_plant_shape_built_blind_from_an_unknown_sql_mes_is_the_bottling_line.py`.

`skills/plant-from-your-mes/example/plant-shape.toml` is what that run produces.
Read it; it is the best answer to "what exactly would I be sending?"

**Not proven.** There is no SQL Server on this machine or on the CI runner, so
**no SQL Server has ever parsed the `queries/mssql/` set.** What is checked is
structural: every placeholder resolves, each query is one bounded `SELECT` in
SQL Server quoting, and none of the SQLite-only spellings has been copied across
(`LIMIT`, `substr`, `GROUP BY 1`). The first person to run them against a real
instance is the one who finds out whether SQL Server agrees. Expect to fix a
line or two, and expect the timestamp comparisons to be the place it happens:
these queries compare a timestamp column against an ISO text value, which SQL
Server converts implicitly and which can behave differently on a `datetime2`
with an unusual collation or a non-UTC column.

**Also not proven:** that the mapping proposal works on anything but the one
invented schema it has been tried against. It matches on table and column names
and on nothing else. On a real plant it will get lines wrong, which is exactly
why `SKILL.md` tells the assistant to read the draft back to a person before
profiling with it. A wrong mapping leaks nothing — it describes a plant that is
not yours, and you will hear that in the first three lines of `explain.py`.

## Building the zip

```bash
fsmes skills-zip --check            # what CI runs: folder and shipping list agree
fsmes skills-zip                    # writes plant-from-your-mes.zip
uv run python -m zipfile -l plant-from-your-mes.zip
```

The contents are a list in `src/fsmes/skills.py`, not a glob, so adding a file
to the skill is a line in a diff and a stray file fails the check in either
direction.

## Where the file goes next

`FORMAT.md` inside the zip says what every section means and how each field maps
onto `labs/kepsim/line.json` — the file the simulator already reads. It also
lists, plainly, what a plant shape carries that the generator has no field for
yet, and the one thing a plant shape cannot know and somebody has to be asked:
**buffer capacity between machines.** No MES we have seen records it.
