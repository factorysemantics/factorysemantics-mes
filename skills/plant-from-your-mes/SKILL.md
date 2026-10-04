---
name: plant-from-your-mes
description: >
  Turn a plant's own SQL MES into one safe, readable file — plant-shape.toml —
  that describes the shape of their line without carrying anything proprietary,
  so they can see their plant simulated in FactorySemantics MES without sending
  anyone their data. Use this when somebody asks how to get their plant,
  their process flow, their tags, their downtime reasons or their naming
  conventions into FactorySemantics without divulging trade secrets, customer
  data, lot or pallet numbers, or anything specific about their business.
  SQL Server first; SQLite supported for testing.
---

# Putting your plant into FactorySemantics without giving anything away

You are reading this because somebody asked a question close to:

> *"I want to input my data into FactorySemantics, without divulging any trade
> secrets, important or proprietary information, or any kind of specifics about
> the nature of the business or its functions. How do I do that?"*

Here is the honest answer, and you should give it to them in these words before
you run anything.

**We can describe the shape of your plant without copying your plant.** Your
MES knows how many machines you have, how fast they run, how much they scrap,
what your tags read and how much they wobble, how often you stop and what kind
of thing goes wrong, how big your orders are, how your shifts work and how you
number your procedures. All of that is *shape*. None of it is a lot number, a
pallet, a customer, a person, a price or a recipe.

So: we run read-only queries that count, measure and group. We never copy a
row. What comes out is **one file, `plant-shape.toml`**, in plain text, which
**you read before it leaves your building**. It names your machines ST-01,
ST-02 and so on. It names your tags AN-01, CT-01, DS-01 by what kind of signal
they are. Your words for downtime reasons stay in your database — the file
carries the *category* and a sentence we wrote to the same length as yours. Your
document numbers appear as `SOP-AA-###`, which is the shape of the number and
not the number.

Then one more thing, because it is worth more than people expect: the file ends
with a list of **things your MES tracks that FactorySemantics does not model
yet** — tool changes and their cost, energy, whatever you have — described by
shape. That list is how the product learns what to build next.

## The promise, in five lines

1. **Every query is `SELECT` only and bounded.** No INSERT, UPDATE, DELETE,
   CREATE, DROP, EXEC or temp table, anywhere in this folder. Read them. They
   are short on purpose.
2. **Nothing phones home.** These scripts have no network code at all. They use
   the Python standard library and nothing else — no install, no driver, no
   dependency.
3. **One file leaves, and the person reads it first.** `plant-shape.toml`, and
   `explain.py` reads it back in plain words so they do not have to read TOML.
4. **The leak check can refuse.** Before the file is written, every identifying
   string in the database is compared against the finished text. If one appears,
   **the file is not written at all**.
5. **The connection is theirs.** No credentials are in any file here, and none
   are asked for. They connect, or they run the queries in their own client and
   hand you the results.

## What never gets copied, no matter who asks

Ids and codes of any kind. Order, work-order, job, batch, lot, serial, pallet,
container, licence plate. Names: people, operators, customers, suppliers,
crews, sites. Item codes and item descriptions. Prices, costs and quantities
tied to a customer. Addresses. Free text of any kind — notes, comments,
descriptions, titles. Recipe and formulation values. Table and column names
from their schema. If you find yourself about to put one of those in the output
file, stop: the answer is a count, a share, a distribution or a pattern instead.

## How to work: widen slowly, and ask before each step

Six steps. **Do not skip ahead, and tell the person what the next step reads
before you run it.** Each step is more intimate than the last, and a plant
engineer who has watched steps 1–3 come back harmless will let you run 4–6. One
who is shown step 6 first will not, and should not.

### Step 1 — what is in there at all (`01_inventory.sql`, `01b_row_count.sql`)

Table names and row counts. That is all. Say what you found: *"this MES has 48
tables; the biggest has 211 million rows and is almost certainly tag history."*

On SQL Server, `01_inventory.sql` uses the row count the engine already keeps
rather than `COUNT(*)` on every table — an exact count over a historian can take
minutes and escalate locks. Use `01b_row_count.sql` on the handful that matter.

### Step 2 — the columns (`02_columns.sql`)

Names, declared types, which are keys. Still the catalogue; still no data.

Now **work out the mapping**, and this is the step where you need the person.

    python scripts/profile.py --sqlite plant.db --propose-mapping mapping.toml
    # or, for SQL Server: save 01 and 02 as CSV and
    python scripts/profile.py --csv-dir results/ --dialect mssql --propose-mapping mapping.toml

That writes a draft. It guesses from names alone and it will get some of them
wrong. **Read it back to the person, line by line, as questions:** "is
`EQUIP_MASTER` your machine list? is `HIST_NUM` where tag values live? where do
you record a stop and its reason?" Correct the file with their answers. A wrong
mapping leaks nothing — it describes a plant that is not theirs, which they will
spot the moment `explain.py` reads it out.

If a section cannot be mapped, **leave it out**. The output will say that
section was not measured and why, which is the truth. Do not guess a table in so
the file looks complete.

### Step 3 — when, and how often (`03_window.sql`)

Per table: first row, last row, how many, how many distinct days. This is where
you find the table nobody has written to since 2019, and it is how the output
can state the window it measured instead of implying it measured everything.

### Step 4 — the shape of the columns (`04_column_profile.sql`, `04b_column_sample.sql`)

Null share, distinct count, lengths. Then — and say this out loud before you run
it — `04b_column_sample.sql` reads **actual distinct values** from the identifying
columns. It is the only query here that does. Two things happen to them:

- a **pattern** is derived (`AA-####-#######` from the order numbers), and
- the values are handed to `leakcheck.py` as the thing it hunts for in the
  output.

They are not written anywhere. If the person is not comfortable with that query,
skip it: you lose the naming grammar and the leak check falls back to sampling
the database itself, and the output says so.

### Step 5 — the machines and the tags (`05_assets.sql`, `05b_tag_samples.sql`)

The machine list, then a bounded sample of recent tag history with the machine's
state resolved beside each reading. This is the step the simulation is actually
built from: base, spread, sampling interval, whether a tag only moves while the
machine runs, how strongly it tracks the machine's state.

**Before you run `05b` on a production box**: it touches the big table. Look at
the plan — it should be a backward scan of an index on the timestamp, not a
sort. Run it out of hours. Lower `--row-cap` if you need to. If their MES does
not keep closed state intervals (no "ended at" column), use
`05c_tag_samples_nostate.sql` instead and say in your summary that tag bases are
medians of everything rather than of running time, which is weaker.

### Step 6 — how the plant behaves (`06*.sql`)

Stops and their durations and categories; good and scrap per machine; the
quality loop and what happens to a non-conformance; order sizes and durations;
the shift pattern; the document numbering. Reason *text* is never read — only
its length, so the generic sentence in the output can be the same size as
theirs.

### Step 7 — the ERP (`07_erp.sql`)

Which calls, how often, how big, how often they fail. **This is the step most
likely to find nothing**, because plenty of plants integrate by nightly file or
through middleware that logs somewhere else entirely. If there is no call log,
do not guess: ask the person *"how does this MES talk to your ERP, and does it
record that it did?"* and write their answer into the `[erp]` section by hand as
a note. Nothing found is reported as nothing found. It is never reported as
none.

## Then: write the file, and read it back

    python scripts/profile.py --sqlite plant.db --mapping mapping.toml --out plant-shape.toml
    python scripts/explain.py plant-shape.toml

`profile.py` runs the leak check itself and refuses to write on a hit. You can
also check an existing file at any time:

    python scripts/leakcheck.py --sqlite plant.db --shape plant-shape.toml

Read `explain.py`'s output **to the person, out loud**. They will know within
three lines whether it is their plant. The three things they notice first are
the number of machines, the scrap percentages, and the shift pattern — if any of
those is wrong, the mapping is wrong, so fix the mapping and profile again
rather than editing the file.

### For SQL Server, where there is no driver here

This folder deliberately installs nothing, so it cannot connect to SQL Server
itself. Two ways round it, and the second is usually easier in a plant:

1. The person runs each query in whatever client they already trust (SSMS, Azure
   Data Studio, `sqlcmd`) and saves each result as CSV named after the query —
   `05b_tag_samples.csv`, `06_states.csv`, and for the per-table templates
   `03_window__HIST_NUM.csv`. Then:

       python scripts/profile.py --csv-dir results/ --dialect mssql \
           --mapping mapping.toml --out plant-shape.toml

2. Or get the filled-in SQL out of the mapping first, so they have exactly the
   text to paste:

       python scripts/profile.py --mapping mapping.toml --dialect mssql --emit-sql ./to-run

   In CSV mode the leak check has no database to sample, so save the output of
   `04b_column_sample.sql` as one value per line and pass `--samples values.txt`.
   If you do not, the output says the check ran against a smaller sample, and
   that sentence is in the file for the person to see.

## When to stop and ask, rather than carry on

- Before step 4, step 5 and step 6, each time. Say what the step reads.
- When a mapping guess is wrong and you cannot tell what the right table is.
- When a query is slow or the person looks worried about load. Lower
  `--row-cap`, or come back out of hours.
- When something in the data is clearly not the plant you were told about —
  twelve machines where they said six, or a two-year gap in the history. Say so.
  That is a finding, not an error.
- When they ask you to put something in the file that this skill does not carry.
  The answer is no, and the reason is that the file's whole value is that
  everybody can be sure what is in it. Offer the count or the share instead.

## What you give back

1. `plant-shape.toml` — the one file.
2. `explain.py`'s output, read to the person.
3. Your own short summary: what you mapped, what you could not and why, what
   step you stopped at, and the `[[also_tracked]]` list — *things your MES tracks
   that FactorySemantics does not yet* — because that list is a feature request
   written by their own plant.

`FORMAT.md` says what every section of the file means and how it maps onto a
FactorySemantics plant. Nothing in this folder runs a simulation; producing the
file is the whole job.
