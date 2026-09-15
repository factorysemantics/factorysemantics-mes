# Plant

For the person who runs a line and wants to know what it did.

| Page | Type | Read it when |
|---|---|---|
| [Your first line](first-line.md) | tutorial | you have ten minutes and a laptop |
| [Beside your existing MES](../operate/first-plant.md) | tutorial | you have an afternoon, a real line, and an MES already running on it |
| [Connect to Kepware — IT](../onboarding/GUIDE-IT.md) | how-to | you need an endpoint, an account and a certificate from whoever owns the server |
| [Connect to Kepware — Engineering](../onboarding/GUIDE-ENGINEERING.md) | how-to | you need the tag addresses from whoever knows the machines |
| [Reading OEE](reading-oee.md) | how-to | the OEE screen shows a grey segment and you want to know why |
| [When a control chart notices something](quality-signals.md) | explanation | a quality hold appeared with a rule number on it, and you want to know what else the MES will do |
| [Shifts](shifts.md) | reference | you want a number for *your* shift, or a row says *not attributed* |
| [Never invent production](never-invent-production.md) | explanation | you want to know why this MES will say *unknown* to your face |

The posture on a real plant is **read-only observer**: the MES subscribes to
tags and computes what it sees. Nothing writes toward a PLC unless a plant
turns on the recommendation queue, and then a person approves each write.
