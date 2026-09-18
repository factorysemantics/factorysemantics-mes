# How much of the window did the MES see

*Explanation. Why every KPI here carries a second number, what that number
means, and how to read the ledger behind it.*

Every OEE figure has a denominator. Ask most MES products what theirs is and
the answer is *the window* — the shift, the last eight hours, the day. That is
only true if somebody watched the whole window, and on a real plant nobody
did. The agent was restarted. A switch rebooted. A certificate expired. An
engineer unplugged the wrong port. The window went on; the watching did not.

So this MES states two numbers, always, side by side:

| | says |
|---|---|
| **Availability** | run time ÷ the part of the window this MES actually watched |
| **Coverage** | how much of the window that was |

92 % availability over forty watched minutes of an eight-hour shift is not the
same claim as 92 % over the shift, and **coverage is the only thing on a
screen that tells those two apart.**

## The ledger

Coverage is not computed on its own. It falls out of a **ledger**: per machine,
per window, a list of intervals that tile the window *exactly* — every second
of it in one interval, no second in two — and each interval carries one
disposition.

| Disposition | Means |
|---|---|
| `observed_running` | the MES watched the machine and it was running |
| `observed_stopped_labelled` | the MES watched it stopped, and somebody said why |
| `observed_stopped_unlabelled` | the MES watched it stopped, and nobody has said why yet |
| `not_observed` | nobody was watching |

Availability is the first of those over the sum of the three `observed_` ones.
Coverage is that sum over the window. Nothing is inferred and nothing is
filled in: if the MES holds no record of a stretch of time, that stretch is
`not_observed` and stays there.

**An OEE window whose seconds do not add up is a bug, not a rounding
difference.** A test asserts the tiling on every path, and `fsmes oee explain`
refuses with a non-zero exit code if the intervals it just printed do not sum
to the window it printed them for.

### What is derived, and what only carries coverage

**Availability is derived from the ledger**: its numerator and denominator are
both sums of ledger intervals, so availability and coverage can never
disagree about the same window.

**Performance and quality keep their own arithmetic** — counted units against
a rated cycle time, and good against good-plus-scrap — and carry the same
window's coverage beside them rather than being rebuilt out of it. That is
stated rather than glossed: a low-coverage window's performance figure is
built on the run time the ledger measured and on counts booked across the
whole window, and the honest reading of it is "over the part of this window
anybody watched". Deriving their inputs from the ledger too is a change of
its own and has not been done.

Where a plant sets a floor, all four figures are withheld together. A window
too thin to state availability for is too thin to state OEE for.

## Where the unwatched time went

Every `not_observed` interval carries a cause.

| Cause | Means |
|---|---|
| `before_first_sample` | earlier than the first state or connection this MES ever recorded for this machine. Not a fault — a machine commissioned this morning has most of an eight-hour window in here — but it is not watched time either |
| `disconnected` | a disconnection this MES recorded: it could not see the machine. The sentence whatever noticed it wrote travels with the interval ([losing sight of a machine](opc-disconnections.md)) |
| `after_last_sample` | later than the last thing this MES recorded, with nothing left open. The machine stopped being reported on and nothing said why |
| `no_state_recorded` | inside the watching period, no disconnection recorded, and the state history has a hole anyway. An agent killed outright writes no disconnection on its way down, and this is what that looks like |

### Why not "the agent was down" and "the tag went stale"

Because this MES cannot tell those apart from what it has written down. A
disconnection interval holds a sentence somebody's code wrote — *the OPC
server did not answer: timed out* — and the endpoint that was being dialled.
Sorting that free text into a taxonomy would be the MES claiming to know which
of several things happened when all it holds is that the link was gone. So
`disconnected` is one cause and the recorded sentence is printed beside it,
unaltered, for a person to read.

The day a plant's OPC server publishes a heartbeat tag, per-machine staleness
becomes a *positive* signal rather than an inference (decision 0030 already
says so, under "to revisit") and this table gains a row with evidence behind
it. Not before.

## How precisely was the watched time measured

Separate question, and the ledger does not answer it — it states it. Each
observed interval carries `sample_interval_seconds`: the cadence this plant's
machine layer is configured to publish at (`MES_OPC_PUBLISH_MS`), together
with a sentence saying that is what the number is.

It matters because a machine that changes state every three seconds, watched
on a fifteen-second grid, loses run time to the grid — measured, on a real lab
run, in [decision 0026](../decisions/0026-counts-that-outrun-the-run-time.md).
Coverage asks *whether* time was observed; the sample interval is about *how
precisely*. They compose: a future change-driven capture makes the observed
intervals more precise and does not change the ledger's shape.

## The coverage floor

By default **nothing is withheld**. Every figure prints with its coverage
beside it and the plant decides what to make of it. There is no default floor,
because a number vanishing off a screen because of a threshold nobody chose is
its own kind of dishonesty.

A plant that would rather be told *unknown* than shown a figure measured over
eleven minutes of a shift says so in its pack:

```toml
[oee]
coverage_floor = 0.8
```

A share, greater than 0 and at most 1; `fsmes pack check` refuses anything
else, including `0` — no floor is the key being absent. With a floor set, a
window whose coverage is below it comes back as **unknown** — in the API, in
`/metrics`, and on the screens, which print the ledger's summary instead of
drawing a smaller bar. A shorter bar would read as a worse machine, and what
actually happened is that nobody watched it.

**What is withheld is the figure, never the evidence.** Run time, counts,
seconds by state and the whole ledger stay on the object.

The three lab packs (`bottling`, `machining`, `finewire`) set a floor, so the
behaviour is visible in the fleet. The cutlery demo deliberately does not, so
the no-floor path is the one the public demo runs.

## Reading the ledger

```console
$ fsmes oee explain MIX01 2h
MIX01 — Mixer 01
  the last 2h: 2026-09-18 00:30:43 to 2026-09-18 02:30:43 UTC (2h 00m 00s  (7,200.0 s))

      from        to    seconds  disposition / cause
  00:30:43  00:50:42     1199.5  before_first_sample
                                   rule: earlier than the first state or connection this MES ever recorded here
  00:50:42  01:20:42     1800.0  observed_running
                                   rule: a recorded state interval said running
  01:20:42  01:30:42      600.0  observed_stopped_labelled
                                   rule: a recorded state interval said not running, and carried a reason
                                   said: blade change
  01:30:42  01:50:42     1200.0  observed_running
                                   rule: a recorded state interval said running
  01:50:42  02:05:42      900.0  disconnected
                                   rule: a recorded disconnection: this MES could not see the machine (decision 0030)
                                   said: the OPC server did not answer: timed out
  02:05:42  02:25:42     1200.0  observed_running
                                   rule: a recorded state interval said running
  02:25:42  02:30:43      300.5  no_state_recorded
                                   rule: inside the watching period, no disconnection recorded, and no state interval either
  7 interval(s)
  ...
  observed                       1h 20m 00s  (4,800.0 s)
  window                         2h 00m 00s  (7,200.0 s)
  coverage                       66.7%
  availability (run ÷ observed)  87.5%
```

The window is a span (`8h`, `90m`) or a shift (`current`, `previous`,
`2026-09-17/NIGHT`). Every interval names the **rule** that produced it, on
purpose: a plant engineer who disagrees with the availability figure should be
able to see which interval they disagree about, and argue with the rule rather
than with the number.

## In `/metrics`

Availability and coverage are exported as a pair, per machine, over the last
eight hours:

```
mes_equipment_coverage{plant="kc1",equipment="MIX01"} 0.667
mes_equipment_availability{plant="kc1",equipment="MIX01"} 0.875
mes_equipment_not_observed_seconds{plant="kc1",equipment="MIX01",cause="disconnected"} 900.0
```

They ship together so a Grafana panel cannot show one without being able to
show the other.

**An availability this MES cannot state is absent, not zero** — too little
watched time, or a window below this plant's floor. Absent is what Prometheus
already means by unknown; a zero would put a machine nobody watched at the top
of a worst-performer board. `mes_coverage_floor` is exported when a floor is
set, so a panel can draw the bar the plant chose.

## What this costs you, and why it is worth it

It makes your numbers lower, and sometimes it makes them disappear. Put this
MES in [shadow mode](shadow-mode.md) beside the system already running the
plant and it will often report *less* availability than the incumbent — not
because it watched a worse plant, but because it is subtracting the minutes it
could not see and the incumbent is not.

That is the feature. A number you can trust has to be able to say when it
cannot.

## See also

- [Losing sight of a machine](opc-disconnections.md) — how a disconnection is
  recorded, and what it does to the state history.
- [Reading OEE](../plant/reading-oee.md) — what each segment means.
- [Never invent production](../plant/never-invent-production.md) — the house
  rule this page is an application of.
- Decision 0033 — *availability is a share of what was watched*.
