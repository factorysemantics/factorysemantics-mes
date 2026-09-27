"""What the assistant's request suite needs to be on a plant before it is asked.

A case in `tests/assist_suite/` is a sentence somebody typed at a machine, and
almost every sentence names something: an order, a material, a machine, a draft
waiting for a signature. Ask a model to "book 600 good on MIX01 against
WO-EVAL-1" on a plant that has neither and the right answer is *there is no
MIX01 here* - which is the assistant behaving well and the suite scoring it as
a failure. On 2026-09-26 nine of nineteen live failures were exactly that.

So a case declares what it needs (`requires`), and a run either

* **arranges** it - puts the fixture on the plant through the same tools the
  suite is about, so a fixture that stops working is a tool that stopped
  working; or
* reports the case **not arranged** - counted apart from pass and fail, never
  silently skipped, and never scored against the model.

## What is arranged and what never is

Everything here is written by the AGENT account, on behalf of the person the
run signed in as, through the product's own API. That account is an agent
deployment and holds an agent deployment's capabilities, so the line falls
where decision 0035 puts it:

* **Arranged**: a work order, the non-conformance a failed check opens, a
  corrective maintenance order, one setting written so the audit trail has a
  row in it, and four drafts - an instruction, a trigger, a downtime reason and
  a non-conformance severity - none of which changes anybody's screen until
  somebody signs.
* **Never arranged**: master data. Materials, equipment, routings, lots and
  specifications are the plant's own, and an agent deployment does not define
  them. A plant without `FG-COLA` and `MIX01` gets those cases reported *not
  arranged* rather than invented for it.

Codes are prefixed `EVAL-`/`eval_` wherever the code is ours to choose, so that
what a run leaves on a plant can be told apart from the plant's own work at a
glance. `docs/ai/ASSIST-EVAL.md` lists every row a live run leaves behind and
what a person does about it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: The setting a run writes so that "who changed this, and when?" has an
#: answer to find. Engineering's reporting window, because that is the one
#: Scott changed by hand on 2026-09-26 and then asked the assistant about.
SETTING = "engineering/default_report_hours"
SETTING_VALUE = "10.0"

#: The order, the machine and the material the suite's sentences name. The
#: first is ours and says so; the last two are the plant's own.
ORDER = "WO-EVAL-1"
MATERIAL = "FG-COLA"
CHARACTERISTIC = "brix"
MACHINE = "MIX01"

#: The drafts a run puts up for the cases that ask for a signature. Their codes
#: are ours to choose, so they carry the prefix: nobody should have to guess
#: whether `eval_changeover` is their plant's vocabulary or ours.
INSTRUCTION = "WI-EVAL-1"
TRIGGER = "TR-EVAL-1"
REASON = "eval_changeover"
SEVERITY = "eval_cosmetic"

#: Why a corrective order was raised. Also how a second run recognises the
#: first run's work, so the plant does not collect one of these per run.
MAINTENANCE_SUMMARY = "Infeed belt slipping (assist eval fixture)"


class Unarrangeable(Exception):
    """A fixture this run may not, or cannot, put on the plant."""


# ------------------------------------------------------------- reading a plant

@dataclass
class Plantview:
    """One plant, read once per question. Presence does not change under a run
    that only ever adds, so asking twice is a wasted request on somebody's
    machine rather than a second opinion."""

    plant: str
    seen: dict = field(default_factory=dict)

    def has(self, requirement: str) -> bool:
        kind, code = split(requirement)
        if requirement not in self.seen:
            self.seen[requirement] = KINDS[kind].here(self.plant, code)
        return self.seen[requirement]

    def forget(self, requirement: str) -> None:
        self.seen.pop(requirement, None)


def split(requirement: str) -> tuple[str, str]:
    """`"material:FG-COLA"` -> `("material", "FG-COLA")`, and the `no ` in
    `"no reason:changeover"` is the caller's to strip first."""
    kind, _, code = requirement.partition(":")
    return kind.strip(), code.strip()


def _tools():
    from fsmes import mcp_server

    return mcp_server


def _ok(payload: Any) -> dict:
    """A tool result, or the plant's own words about why there is not one."""
    if isinstance(payload, dict) and payload.get("error"):
        raise Unarrangeable(str(payload["error"]))
    return payload if isinstance(payload, dict) else {}


def _rows(payload: Any, key: str) -> list[dict]:
    """The list a read tool came back with - or the plant's own words about why
    there is not one. A plant that cannot be read is a reportable fact: it comes
    out as "not arranged, could not be read", never as "the thing is not there".
    """
    if isinstance(payload, dict) and payload.get("error"):
        raise Unarrangeable(str(payload["error"]))
    rows = (payload or {}).get(key) or []
    return [row for row in rows if isinstance(row, dict)]


def _found(payload: Any) -> bool:
    """Looked one up by code: there, not there, or the plant would not say.

    A 404 is an answer - this plant has not got it. Anything else (a refusal, a
    plant that is down) is not, and must not be reported as an absence.
    """
    error = payload.get("error") if isinstance(payload, dict) else None
    if not error:
        return True
    if str(error).startswith("404"):
        return False
    raise Unarrangeable(str(error))


# ------------------------------------------------------------------- the kinds

@dataclass(frozen=True)
class Kind:
    """One sort of thing a case can need, and whether a run may put it there."""

    name: str
    #: Is it on this plant already?
    here: Callable[[str, str], bool]
    #: Put it there, as AGENT on behalf of `actor`. None when a run may not.
    make: Callable[[str, str, str], None] | None = None
    #: When there is no `make`, the sentence that says why - printed beside
    #: every case it stops, because "not arranged" with no reason is no better
    #: than a silent skip.
    cannot: str = ""


_MASTER_DATA = ("master data is the plant's own: an agent deployment does not define "
                "materials, equipment, routings, lots or specifications (decision 0035), "
                "so a run declares this rather than inventing it")


def _has_material(plant: str, code: str) -> bool:
    return any(m.get("code") == code for m in _rows(_tools().materials(plant), "materials"))


def _walk(nodes: list) -> set[str]:
    codes: set[str] = set()
    for node in nodes or []:
        codes.add(str(node.get("code")))
        codes |= _walk(node.get("children") or [])
    return codes


def _has_machine(plant: str, code: str) -> bool:
    tree = _tools().equipment_tree(plant)
    if isinstance(tree, dict) and tree.get("error"):
        raise Unarrangeable(str(tree["error"]))
    return code in _walk(tree.get("roots") or [])


def _has_lot(plant: str, code: str) -> bool:
    return any(lot.get("code") == code for lot in _rows(_tools().lots(plant), "lots"))


def _has_routing(plant: str, code: str) -> bool:
    return any(r.get("code") == code
               for r in _rows(_tools().routings(plant), "routings"))


def _has_spec(plant: str, code: str) -> bool:
    """`spec:FG-COLA/brix` - a characteristic on a material, which is how a
    specification is named everywhere else in this product."""
    material, _, characteristic = code.partition("/")
    return any(s.get("material") == material and s.get("characteristic") == characteristic
               for s in _rows(_ok(_tools().quality(plant)).get("specs") or {}, "items"))


def _has_order(plant: str, code: str) -> bool:
    return _found(_tools().order_detail(plant, code=code))


def _make_order(plant: str, code: str, actor: str) -> None:
    _ok(_tools().create_order(plant, code=code, material=MATERIAL, quantity=1000,
                              release=True, dry_run=False, on_behalf_of=actor))


def _has_nonconformance(plant: str, code: str) -> bool:
    return _found(_tools().nonconformance(plant, code=code))


def _make_nonconformance(plant: str, code: str, actor: str) -> None:
    """Out of the specification on purpose: the MES raises the non-conformance
    itself, which is the only honest way to have one to close.

    The plant numbers it, not this run - `nc_code_prefix` and the plant's own
    counter decide. A plant that calls it something other than the code the case
    names leaves that case *not arranged*, with the code it did use said out
    loud, rather than being scored against a non-conformance nobody asked about.
    """
    tools = _tools()
    _ok(tools.record_check(plant, material=MATERIAL, characteristic=CHARACTERISTIC,
                           value=20.0, order=ORDER, dry_run=False, on_behalf_of=actor))


def _maintenance_on(plant: str) -> list[dict]:
    return _rows(_tools().maintenance_work(plant, machine=MACHINE, open_only=False,
                                          limit=50), "work")


def _has_maintenance(plant: str, code: str) -> bool:
    return any(w.get("code") == code for w in _maintenance_on(plant))


def _make_maintenance(plant: str, code: str, actor: str) -> None:
    """Raise corrective work on the mixer - unless a run already did.

    The plant numbers this one too, so a second run cannot recognise its own
    work by the code. It recognises it by the summary, which is why the summary
    says what it is.
    """
    if any(w.get("summary") == MAINTENANCE_SUMMARY for w in _maintenance_on(plant)):
        return
    _ok(_tools().raise_corrective_maintenance(
        plant, machine=MACHINE, summary=MAINTENANCE_SUMMARY, dry_run=False,
        on_behalf_of=actor))


def _has_setting(plant: str, code: str) -> bool:
    """`setting:engineering/default_report_hours` - a *change* in the trail, not
    a value. "Who changed this?" is only a question on a plant where somebody
    did."""
    _domain, _, key = code.partition("/")
    return bool(_rows(_tools().setting_changes(plant, key=key, limit=5), "changes"))


def _make_setting(plant: str, code: str, actor: str) -> None:
    domain, _, key = code.partition("/")
    _ok(_tools().write_plant_setting(plant, domain=domain, key=key, value=SETTING_VALUE,
                                     dry_run=False, on_behalf_of=actor))


def _has_instruction(plant: str, code: str) -> bool:
    return any(i.get("code") == code
               for i in _rows(_tools().instructions(plant), "instructions"))


def _make_instruction(plant: str, code: str, actor: str) -> None:
    _ok(_tools().draft_instruction(
        plant, code=code, title="Measuring brix",
        body="Use the refractometer at the sample port.",
        material=MATERIAL, characteristic=CHARACTERISTIC,
        dry_run=False, on_behalf_of=actor))


def _has_trigger(plant: str, code: str) -> bool:
    return any(t.get("code") == code
               for t in _rows(_tools().triggers(plant), "triggers"))


def _make_trigger(plant: str, code: str, actor: str) -> None:
    _ok(_tools().draft_trigger(
        plant, code=code, name="Mixer running hot", tag="temp", condition="above",
        threshold=85.0, machine=MACHINE, action="log_event",
        dry_run=False, on_behalf_of=actor))


def _has_reason(plant: str, code: str) -> bool:
    return any(r.get("code") == code
               for r in _rows(_tools().downtime_reasons(plant), "reasons"))


def _make_reason(plant: str, code: str, actor: str) -> None:
    _ok(_tools().draft_downtime_reason(
        plant, code=code, name="Changeover (assist eval fixture)",
        description="A planned product change. Drafted by a faithfulness run; "
                    "nothing labels a stop with it until somebody signs it.",
        dry_run=False, on_behalf_of=actor))


def _has_severity(plant: str, code: str) -> bool:
    return any(s.get("code") == code
               for s in _rows(_tools().nc_severities(plant), "severities"))


def _make_severity(plant: str, code: str, actor: str) -> None:
    _ok(_tools().draft_nc_severity(
        plant, code=code, name="Cosmetic (assist eval fixture)",
        description="Visible, not functional. Drafted by a faithfulness run; "
                    "nothing is graded with it until somebody signs it.",
        dry_run=False, on_behalf_of=actor))


KINDS: dict[str, Kind] = {k.name: k for k in (
    Kind("material", _has_material, cannot=_MASTER_DATA),
    Kind("machine", _has_machine, cannot=_MASTER_DATA),
    Kind("lot", _has_lot, cannot=_MASTER_DATA),
    Kind("routing", _has_routing, cannot=_MASTER_DATA),
    Kind("spec", _has_spec, cannot=_MASTER_DATA),
    Kind("order", _has_order, _make_order),
    Kind("nonconformance", _has_nonconformance, _make_nonconformance),
    Kind("maintenance", _has_maintenance, _make_maintenance),
    Kind("setting", _has_setting, _make_setting),
    Kind("instruction", _has_instruction, _make_instruction),
    Kind("trigger", _has_trigger, _make_trigger),
    Kind("reason", _has_reason, _make_reason),
    Kind("severity", _has_severity, _make_severity),
)}


#: The non-conformance and the corrective order are numbered by the plant, not
#: by this run. These are the codes the demo plant gives them, which is what the
#: suite's sentences say; a plant that numbers them differently reports those
#: cases not arranged, with the code it did use.
NONCONFORMANCE = "NC-00001"
MAINTENANCE = "CM-00001"

#: What a run puts on a plant, in the order it has to go on: the order before
#: the check that fails against it, the machine's work after it. Everything
#: here is arrangeable by an agent deployment; everything a case needs that is
#: *not* here is master data, and is declared rather than made.
ARRANGES = (
    f"setting:{SETTING}",
    f"order:{ORDER}",
    f"nonconformance:{NONCONFORMANCE}",
    f"maintenance:{MAINTENANCE}",
    f"instruction:{INSTRUCTION}",
    f"trigger:{TRIGGER}",
    f"reason:{REASON}",
    f"severity:{SEVERITY}",
)


# ------------------------------------------------------------------ arranging

def arrange(plant: str, *, on_behalf_of: str = "ADMIN",
            view: Plantview | None = None) -> dict:
    """Put the suite's fixtures on this plant, and say what that took.

    Idempotent by construction: every fixture is looked for before it is made,
    and the two whose codes the plant assigns (the non-conformance and the
    corrective order) recognise a previous run's work by what it says rather
    than by the number the plant gave it. A second run on an arranged plant
    creates nothing and fails nothing.

    Returns `{"made": [...], "already": [...], "refused": {requirement: why}}`.
    A refusal is the plant's own sentence, kept whole - a fixture the AGENT
    account may not create is a fact about the deployment, not a bug here.
    """
    view = view or Plantview(plant)
    made: list[str] = []
    already: list[str] = []
    refused: dict[str, str] = {}
    for requirement in ARRANGES:
        kind, code = split(requirement)
        recipe = KINDS[kind].make
        try:
            if view.has(requirement):
                already.append(requirement)
                continue
        except Unarrangeable as exc:
            refused[requirement] = f"the plant would not say whether it has this: {exc}"
            continue
        if recipe is None:                            # not reachable today; said anyway
            refused[requirement] = KINDS[kind].cannot
            continue
        try:
            recipe(plant, code, on_behalf_of)
        except Unarrangeable as exc:
            refused[requirement] = str(exc)
            continue
        except Exception as exc:          # reported, never raised: a run says what it could not do
            refused[requirement] = f"{type(exc).__name__}: {exc}"
            continue
        view.forget(requirement)
        try:
            landed = view.has(requirement)
        except Unarrangeable as exc:
            refused[requirement] = f"the write went through and the plant would not "\
                                   f"confirm it: {exc}"
            continue
        if landed:
            made.append(requirement)
        else:
            refused[requirement] = (
                "the write went through and the fixture is still not there. This plant "
                "numbers this one itself, so look for the code it did use.")
    return {"made": made, "already": already, "refused": refused}


def missing(cases, plant: str, *, view: Plantview | None = None) -> dict[str, tuple[str, ...]]:
    """Per case id, the requirements this plant does not meet.

    A `no ` requirement is met when the plant does *not* have the thing: "draft
    a downtime reason `changeover`" is not a question you can ask a plant whose
    vocabulary already holds one, and the honest report is that the case was
    never arranged rather than that the model got it wrong.
    """
    view = view or Plantview(plant)
    out: dict[str, tuple[str, ...]] = {}
    for case in cases:
        unmet = []
        for requirement in case.requires:
            wanted_absent = requirement.startswith("no ")
            bare = requirement[3:].strip() if wanted_absent else requirement
            try:
                there = view.has(bare)
            except Exception as exc:      # an unreadable plant is a reportable fact too
                unmet.append(f"{requirement} (could not be read: {exc})")
                continue
            if there is wanted_absent:
                unmet.append(requirement)
        if unmet:
            out[case.id] = tuple(unmet)
    return out


def why_not(requirement: str) -> str:
    """The sentence that goes beside a requirement this plant does not meet."""
    bare = requirement[3:].strip() if requirement.startswith("no ") else requirement
    kind, _ = split(bare)
    if requirement.startswith("no "):
        return "this plant already has it, and nothing here removes a plant's own records"
    listed = KINDS.get(kind)
    if listed is None:
        return f"no fixture of kind {kind!r} is known"
    return listed.cannot or "a run arranges this, and on this plant it could not be made"


def unknown_kinds(cases) -> list[str]:
    """Requirements naming a kind no fixture knows. Called by the suite's own
    test, so a typo in a `requires` line is a red build rather than a case that
    quietly never runs."""
    bad: list[str] = []
    for case in cases:
        for requirement in case.requires:
            bare = requirement[3:].strip() if requirement.startswith("no ") else requirement
            kind, code = split(bare)
            if kind not in KINDS or not code:
                bad.append(f"{case.id}: {requirement}")
    return sorted(bad)
