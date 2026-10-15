# Stage 2 — A plant shaped like theirs, from the MES they already have

**Done with this stage when** `fsmes demo from-shape` has started a plant built
from their own `plant-shape.toml`, and the dashboard shows their machine count
and their shift pattern.

The point of this stage: they do not have to believe a bottling demo. They get
*their* line — the right number of machines in the right order, running at
their rates, scrapping at their rates, stopping as often as they stop — and
not one row of their data leaves their database.

## What you need before you start

- Stage 1 done: `fsmes` runs somewhere.
- **Two machines, usually.** The MES database lives on one box; `fsmes` is
  installed on another. The box with the SQL Server on it very often has no
  Python and no internet and will not be getting either. That is normal and
  the design expects it.
- Read-only access to their MES database, in whatever client they already
  trust. SQL Server first; SQLite works for a test.
- Their patience for about an afternoon. This is the stage with a person in
  the loop.

## The commands, in order, with what to paste back

### 1. Get the folder onto the box that has the MES on it

On the machine where `fsmes` is installed:

```
fsmes skills unpack plant-from-your-mes ./assistant
```

It writes the folder with no internet, out of the installed package, and says
how many files landed. If that machine cannot reach the MES box, they copy the
folder across however they normally move a file — it is text and scripts, a
few hundred kilobytes.

If they want this whole assistant folder again instead:
`fsmes skills unpack assistant ./assistant`. `fsmes skills list` says what
this installation carries.

### 2. Open `plant-from-your-mes/SKILL.md` and follow it

**This is where you switch pages.** That folder is stage 2's own instructions,
written for you, and it is more careful than this page can be: seven steps
that widen slowly, each one read out to the person before it runs, with the
promise that every query is `SELECT` only and nothing phones home.

Do not summarise it. Open it and work it. The short version of what happens:

- The scripts need **nothing but a plain Python 3.11 or newer** on the MES
  box. No `fsmes`, no database driver, no internet, no AI. They read TOML with
  the standard library.
- For SQL Server there is deliberately no driver in the folder, so the person
  runs each query in SSMS or `sqlcmd` and saves the result as a CSV named
  after the query; the scripts read the CSVs.
- `scripts/profile.py` writes `plant-shape.toml`. Before it writes, it runs
  `leakcheck.py`: every identifying string it sampled from their database is
  hunted for in the finished text, and **if one appears, the file is not
  written at all**.
- `scripts/explain.py` reads the file back in plain English. Have them read
  that out. They will know within three lines whether it is their plant.

### 3. Turn the file into a plant, on the `fsmes` box

```
fsmes shape plant plant-shape.toml --out ./acme --buffer 20
fsmes pack check ./acme
fsmes demo from-shape plant-shape.toml --name acme --buffer 20
```

`fsmes shape plant` writes a plant pack and starts nothing — so they can read
the pack, and the list it prints, before a plant exists. It prints every field
the shape carried that this plant does not use, and every thing it had to
state because the shape could not say it. Read that list to them; it is the
honest part.

`fsmes demo from-shape` does the same and starts it. `--buffer` is pieces of
space between each pair of machines: the one number no MES records, so it is
the one number they are asked. It will ask if you leave it out.

## What the output means

**The mapping is wrong before it is right.** `profile.py --propose-mapping`
guesses from column names alone and gets some wrong. Read the draft back to
them as questions — "is this your machine list? is this where tag values
live? where do you record a stop and its reason?" — and correct it with their
answers. A wrong mapping leaks nothing; it describes a plant that is not
theirs, which they will spot the moment `explain.py` reads it out. If a
section cannot be mapped, **leave it out**: the file will say that section was
not measured and why, which is the truth.

**The leak check refused and no file was written.** That is the check working,
not a failure. Something identifying from their database appeared in the
output. Find it, change the mapping or drop the section, and run again. Do not
work around it.

**`explain.py` describes somebody else's plant.** Three things they notice
first: the number of machines, the scrap percentages, the shift pattern. If
any is wrong, the *mapping* is wrong. Fix the mapping and profile again —
never edit `plant-shape.toml` by hand to make it look right.

**A query is slow, or they look worried about load.** Stop. Lower
`--row-cap`, or come back out of hours. Step 5 touches the big history table;
`SKILL.md` says what to look for in the plan. A nervous plant engineer who is
made to wait becomes a plant engineer who stops.

**Nothing found in the ERP step.** Plenty of plants integrate by nightly file
or through middleware that logs somewhere else. Nothing found is reported as
nothing found. It is never reported as none.

## The thing worth more than the plant

`plant-shape.toml` ends with a list of **things their MES tracks that
FactorySemantics does not model yet** — tool changes and their cost, energy,
whatever they have. That list is a feature request written by their own plant.
Ask them to send it. It is the most useful thing they can give the project,
and it carries no data.

## Done check

Ask for the last lines of `fsmes demo from-shape`. You are looking for the
station and routing counts, the flow line, and a dashboard URL — and then for
them to confirm the machine count and the shift pattern on the screen are
theirs. If the counts on screen are not their plant, go back to the mapping.

Then `3-shadow-mode.md`.

*The long version of this stage is `plant-from-your-mes/SKILL.md` in this
folder, and `docs/operate/plant-from-your-mes.md` in the project's repository.*
