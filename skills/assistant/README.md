# FactorySemantics MES — the assistant folder

This folder is for an **assistant**, not for a program. Give it to whatever
AI assistant you already use — a chat window is enough, it needs no access to
this computer — and say:

> **Help me install FactorySemantics MES. Start with `SKILL.md`.**

It will walk you through four stages, one at a time:

1. **Install it**, on Windows or Linux, until a demo plant is running in your
   own browser.
2. **A plant shaped like yours**, built from the MES you already have, without
   copying any of your data out of it.
3. **Shadow mode**, running beside your live system, watching your real line
   and booking nothing, ending in a scorecard that compares the two.
4. **Migration** — an honest page on what you have at that point, and what is
   still not built.

## What the assistant will and will not do

- **It runs nothing.** You type the commands; you paste back what they
  printed; it reads that and says what to do next. If it offers to run things
  for you, it is not following this folder.
- **It will not ask you for data.** No order numbers, no customers, no tag
  names, no rows. There are exactly two files it ever asks to read: the
  `plant-shape.toml` you produce in stage 2, and the file `fsmes doctor`
  writes when something is wrong with the installation. Your own machine
  scrubs each one and will not write it at all if its leak check fails, you
  read it before you paste it, and the assistant tells you so before it
  asks.
- **Nothing leaves your building because of this folder.** Every command in it
  runs on your machine against your systems. After the install, none of it
  needs the internet.
- **It will tell you what is not built.** Nobody has yet run this MES's shadow
  mode on a real plant. There is no cutover procedure and no history
  migration. If you ask whether you can switch your old MES off, the honest
  answer today is no, and stage 4 says exactly what is missing.

## If you would rather read it yourself

Every page is plain markdown and is written to be read by a person too. Open
them in order: `0-how-to-help.md`, then `1-install.md`, `2-your-plant.md`,
`3-shadow-mode.md`, `4-migration.md`. `plant-from-your-mes/` is stage 2's own
folder, with the SQL it runs and the scripts that run it.

The project is pre-alpha and is at
`github.com/factorysemantics/factorysemantics-mes`. Please do not put real
plant data in an issue or a discussion there.
