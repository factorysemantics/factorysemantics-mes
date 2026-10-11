# How-to — follow a reading back to what caused it

The product went cold on the filler and nobody knows why. This page is the
walk from that reading to the answer, on the machine page's **Trend** tab,
using nothing but clicks.

There is no model in any of it. Every line is a record this MES already
held.

## Put both tags on one chart

Open the machine — **Machines → FILL01** — and pick **Trend**.

The heading has two pickers: *Trend of …* and *and …*. The first is the tag
you came for; the second is what you want to read it against. Both lists are
every tag the machine has ever published, not just the ones arriving now, so
a tag that stopped yesterday is still offered — which is usually the tag
somebody is looking for.

Pick `ProductTemp` and `ChillerOutletTemp` and you get two lines.

**Degrees and bar do not share a scale.** The unit a tag was configured with
decides which side of the chart it is drawn against: a temperature and a
pressure on one scale draws the pressure as a flat line along the bottom,
which is a picture of the units and not of the plant. The footer says which
side is which — *left axis: degC · right axis: bar*. Two tags whose unit the
plant never recorded share a side rather than being split on a guess, and the
footer says that is what happened.

A link the plant declared on a tag — an alarm limit — is drawn only when the
readings reach it. The axis is never stretched out to a limit nobody came
near, because that draws a one-degree ramp as a flat line; the footer says
when a limit is off the picture.

## Set the window to the shift that already finished

The picker at the top of the page is a *trailing* window: the last eight
hours, ending at this second. That is the right question for *how is it
going*, and the wrong one for *what happened at twenty past six*.

Under the Trend heading there is a second control, and it belongs to this
chart:

| Choose | You get |
|---|---|
| **This shift** | The shift the plant is in now, from its own shift pattern |
| **Last shift** | The one before it — a day shift read at four in the afternoon |
| **Last 15 minutes … 7 days** | The trailing windows, as before |
| **A start and an end** | Two boxes; type both and press *Apply* |

A window needs two ends. One end alone is refused — *"A window has two ends:
give a start and an end"* — and so is an end before its start. Nothing is
guessed from half an answer.

Under the chart is the window in words: **the shift's name, the two instants,
and the plant's own time zone**. It is worded from what the chart was *drawn*
on, not from what was asked for, so a window the MES narrowed — because it
was not watching that far back — is described as the stretch you are actually
looking at.

A wide window is honest about its own cost. The chart says how wide each
point is — *each point is one 30s bucket of readings — its mean, with the
highest and the lowest of the bucket behind it* — and draws that spread as a
band behind the line. An eight-hour window thinned to one point a minute
still shows a one-second excursion, because the excursion is in the band.

## Click a reading

Click any point on either line — or tab to one and press **Enter**.

The panel beside the chart fills with every record this MES holds about that
second. It was on the page before you clicked, so the chart does not move
under your hand, and the reading you opened wears a ring.

| The question | What it shows |
|---|---|
| **The reading** | The bucket's mean in its own unit, its highest and lowest, how many readings went into it, and when |
| **What the machine was doing** | Running, setup or down at that second, what it had just come out of, the reason somebody gave, and where that reason came from |
| **The job that was open** | The order and operation on the machine then, linking to the Orders screen |
| **Maintenance open on this machine** | Every maintenance order open on it *anywhere in the window*, each with its state and the reason it is sitting in that state — *assigned, never started: needs the line stopped* |
| **What the lab measured in this window** | Every quality sample taken in the window, with its characteristic, its value and its chart's verdict |
| **This machine's other tags at that instant** | Every other tag the machine was publishing, folded until you want it |

Every row links through to the screen that owns it. Each block says how much
of its own window anybody was watching — and the blocks that are lists of
records say they have **no** such figure rather than printing a share of
something they are not counting.

A block with nothing in it says so in words. An empty *Maintenance* block is
a machine with no open order in that window, and it says that; it is never a
blank box you could read either way.

## The walk, end to end

That is the whole of it, on the bottling line's planted story:

1. A fill-height sample comes up short on the Quality screen. It names the
   filler and the order.
2. **Machines → FILL01 → Trend.** `ProductTemp` and `ChillerOutletTemp`, last
   shift. The product temperature walks down from 8.4 to 6.4 degC, and the
   chiller outlet leaves 3.2 degC twenty minutes ahead of it.
3. **Click a reading inside the ramp.** The panel's maintenance block names
   the chiller condenser clean, *assigned and never started*, and says why —
   it needs the line stopped and there was no window this shift.

Three screens, and the third one answers it.

## What it will not tell you

- **Who named a stop.** The person who labelled a stop is in the audit log
  and not on the stop, so the panel says where the reason came from — *the
  MES recorded this, see the audit log* — rather than putting a name on it.
  Recording the labeller is a change to the data and a change of its own.
- **Anything about the machine upstream.** The panel answers *what was
  happening on this machine at this second*. A cause one station up the line
  is a different question, and this screen does not pretend to it.
- **Whether two tags are related.** Two lines twenty minutes apart is
  something a person reads; nothing here concludes it for them.
