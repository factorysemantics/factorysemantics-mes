# Stage 1 — Install it, until a plant answers in the browser

**Done with this stage when** the person pastes a line containing
`is up: http://127.0.0.1:8010` from `fsmes demo bottling status`, *and* says
the dashboard opened in their browser.

Windows first on this page, because that is what a plant engineer's PC is.
Linux is the same commands with different words and is at the bottom.

## What you need before you start

- Their operating system, and whether they have Python. `py --version` on
  Windows, `python3 --version` on Linux. Python 3.12 or newer.
- Nothing else. No database, no licence, no account, no internet after the
  download. The demo plant listens on loopback only.
- Tell them what this will do: it installs one Python package and, when they
  start the demo, runs five background processes on their own PC that simulate
  a bottling line and serve a dashboard. It is not a Windows service and
  nothing restarts it.

## The commands, in order, with what to paste back

### Windows (PowerShell)

```powershell
pipx install factorysemantics-mes
fsmes --version
fsmes demo bottling
```

Ask them to paste all of it. If `pipx` is not there:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

…then a **new** PowerShell window, and `pipx install factorysemantics-mes`
again. `pipx` is first on this page for one reason: it puts `fsmes` on PATH
itself, which `py -m pip install` does not.

### Linux or macOS

```bash
pipx install factorysemantics-mes
fsmes --version
fsmes demo bottling
```

`uv tool install factorysemantics-mes` instead of `pipx install` if they use
uv. Everything after that is identical.

### Then, whichever they are on

```
fsmes demo bottling status
```

and ask them to open `http://127.0.0.1:8010/dashboard` in a browser on that
same PC.

The start prints how to sign in — operator, admin and supervisor accounts out
of the demo pack, with their passwords in the open. Say that out loud: they
are lab passwords, the plant listens on loopback, and this is an evaluation
plant and not a plant anybody else can reach.

## What the output means

Four things actually go wrong. Each is identified by text the computer printed,
so ask for the paste before you diagnose.

### `not recognized` — it installed, and the shell cannot find it

```text
WARNING: The script fsmes.exe is installed in
'C:\Users\you\AppData\Roaming\Python\Python312\Scripts' which is not on PATH.
```

If they typed `fsmes` and PowerShell said it is **not recognized as an
internal or external command**, it *is* installed. `py -m pip install` writes
the program into a Scripts folder that is not on PATH and prints the warning
above, which has usually scrolled away. Three ways out, cheapest first:

1. `py -m fsmes demo bottling` — the same program, found through Python
   instead of through PATH. Every command on these pages can be typed that
   way: `py -m fsmes` wherever a page says `fsmes`.

   **On 0.3.0 this one does not work yet.** It prints `No module named
   fsmes.__main__; 'fsmes' is a package and cannot be directly executed`.
   That is not a broken install — it is the release not carrying the entry
   point, which 0.3.1 adds. Say so and go to way 2 or 3.
2. For this window only: `$env:PATH += ";C:\...\Scripts"` with the folder from
   the warning. If the warning has scrolled away,
   `py -m pip show -f factorysemantics-mes` names it.
3. Permanently: Windows search → *Edit environment variables for your
   account* → `Path` → **Edit** → **New** → paste the folder → OK → a new
   PowerShell. Then `fsmes --version` answers.

To settle *is it installed at all*, ask for
`py -m pip show factorysemantics-mes`. That answers on every release, with
PATH broken or not, and prints the version — which you want anyway.

### `still alive` — the plant is running and cannot be talked to

This is the one that confuses people most. One of the five processes died; the
others are holding the ports; nothing answers and nothing can start over it.
`fsmes demo bottling status` says so:

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

So, in order:

```
fsmes demo bottling status
fsmes demo bottling stop
fsmes demo bottling
```

`stop` ends the survivors, clears the record, and says what it found. It needs
no flag.

**On 0.3.0 it needs one and does not have one.** There, `stop` refuses this
state outright — see `did not answer` below — and the way out is to end the
process ids `status` named, by hand: `taskkill /F /PID 7964` for each, in
PowerShell (Task Manager's *Details* tab, *End task*, does the same thing by
clicking), then `fsmes demo bottling`. On Linux, `kill 7964`. Ask for
`fsmes --version` — or `py -m pip show factorysemantics-mes` if `fsmes` is
still not found — before you tell them which of the two they are on.

The log the `gone:` line names is worth reading: it says why that process
died. Ask them to paste its last few lines, not the whole file.

### `did not answer` — 0.3.0 refusing to act on a silent plant

```text
bottling did not answer (did not answer (...)). `stop` acts on a running plant and this one is
not saying anything; a plant is never managed through a gap in which nobody can see it. Its pid
file names 4 process(es) that are still alive (7964, 3940, 5128, 2216), so this is a plant that
is running and cannot be talked to.
```

That is 0.3.0. Take the `taskkill` route above. (On 0.3.1 and later, `stop`
handles it and this message does not appear for the demo.)

### Nothing happens, or it stopped by itself

Nothing restarts this plant. It goes down when the PC sleeps, restarts or
shuts down, or when they run `fsmes demo bottling stop`. Antivirus stopping a
background process is possible; nobody has confirmed one doing it here. The
fix is always the same: `fsmes demo bottling` again. It is the same database
and the same plant — nothing is lost.

Closing the PowerShell window used to take it down too; that was fixed on
2026-10-12, so on an older build, expect it.

## Where things are, when they ask

Everything this writes — its copy of the plant pack, the database, the logs,
the generated shift — is under one directory:

- Windows: `%LOCALAPPDATA%\fsmes`
- Linux: `~/.local/share/fsmes`
- macOS: `~/Library/Application Support/fsmes`

Nothing under the installed package is written to. Uninstalling leaves their
data; deleting that one directory removes it. `FSMES_HOME` moves the whole lot
somewhere else.

The demo plant is on port `8010` for its dashboard and `4841` for its
simulated OPC server. `fsmes demo machining` is a second plant beside it, on
`8020`.

## Done check

Ask for `fsmes demo bottling status`. You are looking for a line like:

```text
  bottling is up: http://127.0.0.1:8010/dashboard
```

and for them to confirm the dashboard drew in their browser. Both, not one.
Then `2-your-plant.md`.

*This page scripts `docs/operate/run-it-on-your-own-box.md` in the project's
repository, which is the long version and says which release each fix first
ships in.*
