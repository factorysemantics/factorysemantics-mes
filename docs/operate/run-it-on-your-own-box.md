# Run it on your own box

One PC, one plant, nobody to ask. This is the page for the first hour on a
machine of your own: what to type on Windows, how to tell whether the plant
is still running, and what to do when half of it has died.

!!! note "Which version this describes"

    Four fixes named here — `stop` recovering a plant that is alive but
    silent, `status` naming the process that died and its log, a start that
    checks the ports first, and `py -m fsmes` — first ship in **0.3.1, not
    yet released**. Everything else is true of 0.3.0 too, and where a fix is
    what you need, the old way out is written beside it.

## 1. Install it on Windows

```powershell
pipx install factorysemantics-mes
fsmes demo bottling
```

`pipx` is first because it puts `fsmes` on your PATH itself. If you do not
have it: `py -m pip install --user pipx`, `py -m pipx ensurepath`, then a new
PowerShell window.

**If you installed with `py -m pip install` and `fsmes` is "not recognized as
an internal or external command":** it is installed. The command is just not
on PATH — on Windows, `pip` puts `fsmes.exe` in a *Scripts* folder that a
default Python install does not add. Two ways out.

**Put the folder on PATH.** `pip` printed where it is; scroll back to:

```text
WARNING: The script fsmes.exe is installed in
'C:\Users\you\AppData\Roaming\Python\Python312\Scripts' which is not on PATH.
```

If that line has scrolled away, `py -m pip show -f factorysemantics-mes`
prints the location again. Then: Windows search → *Edit environment variables
for your account* → `Path` → **Edit** → **New** → paste the folder → OK →
open a new PowerShell, and `fsmes --version` answers. For one window only,
changing nothing permanently: `$env:PATH += ";C:\...\Scripts"`.

**Or do not touch PATH.** `py -m fsmes demo bottling` works the same. Every
command on this page can be typed that way: `py -m fsmes` wherever it says
`fsmes`.

## 2. Is it still running?

```powershell
fsmes demo bottling status
```

A healthy plant prints its address. Open it:
`http://127.0.0.1:8010/dashboard`.

**Nothing restarts it.** This is a demo you started, not a Windows service.
All of these take it down and none of them brings it back:

- the PC sleeps, restarts or shuts down;
- antivirus stops one of its background processes (possible; nobody has
  confirmed one doing it here);
- you typed `fsmes demo bottling stop`.

Closing the PowerShell window used to take it down as well; that was fixed on
2026-10-12. To bring the plant back, type `fsmes demo bottling` again — same
database, same plant, and up in seconds, because the pack copy and the
generated shift are already on disk.

## 3. When status says alive but silent

Sometimes one process dies and the others keep running. `status` says so:

```text
bottling is not answering on http://127.0.0.1:8010, and 4 of its 5 processes are still alive:
    still alive: the OPC server the machines answer on (pid 7964)
    still alive: the reader that writes the machines into the MES (pid 3940)
    still alive: the simulated floor (pid 5128)
    still alive: the log sink (pid 2216)
    gone: the dashboard and the API (pid 8822) - its log is C:\Users\you\AppData\Local\fsmes\logs\bottling\api.jsonl
that is a plant that is running and cannot be talked to. This is the way out:
    fsmes demo bottling stop      (stops what is left and clears the record)
    fsmes demo bottling           (starts it again)
```

A simulated plant is five processes. Here the one everything talks to is gone
and the rest still hold their ports, so nothing answers — and nothing can be
started over the top of it. `stop` ends the survivors and clears the record;
it needs no flag and says what it found:

```text
bottling was not answering; 4 of its 5 processes were still alive (7964, 3940, 5128, 2216); stopping them.
`fsmes demo bottling` starts it again.
```

The log `status` named is the only place the reason is. Read the last lines
of it; if you want someone else to look, that file is the thing to send.

**On 0.3.0**, `stop` refuses this state and has no flag of its own: stop the
pids it named by hand (`taskkill /F /PID 7964`) and then `fsmes demo bottling`.

**Starting on top of a silent plant** no longer launches five processes that
die one by one. If the dashboard port or the OPC port is taken, the start says
which port it is, what is on it — this installation's own plant, or another
program — and what to type.

## 4. What is on this box, and what leaves it

With `bottling` up:

| what | where |
|---|---|
| the dashboard and the API | `http://127.0.0.1:8010` |
| the OPC UA server its machines answer on | `opc.tcp://127.0.0.1:4841` |
| everything it writes — pack copy, database, logs, generated shift | `%LOCALAPPDATA%\fsmes` |

`fsmes demo machining` is a second plant beside it, on 8020 and 4842. Nothing
under the installed package is written to, so uninstalling leaves your data
and deleting that one directory removes it.

Nothing goes off the box except two things, and both are off until you turn
them on:

- **the chat on the AI tab** needs `ANTHROPIC_API_KEY` and costs about
  **$0.12 a question**. Without a key the tab says so, and every other screen
  is unaffected. See [the AI screen and what it records](ai.md).
- **the floor assistant** needs a local [Ollama](https://ollama.com). No key,
  nothing over the internet, and simply off if Ollama is not running.

## 5. A plant shaped like yours: four things people trip on

[Your plant in it without sending us your data](plant-from-your-mes.md) turns
a description of your own line into a running plant, with no data leaving the
building. Four things to know before you start:

1. **The MES box needs a `python` 3.11 or newer**, and nothing else — no
   fsmes, no driver, no internet. If you cannot run Python there, the same
   scripts emit the exact SQL to paste into SSMS, Azure Data Studio or
   `sqlcmd`, and you carry the answers back.
2. **The mapping file is a proposal, not an answer.** `mapping.toml` is the
   scripts' guess at which of your tables mean what. Read it back to somebody
   who knows that schema and correct it before the next command; a wrong
   mapping describes a plant that is not yours.
3. **The SQL Server queries have never run on a real SQL Server.** There is
   none here and none on CI. They are written against the documented syntax
   and checked for shape only. Expect to fix something, and please say what.
4. **Your plant comes back named `ST_01`, `Area 1`.** That is on purpose: the
   shape file carries counts, rates and stop mixes, and not one name from your
   database. Rename things afterwards if you want them named.

For a plant you mean to keep, [beside your existing MES](first-plant.md) is
the long version, and [backup and restore](backup.md) says what to copy.
