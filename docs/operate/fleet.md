# The fleet: plants this installation owns

*How-to and explanation. Build, start, stop and re-pack a fleet of plants from one command — and understand exactly which plants that command will refuse to touch.*

```bash
fsmes fleet create labs/multiplant/machining   # build a plant from a pack, and own it
fsmes fleet start machining                    # start it
fsmes fleet status machining                   # what it says about itself
fsmes fleet stop machining
fsmes fleet apply machining                    # a new version of its pack, to a stopped plant
fsmes fleet list                               # everything owned or watched, with the totals
fsmes fleet plan                               # what a deployment script would act on
```

`fsmes fleet` manages **plants**, never **production**. It can stop a plant it owns; it cannot make one say it built something. No tag reaches a PLC through it, nothing goes to an ERP, no order is created or closed, no production is booked, and no master data, account or audit row is edited. Those stay each plant's own business, behind that plant's [shadow mode](shadow-mode.md), its capability roles and its approvals.

## What ownership means

A plant is **owned** by this installation when all three of these hold. Any one missing and the plant is **observed only**, and every verb above refuses it.

1. **This installation created it, and wrote that down.** `fsmes fleet create` appends an entry to `ownership.toml` in the fleet's data directory: the plant's name, its pack, where its data lives, when and on which host and OS user it was created, and a random **instance id**.
2. **The plant corroborates the id.** `fsmes fleet create` also writes that id into the plant's own data directory, and the plant returns it on `/health`. A plant answering with a *different* id, or with *no* id, is not owned — whatever the file says.
3. **A path to act exists.** Either **local** — the same host and the same OS user that created it, so this process can signal that plant's processes — or **remote**, where the entry names an environment variable holding a credential a person put there. A credential is never discovered, never defaulted and never reused from another entry, and only its *name* is ever written down.

Being able to reach a plant is not owning it. Knowing its name is not owning it. Having its password is not owning it.

### What ownership does not prove

**The instance id is a continuity check, not an authentication.** It proves the plant answering on that port is the plant this installation created and has not been swapped for a different one — the mistake case, which is the case that actually happens. It does not stop a person copying an id into the file on purpose. What keeps a stranger out of a plant is unchanged and is the plant's own business: its accounts, its [capability roles](security.md), its shadow mode.

There is no enrolment handshake here, no certificate authority and no bootstrap token. On the remote branch of condition 3 the whole weight rests on a credential a person typed.

### Giving ownership back

Delete the instance file from the plant's data directory (`<data_dir>/<name>.instance`). Nothing can prove condition 2 after that, so the plant becomes observed like any other — and it keeps running. Deleting the `[[owned]]` entry does the same from the other side.

Two plants claiming one instance id is an **error that refuses**, not a coin toss: a plant directory copied to make a second plant carries the first one's id until something regenerates it, and a tool that picked one of them would act on the wrong plant. `fsmes pack check` cannot catch this — a pack carries no instance id — so the fleet tool catches it when it reads the file.

### A plant that is silent

A plant that is not running is not contradicting anything, and a rule that treated silence as a contradiction would forbid the tool's own first two steps. So condition 2 is *the plant must not contradict us*, and it resolves three ways ([decision 0023](../decisions/0023-the-fleet-console-observes.md) has the same table):

| The plant is | What happens |
|---|---|
| answering | it must return this name and this instance id, or the verb is refused |
| silent, local | the instance id in its own data directory must still be there and still match; only `start` and `apply`, the two verbs a stopped plant can take, may proceed |
| silent, remote | refused — there is no data directory to read on another host, so nothing corroborates anything |

The thing the strict reading protects stays protected: **no verb reaches into a *running* plant that has not just said who it is.** On the console, a silent plant's ownership reads `unknown` rather than `yes`.

## The commands

### `fsmes fleet create <pack>`

Checks the pack and refuses on failure; makes the fleet's data directory; writes the plant its instance id; applies the pack — **the pack's settings, the schema to head, the master data the pack carries, and the accounts it declares** — adds the accounts a pack that declares none still needs, and records the ownership last. It **starts nothing** — the plant exists after this, and runs when a person says so.

That is the whole of `fsmes plant <name> init` and not a part of it. Until 2026-09-14 it was a part: `create` applied the pack and stopped, and it applied it to *this process's* database rather than to the plant's, because `fsmes pack apply` only named a database when the pack named one of its own. What it built was a stray file beside the working directory and an ownership entry for a plant that did not exist — which then started, answered `/health` with `ok`, was counted *answered* by the console, and returned **500 to every sign-in**. If that shape sounds familiar in your own fleet, `fsmes db-status --plant <name>` is the question that settles it.

It says which database it is about, on a line of its own, before it changes anything:

```
  machining: 13 settings from the pack
  machining: database sqlite:///.data/machining.db (the file this fleet gives machining, in .data)
      Database schema created at c8b1e40d7a92.
      equipment: 7 made, 0 already there
      ...
      account ADMIN (admin): created
  machining: schema at head, pack applied, accounts in place
```

Where that database comes from, in this order and never a fourth: the pack's own `[storage] database_url`; `MES_DATABASE_URL`, if you set it; otherwise `<data dir>/<plant>.db`, the file the fleet gives it — which is the same path the plant is started against. Never this process's default, because a database nobody named is not this plant's.

The order matters. The instance id is written *before* the pack is applied, so a plant that half-applies is still a plant this installation can prove it made; and the ownership entry is written *last*, because an entry for a plant that was never built is a claim about a plant that is not there. So a pack whose database cannot be reached, or whose schema the migrator will not identify, **refuses before anything is recorded as owned**.

A pack that carries no master data builds a plant with no machines on it. That is allowed, it is not a failure, and `fsmes fleet list` and the console now say so — see *answered, but empty* below.

`fsmes plant` reads the fleet file (the list of packs), not the ownership file, so `create` prints the line to add to it if you want the plant to show up there too.

### `fsmes fleet start|stop <name>`

The ownership gate, then the machinery that already runs plants (`fsmes plant <name> start|stop`). Start and stop are local process control: there is no remote start in this product and this decision adds none.

**`fsmes fleet stop <name> --force`.** `stop` acts on a running plant, so a plant that has stopped answering `/health` is refused: a plant is never managed through a gap in which nobody can see it. There is one state where that leaves a person stuck, and it is real — on 2026-09-14 a plant stopped answering `/health` while still answering `/pack`, still running, and the only way out was `kill`. The refusal now says whether the pid file still names live processes, and names them:

```
machining did not answer (did not answer (timed out)). `stop` acts on a running
plant and this one is not saying anything; a plant is never managed through a
gap in which nobody can see it. Its pid file names 2 process(es) that are still
alive (81422, 81423), so this is a plant that is running and cannot be talked
to. `--force` stops it: what this installation owns it may stop, and ownership
is the safety property here, not liveness.
```

`--force` gives up **liveness and nothing else**. Conditions 1 and 3 still hold in full, and condition 2 is still corroborated — by the instance id in the plant's own data directory, the half of it that is readable while the plant is silent, exactly as `start` and `apply` corroborate it. A plant this installation did not create is refused with the flag exactly as it is without it, and so is a plant that has taken its ownership back by deleting that id. It is the only verb in this product that takes the flag.

**Nothing starts a plant on its own.** There is no scheduler, no reconciliation loop, and no daemon that notices a stopped plant and brings it back. Desired state stays intent displayed as drift, never intent enforced.

### `fsmes fleet apply <name> [--pack <dir>]`

`fsmes pack check` first, refusing on failure; then the pack, to an owned plant that is **not answering** — applying a pack upgrades a database, and doing that underneath a live process is how a plant ends up half-upgraded. One plant's pack is never applied to another: a pack is a plant's identity.

### `fsmes fleet status <name>` and `fsmes fleet list`

Reads. `status` says whether the plant is owned and **why** — the same question every verb asks, answered before you hit it. `list` states its total: *"N plants, M answered, K answered but empty, L unknown; J owned"*. The number recorded is never the number seen.

Three states, and the distinctions are the point:

| State | What it means |
|---|---|
| `answered` | the plant answered, and has a schema and machines |
| `empty` | the plant answered, and **said** it has no schema or no machines |
| `unknown` | nobody heard from it. Never healthy, never down |

*Answered, but empty* arrived on 2026-09-14, because the state it names had been invisible: a plant with a schema at head, an account that signs in and no equipment at all looks healthy from every other angle, and the person it matters to is the one who has just built the fleet. It comes from what the plant said and from nothing else — a plant that does not answer `/pack`, or whose database did not answer, stays `answered`, because *unasked* is not *nothing there*.

```
2 plants, 1 answered, 1 answered but empty, 0 unknown; 2 owned.
  bottling       owned     answered  http://127.0.0.1:8010
  machining      owned     empty     http://127.0.0.1:8020   no line: this plant has no machines on it
```

The usual cause of `no line` is a pack that carries no master data — `fsmes pack apply` says *"master data: this pack carries none, so nothing was seeded"* when that is so. The usual cause of `no schema` is a plant whose database was never migrated; `fsmes db-status --plant <name>` says more.

### `fsmes fleet plan`

Reads. What a deployment script needs to know about every plant the **fleet file** lists — owned or not, since a promote moves a whole machine: where each pack is, where each database is and what kind it is, whether the plant simulates a line worth regenerating, and where to ask it whether it came back.

It states its total the same way `list` does: how many packs the fleet lists, and how many of those this product's deployment tooling could back up before migrating. Those two differing is the fact that matters — it means a plant nobody could roll back.

```
~/.config/fsmes/prod/fleet.toml: 2 plants, 2 this tooling can back up before migrating.
  bottling       http://127.0.0.1:9010      sqlite      backup: copy
  cutlery        http://127.0.0.1:9030      postgresql  backup: pg_dump
```

`--json` is the form [`deploy/promote.sh`](https://github.com/factorysemantics/factorysemantics-mes/blob/main/deploy/promote.sh) reads, so the promote does not parse `fleet.toml` a second time in bash. No password is printed unless `--with-password` asks for one, which is for a script piping it straight into `pg_dump`; the plain output is safe to paste into an issue.

## The console

```bash
fsmes fleet console                    # loopback; one page, at http://127.0.0.1:8090
```

One page. Every plant in the list — the packs this machine runs, the plants this installation created, and the `[[observe]]` entries a person added — each polled on `/health` and `/pack`, one row each:

| Column | Where it comes from |
|---|---|
| plant, label | the plant's own `/health`, not the address it was dialled at |
| owned | `yes`, `no`, or `unknown` while the plant is silent |
| answering | `answered`, `answered, empty`, or `unknown` |
| profile, clock | `/health` — and a defaulted zone says it was defaulted |
| shadow | `/health` |
| pack, drift | `/pack` — and *never applied* is not *no drift* |
| schema | `/pack`, against this build's head |
| line | `/pack` — how many machines this plant has, or *unknown* if it could not count them |
| modules | `/pack` — how many this plant serves, and which it does not |
| last answered | when this console last heard from it |

At the top: **"3 plants, 2 answered, 0 answered but empty, 1 unknown"**, and below it how many are owned, how many are recorded here but not answering, and how many are only watched. The number in the list is never the number seen.

**The port is 8090, and that is not arbitrary.** It was 8100 until 2026-09-14, which is the first port `fsmes score` and `fsmes sweep` hand an ephemeral plant — so a scored run started in the same minute as a console took the console's port, and what answered on it afterwards was a plant's sign-in page wearing the console's address. The console's port and the simulator's range are named constants now, and `tests/test_fleet_console.py` fails if they ever overlap again.

### What the page cannot do, and how to check that yourself

**There is no control on it, and no path from it to `fsmes fleet`.** Four things make that checkable by reading rather than by trusting this page:

1. `src/fsmes/fleet/console.py` declares two routes, both `GET`. No `app.post`, `app.put`, `app.patch` or `app.delete` appears in it.
2. It imports `observe` and `owned` and **never `commands`** — the verbs are not reachable from the process that serves the page.
3. `src/fsmes/web/fleet.js` makes exactly one kind of request: a `GET` of `/fleet.json`, its own server. The page has no form, no button and no input.
4. Everything it asks a plant is a `GET` of `/health` or `/pack`, which a plant answers without a credential — so **the console holds no credential at all**. A console is a long-running process on a port, and whatever it can do, whoever can reach that port can do; the safest credential is the one that does not exist.

`tests/test_fleet_console.py` holds all four by parsing the source, so they stay true.

The M8 design reached first for a *read-only machine role* for the console to run as. This console needs no account at all — every column above comes from an endpoint a plant answers to anyone who can reach it, and holding no credential is a stronger property than holding a read-only one. The role is not rejected, it is **not yet needed**: the day a console shows OEE, the loss breakdown or `/ops/services` is the day this product grows one, and nothing here assumes its absence.

### What it refuses to show

- **A plant that did not answer is `unknown`.** Never healthy, never down. It is also not owned while it is silent, because nothing can corroborate the instance id — so a plant is never managed through a gap in which nobody can see it.
- **Nothing is added up across plants.** No fleet OEE, no fleet availability, no single number of any kind. A fleet OEE is a lie unless every plant is the same shape, and no two are. Twelve plants are twelve rows.
- **It holds no plant data.** The last answer is cached for display and nothing else. Orders, serials, people and events stay in the plant that made them.
- **It starts nothing.** No scheduler, no reconciliation loop, no daemon that notices a stopped plant and brings it back.

A console `--manage` flag — off by default, loopback only, each button calling the same gated function the command calls — is possible later and is deliberately not here.

## `ownership.toml`

In the fleet's data directory (`[environment] data_dir` in the [fleet file](registry.md), or `labs/multiplant/.data` in a checkout).

```toml
[[owned]]
name = "machining"
pack = "/home/you/fsmes/labs/multiplant/machining"
instance_id = "4f6c…"
data_dir = "/home/you/fsmes/labs/multiplant/.data"
api_host = "127.0.0.1"
api_port = 8020
control = "local"
created_at = "2026-09-13T20:41:07+00:00"
created_on_host = "workshop"
created_by_user = "you"
product_version = "0.1.2"
pack_fingerprint = "9a1c…"

[[observe]]
name = "hall2"
url = "http://10.20.30.41:8050"
about = "someone else's plant; watched, never touched"
```

**It is not the fleet file.** `fleet.toml` lists the packs this machine runs; `ownership.toml` records the plants this installation created. Decision 0023 called the ownership file `fleet.toml` before [M8 piece 3](packs.md) gave that name to the registry, and two files of one name is a trap for whoever reads the next traceback.

No password is ever written here — `credential_env` names the variable one lives in. `[[observe]]` entries are plants this installation does **not** own: they exist so a reader can see them, and no verb will act on one.

## See also

- [Plant packs](packs.md) — what a pack may contain, and `fsmes pack check`
- [Plants from a fleet file](registry.md) — `fsmes plant`, the machinery underneath
- [Beside your existing MES](first-plant.md) — the engineers' front door
- [Decision 0023 — the console manages only the plants it owns](../decisions/0023-the-fleet-console-observes.md)
- [The M8 design](../design/m8-packs-and-fleet.md) §8, which this is built from
