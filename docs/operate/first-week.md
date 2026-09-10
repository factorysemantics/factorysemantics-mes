# What to expect in the first week

*Explanation. The MES is now watching a real line beside the system already
in charge there. Here is what it will get wrong, what that means, and what
is worth comparing.*

Nothing on this page is a promise. It is the list of things that have
surprised people on the first day of a shadow run, written down so they
surprise nobody again.

## Day one: three things that look wrong and are correct

**OEE says *unknown*, not a number.** The MES refuses to compute a KPI from
history it does not have. It was not subscribed before you started it, so
the first window is short and mostly unknown. Numbers appear as observation
accumulates — a shift, then a day, then a week. See
[Reading OEE](../plant/reading-oee.md).

**The downtime pareto is close to 100 % unlabelled.** Nothing on an
OPC-fed line labels a stop. The MES names that bar *unlabelled* rather than
filing it under "other", because a pareto that hides its own ignorance
convinces a plant it has data it does not have. Reason codes are an
operator workflow somebody adds later; they are not a missing feature you
are waiting on.

**A machine with no scrap counter shows no quality figure.** Blank in the
worksheet means unknown on the screen. Never zero.

## Week one: the four mistakes that are actually yours to fix

These are the ones worth chasing, in the order they usually appear.

### 1. Tag-map mistakes

The commonest first-week finding is a tag that was mapped to the wrong
meaning, not a tag that was unreadable —
[`fsmes opc-verify`](opc-readonly.md) catches unreadable on
day one. What it cannot catch is a *plausible* wrong answer: the good
counter pointed at the infeed rather than the outfeed, an analog that is
the setpoint and not the measurement, a state tag that belongs to the
neighbouring machine.

The symptom is a number that is steadily wrong in one direction. Fix it in
the worksheet, regenerate the tag map, restart the agent. Never in code.

### 2. Planned stops mislabelled as downtime

If a changeover, a planned clean or a break maps to `down`, every
availability figure after it is wrong, and the shape of the pareto sends
people to the wrong machine. This is the single most damaging mapping
mistake, and it is invisible unless somebody who knows the line looks at a
day of state history and says "that stop was a changeover".

Do that once, in week one, with the person who runs the line. It is an
hour, and it decides what every availability number you ever report means.
The mapping table is in the
[Engineering guide](../onboarding/GUIDE-ENGINEERING.md).

### 3. Counter resets

Counters reset — at shift change, at power cycle, at order change, at a
PLC download. The MES books production from counter *increases* and
re-baselines on a reset rather than booking a negative or a huge jump, so a
reset does not corrupt the count. What it does do is make *performance*
read unknown for the window it happened in, which looks like a gap.

Ask Engineering when each counter resets and write it in the worksheet's
`notes` column. It will not change the software's behaviour; it will change
how quickly somebody recognises the gap.

### 4. The agent not running

The window the agent was not watching cannot be reconstructed afterwards.
That is deliberate — see
[Never invent production](../plant/never-invent-production.md) — and it
means an agent that died at 02:00 and was restarted at 08:00 leaves six
hours that will read *unknown* forever.

Run it under a supervisor from the first day
([deploy](deploy.md) has the systemd unit), and check on the first morning
that it is still the process you started.

## What to compare against the system already in charge

Compare the things both systems measure the same way:

| Worth comparing | Why |
|---|---|
| Good count per order, per shift | Both count the same physical parts. A steady difference is a tag-map error; a difference that appears only at order boundaries is a booking-rule difference, which is the interesting one. |
| The *shape* of the day — when the line ran and when it stopped | Two systems watching the same tags should agree about when the machine was moving, even if they label the stops differently. |
| The longest stops | Both will have them. Whether they agree on *which machine* stopped first is a real test of the tag map. |

## What is not worth comparing, yet

| Not yet | Why |
|---|---|
| OEE, as a single number | Two systems almost never define availability over the same calendar. Until you have agreed what counts as planned time, comparing the number compares the definitions, not the plants. |
| The downtime pareto | The incumbent has reason codes entered by people. This MES has none in week one. The comparison is between labelled and unlabelled, which tells you nothing about either system. |
| Anything in the first two hours | Too short a window for any of the KPIs to leave *unknown*. |
| Scrap, unless every machine has a scrap counter | An unknown compared against a number is not a comparison. |

## The honest summary to give whoever asked for this

After a week you can say, with evidence: whether the tag map is right,
whether this MES sees the same line the incumbent sees, and how far apart
the two counts are. You cannot yet say whether its OEE is better, because
in week one it is mostly *unknown* and unknown is the correct answer.

## See also

- [Running beside your existing MES](first-plant.md) — the guide this page is the last step of
- [Reading OEE](../plant/reading-oee.md)
- [Never invent production](../plant/never-invent-production.md)
