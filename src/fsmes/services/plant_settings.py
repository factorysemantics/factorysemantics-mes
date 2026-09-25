"""The settings a plant owns: read them, edit them, seed them from the pack.

This is the mechanism decision 0035 §3 described in two sentences — **tier one
is seeded by the pack and owned by the database** — and that `shifts` had
implemented for years while every `[quality]` number stayed a compiled
environment variable. It is written here, once, generically, so that the next
plant-scope section to want an editable setting adds a row to this table and a
flag to its `ConfigSection` and nothing else. What a future section has to do
is set out in `docs/design/config-assistance.md` §11.

## The three layers

A setting is read in this order, and the order is the whole design:

1. **The row in `plant_settings`** — this plant's own answer, written by the
   pack when it was built or by somebody holding the section's `define`
   capability since.
2. **The compiled pack setting** — the `MES_*` environment variable
   `fsmes pack apply` wrote, which is where these values lived until this
   module existed.
3. **The literal the product ships** — the number that was in the source
   before the configuration audit of 2026-09-21 named it.

So *a plant with no rows in this table behaves exactly as it did before the
table existed*, which is the rule-one test this whole change has to pass, and
an upgrade needs to move no data to keep it true.

## Read through the caller's session, and cached on it

Every accessor takes the session its caller already has. No process-level
cache and no refresh interval: a setting a person saves is in force on the
next request, everywhere, because the next request reads the table. The one
thing that would make that expensive — a query per reading, and
`serial_digits()` is read once per serial issued — is avoided by memoising
one section's rows on `session.info`, which is how `fsmes.services.calendar`
already keeps shift patterns out of a hot loop. One query per section per unit
of work, and a write inside that unit of work clears its own memo.

## Why a `define` capability and no approval step

Rule three of 0035 is explicit: *"a number a plant administrator edits and
which takes effect when saved has no pending state."* These are not a
vocabulary. Nothing is written *in* them — a non-conformance stores its
severity, and no record anywhere stores "the Cpk bar that was in force when I
was judged" — so there is nothing for a revision history to protect and
nothing for an approver to sign. Undo is typing the old number back, and the
audit row says what it was.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes import modules
from fsmes.domain import PlantSetting
from fsmes.services import audit

#: Where one unit of work keeps the rows it has already read. The same trick,
#: and the same reason, as `calendar._PATTERN_CACHE`.
_MEMO = "fsmes.plant_settings"

#: What the pack seeder and the audit trail call a value nobody has edited.
PACK = "pack-apply"


class Invalid(Exception):
    """A proposed value is not one this key can hold, and the message is the
    sentence `fsmes pack check` would have printed for the same value in a
    file. One wording for one rule, whichever door the value came in by."""


class Unknown(Exception):
    """No live setting goes by that name on this version. Said out loud rather
    than ignored: a setting somebody believes they saved is worse than one they
    were told does not exist."""


# --------------------------------------------------------- which keys are live


def live_sections(domain: str | None = None) -> tuple[modules.ConfigSection, ...]:
    """Every `ConfigSection` whose pack keys are edited on its own
    Configuration page, across the registry or in one domain."""
    return tuple(section for module in modules.REGISTRY
                 for section in module.config_sections
                 if section.edit_here and (domain is None or section.domain == domain))


def owner(domain: str, key: str) -> tuple[modules.ConfigSection, str, object]:
    """Who owns one live setting in one domain: the `ConfigSection` it is
    listed under, the pack table it lives in, and the `fsmes.pack.format.Key`
    it is. `Unknown` when this version has no such setting.

    The key is returned unannotated because `fsmes.pack` is an edge and this
    is a service: the pack schema is imported inside each function that needs
    it, never at module scope, so that installing the product without the pack
    tooling still imports `fsmes.services` (the allowance and its reason are
    in `tests/test_core_purity.py`).

    The section is what carries the capability a write is gated on, so finding
    the owner and finding the gate are deliberately one lookup: a setting
    reachable without a section behind it would be a setting nobody owns.

    The pack table is returned rather than assumed to be the domain's own
    slug. A Configuration workspace is a place on a screen and a pack section
    is a table in a file; `[oee] coverage_floor` listed on a Process page is
    where the two come apart, and Quality's happening to share a name is a
    coincidence worth not building on.
    """
    from fsmes.pack import format as fmt

    for section in live_sections(domain):
        for written in section.pack_keys:
            where, name = split(written)
            if name != key:
                continue
            schema = fmt.key_named(where, name)
            if schema is None:  # pragma: no cover - the registry names a key this version has
                raise Unknown(f"this version has no pack key called {written!r}")
            return section, where, schema
    raise Unknown(
        f"nothing editable in the {domain!r} workspace is called {key!r}. "
        f"It has: {', '.join(sorted(editable_keys(domain))) or 'nothing'}.")


def editable_keys(domain: str) -> set[str]:
    """The key names one domain's Configuration page can write."""
    return {split(written)[1] for section in live_sections(domain)
            for written in section.pack_keys}


def split(written: str) -> tuple[str, str]:
    """`"[quality] spc_min_points"` as `("quality", "spc_min_points")`."""
    section, _, name = written.strip("[]").partition("] ")
    return section, name


# ------------------------------------------------------------------- reading


def rows(session: Session, section: str) -> dict[str, str]:
    """One pack section's settings as this plant owns them, `{key: text}`.

    Memoised on the session, so the fifty serials a pallet issues cost one
    query rather than fifty.
    """
    memo = session.info.setdefault(_MEMO, {})
    if section not in memo:
        memo[section] = {
            row.key: row.value for row in session.scalars(
                select(PlantSetting).where(PlantSetting.section == section))
        }
    return memo[section]


def forget(session: Session) -> None:
    """Drop what this unit of work remembers. Called by every write here, so
    a service reading a setting back after saving it reads what it saved."""
    session.info.pop(_MEMO, None)


def value(session: Session, section: str, key: str, fallback):
    """What this plant is running on for one setting, typed.

    The three layers, in order. `fallback` is the literal the product ships,
    passed in by the service that owns the number so that this module never
    holds a second opinion about what a default is.
    """
    from fsmes.pack import format as fmt

    schema = fmt.key_named(section, key)
    if schema is None:  # pragma: no cover - callers name keys this version has
        raise Unknown(f"this version has no pack key called [{section}] {key}")
    mine = rows(session, section).get(key)
    if mine is not None:
        try:
            return fmt.parse(schema, mine)
        except ValueError:  # pragma: no cover - the write path refuses these
            return fallback
    from fsmes.config import get_settings

    field = field_name(schema)
    written = getattr(get_settings(), field, None) if field else None
    if written is None:
        return fallback
    try:
        return fmt.parse(schema, str(written))
    except ValueError:
        return fallback


def field_name(schema) -> str:
    """The `Settings` field a pack key compiles to — `MES_QUALITY_SPC_MIN_POINTS`
    is `quality_spc_min_points`. Read off the pack schema rather than written
    out again, so the two cannot drift."""
    return (schema.becomes or "").removeprefix("MES_").lower()


def table(session: Session, section: str) -> dict:
    """One pack section as the plant is actually running it, typed, with every
    live key in it.

    The shape `fsmes.pack.check.quality_numbers` judges, which is what lets a
    single edited value be checked against the pair it belongs to: `cpk_marginal`
    is only wrong beside the `cpk_capable` this plant already has.
    """
    from fsmes.pack import format as fmt

    out: dict = {}
    for sec in live_sections():
        for written in sec.pack_keys:
            where, name = split(written)
            if where != section:
                continue
            schema = fmt.key_named(where, name)
            if schema is None:  # pragma: no cover
                continue
            out[name] = value(session, where, name, _shipped(schema))
    return out


def _shipped(schema):
    """The literal this version ships for one key, from the settings model."""
    from fsmes.config import Settings
    from fsmes.pack import format as fmt

    field = field_name(schema)
    declared = Settings.model_fields.get(field)
    if declared is None:  # pragma: no cover - every live key compiles to a setting
        return None
    try:
        return fmt.parse(schema, str(declared.default))
    except ValueError:  # pragma: no cover
        return declared.default


# ------------------------------------------------------------------- writing


def write(session: Session, *, domain: str, key: str, written: str, actor: str) -> dict:
    """Put one setting in force on this plant, now.

    Validated with the pack checker's own rules and refused with its own
    sentences; audited before and after; in force for the next reading rather
    than at the next restart. Returns the setting's state as the Configuration
    page reads it.
    """
    from fsmes.pack import check as checker
    from fsmes.pack import format as fmt

    _, section, schema = owner(domain, key)
    name = key
    try:
        proposed = fmt.parse(schema, written)
    except ValueError as exc:
        raise Invalid(f"[{section}] {name}: {exc}") from None

    judge = getattr(checker, f"{section}_numbers", None)
    if judge is not None:
        # Found by the section's own name rather than a registry, because the
        # checker is where a pack section's rules live and a second list of
        # which sections have rules would be a list that drifts - `[quality]`
        # has `quality_numbers`, `[erp]` has `erp_numbers`, `[process]` has
        # `process_numbers`, `[controls]` has `controls_numbers`, `[admin]`
        # has `admin_numbers`, and so on; a section with none is validated by
        # `fmt.parse` alone, which is the honest answer for a key whose only
        # wrong values are ones that are not the kind of thing at all.
        #
        # Judged against everything this edit *broke*, rather than against
        # everything wrong with the whole table. Two reasons, and the second
        # is a bug #100 shipped: a plant already out of range on some other
        # key would otherwise be unable to save anything at all, and a pair
        # rule reports at whichever of its two keys reads best, so lowering
        # `cpk_capable` under `cpk_marginal` was accepted while raising
        # `cpk_marginal` over `cpk_capable` was refused - the same
        # crossing-over, refused one way round and not the other.
        running = table(session, section)
        proposed_table = {**running, name: proposed}
        before = {str(problem) for problem in judge(running)}
        problems = [p for p in judge(proposed_table) if str(p) not in before]
        if problems:
            raise Invalid(str(problems[0]))

    text = fmt.as_written(schema, proposed)
    row = session.scalar(select(PlantSetting).where(
        PlantSetting.section == section, PlantSetting.key == name))
    before = None if row is None else {"value": row.value, "set_by": row.set_by}
    if row is None:
        row = PlantSetting(section=section, key=name)
        session.add(row)
    row.value = text
    row.set_by = actor
    from fsmes.db import utcnow

    row.set_at = utcnow()
    session.flush()
    forget(session)

    audit.record(session, actor=actor, action="plant_setting.set",
                 entity_type="plant_setting", entity_id=f"[{section}] {name}",
                 before=before, after={"value": text, "set_by": str(actor)})
    return state(session, domain, key)


# --------------------------------------------------------- what a screen reads


def state(session: Session, domain: str, key: str) -> dict:
    """One setting as the Configuration page prints it.

    Two values for *who set this*, not three, and it says only what it can
    know: **the product's default, unchanged**, or **this plant set it**. A row
    written by `fsmes pack apply` is the second of those — the plant did set
    it, in its pack — and `set_by` says which door it came in by for anybody
    reading the audit trail.
    """
    from fsmes.pack import format as fmt

    _, section, schema = owner(domain, key)
    now = value(session, section, key, _shipped(schema))
    shipped = _shipped(schema)
    row = session.scalar(select(PlantSetting).where(
        PlantSetting.section == section, PlantSetting.key == key))
    return {
        "key": f"[{section}] {key}",
        "name": key,
        "about": schema.about,
        "kind": schema.kind,
        # Everything reaches a screen as text, because that is what a setting
        # is by the time a plant reads one.
        "value": fmt.as_written(schema, now),
        "default": "" if shipped is None else fmt.as_written(schema, shipped),
        "is_default": now == shipped,
        # Whether this plant has taken ownership of the key, and who last
        # wrote it. `null` is *no row: the pack setting or the product's
        # default is what this plant reads*.
        "set_by": row.set_by if row else None,
    }


# --------------------------------------------------------- seeding from a pack


def seed(session: Session, pack) -> dict[str, int]:
    """Give this plant a row for every live setting its pack carries.

    `pack` is an `fsmes.pack.format.Pack`, unannotated for the reason `owner`
    above gives.

    **Idempotent, and it never updates** — the rule every other kind
    `fsmes pack apply` seeds already keeps (`fsmes.pack.masterdata`). A key
    with a row is left exactly as it is, whether the pack wrote that row last
    month or an administrator typed it this morning: a pack that reached back
    into a number somebody had deliberately changed on a running plant would
    be the pack overruling the plant, which is the opposite of what a pack is
    for. `fsmes pack status` reports the drift instead.

    Returns `{"made": n, "present": n}` for the receipt, in the shape the
    masterdata seeder answers in.
    """
    from fsmes.pack import format as fmt

    made = present = 0
    for section in live_sections():
        for written in section.pack_keys:
            where, name = split(written)
            schema = fmt.key_named(where, name)
            if schema is None:  # pragma: no cover
                continue
            if name not in pack.table(where):
                continue
            if session.scalar(select(PlantSetting.id).where(
                    PlantSetting.section == where, PlantSetting.key == name)):
                present += 1
                continue
            try:
                text = fmt.as_written(schema, pack.table(where)[name])
            except (TypeError, ValueError):  # pragma: no cover - the checker refused it first
                continue
            session.add(PlantSetting(section=where, key=name, value=text, set_by=PACK))
            made += 1
    forget(session)
    return {"made": made, "present": present}
