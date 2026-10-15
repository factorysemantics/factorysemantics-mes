# One file to paste when something is wrong

```bash
fsmes doctor
```

It writes `doctor.md` in the directory you are standing in and prints the
line it will be judged by:

```
wrote /home/you/doctor.md  (169 lines, 1 plant)
leak check: passed: 0 of 192 identifying values appear in this file (283 values sampled from the source)
Read it before you paste it anywhere. Nothing in this product sends it.
```

That file is the whole answer to "what is wrong with my plant". Paste it into
your own agent — Claude, ChatGPT, a local model — or into an issue on the
project, or into a chat with us. **Nothing in this product sends it
anywhere**, and there is no button that does. Where it goes is your decision,
made after you have read it.

On Windows, and when `fsmes` is not on PATH:

```powershell
py -m fsmes doctor
py -m fsmes doctor --plant bottling --out C:\Users\you\Desktop\doctor.md
```

It starts nothing and stops nothing. It takes about a second.

## What it writes

Two halves, one heading each, so a person can skim it and an agent can find a
section by name.

**Issues** — `## This installation` (version, OS, Python, whether it came
from pipx or pip, the home folder), then `## Plants in this home` and, per
plant, `### Processes` (each one alive or **gone**, by what it is for, with
the log that will say why it went), `### Ports` (and whether they answer),
`### Shadow mode`, and `### Log — …`: the **last 40 lines of every process
log, with the values taken out**.

**Performance, toward migration** — only for a plant in shadow mode: how long
it has been watching, `### Coverage over that time`, `### Scorecard over
time` (one row per kept scorecard — see
[the shadow scorecard](shadow-scorecard.md)), `### Differences still open`,
and a fixed section on **what is not built yet**, which is the same text the
packaged assistant prints. If shadow mode is off, that half says so in one
line and stops rather than padding.

A home with no plant in it still gets the first half, and says there is no
plant. A plant that is half-dead names the process that is gone and its log,
the same words [`fsmes status`](run-it-on-your-own-box.md#2-is-it-still-running)
uses.

## What it never writes

Your tag names, order numbers, product names, lot numbers, machine names,
people, this PC's name, your user name, database URLs, the OPC endpoint
beyond its scheme and port. A log line keeps its keys, its level and the
sentence our code wrote; every value in it becomes a shape:

```
{"timestamp": "2026-10-14T06:30:01.000000Z", "level": "error", "logger": "opc.agent",
 "event": "could not read", "tag": "AAAAAA_AAA_AAA", "order": "AA-AAAA-#",
 "exception": [{"exc_type": "TimeoutError", "exc_value": "no answer for AAAAAA_AAA_AAA",
 "frames": [{"filename": "site-packages/fsmes/integrations/opc/agent.py",
 "name": "read_batch", "lineno": 412}], "locals_dropped": 2}]}
```

`AAAAAA_AAA_AAA` was a tag and `AA-AAAA-#` an order: enough for an agent to
say *the OPC read timed out in the agent's read loop*, and nothing about
whose plant it was. The notation is the one the
[shape file](plant-from-your-mes.md) uses, and so is the check: the finished
file is read back and refused if any value the command saw on the way through
is still in it. **If the check fails, no file is written** and the command
says which value stopped it.

Two things follow from that rule, and you should know both:

- **It over-scrubs.** Measurements go too — a fill weight of 502.3 becomes
  `<float>`. This file is for diagnosing an installation, not for reading
  data out of. Figures belong in
  [the shadow scorecard](shadow-scorecard.md).
- **It keeps our own words, and your plant's name.** If one of your tags is
  spelled exactly like a word this product writes — `temperature`,
  `running` — that word stays. Your plant's own name is printed as you typed
  it, because a file that calls it `aaaaaaaa` cannot be used to help you.
  Read the file; it is short, and that is the point of printing it rather
  than sending it.

It also cannot see what it was never told. It reads this installation's own
files — the home folder, the plant's record of its processes, its logs, the
coverage ledger, kept scorecards — and asks its ports whether they answer. It
does not read your MES, your historian, or anything a log did not already
say.

## Where to paste it

To your own agent, with the question you actually have:

> My plant stopped. What do I do? *(then the file)*

To us: open an issue on
[the project](https://github.com/factorysemantics/factorysemantics-mes/issues)
and paste it in. If you have run shadow mode beside the MES in charge, the
second half is the part we have never seen from a real plant and the part
that decides what gets built next — the three questions at the end of the
file are there for that.
