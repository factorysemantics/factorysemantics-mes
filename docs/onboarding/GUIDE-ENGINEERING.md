# The tag worksheet — for Engineering / Controls

*Hand this page to whoever knows the machines and the PLC tags. What comes
back is one CSV row per machine — that single file configures the whole MES.*

## What we are building

A monitoring layer that watches each machine's existing tags — nothing is
added to any PLC — and turns them into downtime history, OEE and trends. It is
**read-only**: it subscribes to the tags you list and writes to none.

For each machine on the line we need **four tag addresses and two facts**.
Blank is a legitimate answer everywhere except the state tag — the software
shows *unknown* rather than inventing a number.

## The worksheet columns

Fill one row per machine, **in process order** (upstream first — row order
becomes the routing). Template: `worksheet-template.csv`; a filled example:
`worksheet-example.csv`.

| column | what goes in it |
|---|---|
| `equipment` | Short code for the machine — `FIL01`, `CAP01`. No spaces. This becomes its name in the MES. |
| `name` | Human name — "Filler 1". Shown on every dashboard. |
| `node_state` | Full OPC node id of the tag that says what the machine is doing. |
| `node_good` | Node id of the **cumulative** good-parts counter. |
| `node_scrap` | Node id of the cumulative reject counter. Blank if the machine has none. |
| `node_analog` | Node id of **one** process value worth trending — a temperature, pressure, torque, speed. |
| `analog_name` | What that value is called — `FillTemp`, `CapTorque`. |
| `state_map` | What each raw state value means: `0=idle; 1=running; 4=down; 5=setup` |
| `cycle_seconds` | Rated seconds per unit at this machine. Blank if not known. |
| `notes` | Anything worth keeping. Not read by software. |

### Finding node ids

In Kepware a tag's id is `Channel.Device.TagName` — the tree you see in the
configuration client — carried in a node id like:

```
ns=2;s=Line1.Filler.ProdCount
```

`ns=2` is Kepware's default namespace index. The reliable way to get ids
exactly right is **OPC Quick Client** (Kepware: Tools → Launch OPC Quick
Client): click a tag and copy its Item ID. Watch for auto-generated tag names
that differ from the display name — e.g. tags generated from a file keep the
sanitised file name in them (`LD_csv_State`). We will verify every id against
the live server before trusting any of them, so a typo is caught, not fatal.

## The two answers that matter most

### 1. What do the state values mean — and which ones count as *down*?

This is the decision with real consequences, because it defines every
availability figure the system will ever report:

| the machine is... | map it to | why |
|---|---|---|
| producing | `running` | |
| stopped, no product to work on (starved) | `idle` | the machine is healthy — the *line* has a problem |
| stopped, downstream full (blocked) | `idle` | same |
| faulted / breakdown | `down` | the only thing that should count against availability |
| changeover / planned stop | `setup` | **not** downtime — mapping this to `down` silently wrecks OEE |

If a changeover gets mapped to `down`, every availability number reported
afterwards is wrong, and nobody will be able to say why. If a value ever shows
up that is not in your map, the system flags it loudly rather than guessing.

### 2. How do the counters behave?

We book production from counter *increases*, so we need cumulative counters,
and we need to know their habits:

- **Do they reset** — at shift change, at power cycle, at order change?
  (Resets are handled: the count re-baselines and nothing false is booked.
  We just should not be surprised by them.)
- **What do they count** — bottles, cases, trays? Per-machine differences are
  fine; say so in notes.
- **Is scrap counted at all?** Many machines only count good. Then leave
  `node_scrap` blank and that machine's quality reads unknown — which is the
  truth, and better than pretending zero scrap.

### The cycle time

`cycle_seconds` is the machine's rated time per unit — nameplate speed or the
standard from the process sheet. It is the denominator of OEE *performance*
("how fast did it run vs. how fast should it"). If nobody is sure, leave it
blank: performance will show *unknown* until the real number is filled in.
A guessed cycle time is worse than none, because it quietly turns performance
into fiction.

## What happens with the worksheet

1. `fsmes make-tag-map worksheet.csv` turns it into the MES configuration,
   flagging anything inconsistent.
2. `fsmes opc-verify` connects to the server and reads every listed tag twice —
   you can watch the same values in OPC Quick Client while it runs. Anything
   unreadable or unmappable is reported per tag, with the machine and id.
3. The line appears on the dashboards. From then on, changing which tags feed
   the MES means editing the worksheet and regenerating — never code.
