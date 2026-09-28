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

* **Arranged**: a work order and its first step started, the non-conformance a
  failed check opens, a corrective maintenance order, one setting taken to ten
  and put back to eight so the audit trail has a row in it, and five drafts - an
  instruction, a trigger, a downtime reason, a non-conformance severity and a
  recommended setpoint change - none of which changes anybody's screen until somebody
  signs. Every one of them is about something no drafting case asks for, so
  that a case and the arrangement it runs on never describe the same thing.
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

#: The value the audit trail has to carry: the 10.0 he says he set, because the
#: case that asks about it quotes that number.
SETTING_VALUE = "10.0"

#: And what the key is left reading afterwards - the product's own default.
#:
#: Both halves, because one run has to serve two of his sentences and they pull
#: opposite ways. "I just changed it to 10.0 hrs" only means something on a
#: plant whose trail says somebody did; "I want to change the default reporting
#: window to 10hrs" only means something on a plant where ten is a change. Live
#: on 2026-09-27 the arrangement wrote 10.0 and stopped, and the model answered
#: the second one with *"already set to 10.0 hours - nothing to change"*, which
#: was right and was scored as a failure. So the fixture is the whole story:
#: somebody took it to ten, and it is back at eight.
SETTING_RESTS_AT = "8.0"

#: The order, the machine and the material the suite's sentences name. The
#: first is ours and says so; the last two are the plant's own.
ORDER = "WO-EVAL-1"
MATERIAL = "FG-COLA"
CHARACTERISTIC = "brix"
MACHINE = "MIX01"

#: The step of that order a run starts, so that booking production against it is
#: the direct answer to "produce 2 units of FG-COLA on MIX01 for WO-EVAL-1".
#:
#: Live on 2026-09-27 the model answered that sentence with
#: `start_operation(WO-EVAL-1, 10)`, and on a released order whose first step had
#: not begun that is a defensible first move - so the case had two right answers
#: and was scored against one of them. A run starts the step instead. The step
#: after it is deliberately left pending, which is what `operator-starts-a-step`
#: needs: a request to start a step only means something where a step is waiting.
STEP_STARTED = 10
STEP_WAITING = 20

#: The drafts a run puts up for the cases that ask for a signature. Their codes
#: are ours to choose, so they carry the prefix: nobody should have to guess
#: whether `eval_awaiting_parts` is their plant's vocabulary or ours.
#:
#: And each one is about something no drafting case asks for. The prefix kept the
#: *codes* apart, which is what the ratchet needed; it did not keep the *words*
#: apart, which is what a model reads. Live on 2026-09-27, asked to "draft a
#: cosmetic severity", the model answered *"there's already a draft 'cosmetic'
#: severity (code eval_cosmetic)"* - true, and a failure the suite had arranged
#: for itself. `eval_changeover` beside "draft a downtime reason changeover",
#: "Measuring brix" beside "draft a work instruction WI-BRIX called Measuring
#: brix", and a trigger on MIX01's temperature above 85 beside "draft a trigger
#: TR-HOT: if MIX01 temp goes above 85" were the same trap, three more times.
INSTRUCTION = "WI-EVAL-1"
INSTRUCTION_TITLE = "Logging a shift handover (assist eval fixture)"
TRIGGER = "TR-EVAL-1"
TRIGGER_NAME = "Feed pump pressure high (assist eval fixture)"
TRIGGER_TAG = "pressure"
TRIGGER_THRESHOLD = 6.5
REASON = "eval_awaiting_parts"
REASON_NAME = "Awaiting parts (assist eval fixture)"
SEVERITY = "eval_scuff"
SEVERITY_NAME = "Scuff (assist eval fixture)"

#: Every draft a run puts up, as code and the words a person would read. The
#: suite's own test walks this against every drafting case, so a fixture that
#: starts reading like the draft a case asks for is a red build.
DRAFTS = ((INSTRUCTION, INSTRUCTION_TITLE), (TRIGGER, TRIGGER_NAME),
          (REASON, REASON_NAME), (SEVERITY, SEVERITY_NAME))

#: Why a corrective order was raised. Also how a second run recognises the
#: first run's work, so the plant does not collect one of these per run.
MAINTENANCE_SUMMARY = "Infeed belt slipping (assist eval fixture)"

#: The two identified units the packing and disposition cases name. Ours to
#: choose, so they carry the prefix - a serial `SN-EVAL-1` in somebody's trace
#: is recognisably a run's and not a bottle that left the plant.
#:
#: There is no gauge here, and that is the rule rather than an omission: see
#: `_GAUGE` below.
UNIT = "SN-EVAL-1"
CONTAINER = "SN-EVAL-CASE-1"


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


def same_value(written: Any, wanted: Any) -> bool:
    """`"8"`, `"8.0"` and `8.0` are one value.

    The scorer's own comparison, borrowed rather than written again: a setting is
    text by the time a plant reads one, how it is written is the pack formatter's
    business, and two functions disagreeing about whether eight is eight is one
    of the ways a fixture quietly stops being made.
    """
    from fsmes.services.assist_eval import same_value as compare

    return compare(wanted, written)


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
    #: Why this may not, or could not, be put there - printed beside every case
    #: it stops, because "not arranged" with no reason is no better than a
    #: silent skip. Required where there is no `make`; also worth writing for a
    #: kind a run tries and a particular plant cannot carry, which is what
    #: `why_not` prints for it.
    cannot: str = ""
    #: What would make a plant able to carry it, in the words a result file
    #: prints. The other half of the same fact: a reader who is told only that a
    #: case was not arranged cannot tell a plant that is missing a line from a
    #: suite that is asking for something impossible. `how_to_arrange` prints it.
    fix: str = ""


_MASTER_DATA = ("master data is the plant's own: an agent deployment does not define "
                "materials, equipment, routings, lots or specifications (decision 0035), "
                "so a run declares this rather than inventing it")

_SEED_IT = ("put the code on the plant yourself, or run with `--seed-masterdata`, which "
            "puts the demo plant's master data there over the plant's own API as the "
            "account you signed in as - never as the agent")


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


def _has_operation(plant: str, code: str) -> bool:
    """`operation:WO-EVAL-1/10` - that step of that order, started.

    Started, not finished. What the requirement is about is whether starting the
    step is still an honest first move, and a step that is running and a step
    that is done both answer *somebody has started this*; only a pending one
    leaves the question open.

    An order this plant has not got, or a step that is not on its routing, is
    unarrangeable rather than absent: the case is reported *not arranged* with
    the plant's own words, which is the difference between "your routing has no
    step 10" and "step 10 has not been started".
    """
    order, _, seq = code.partition("/")
    detail = _tools().order_detail(plant, code=order.strip())
    if not isinstance(detail, dict) or detail.get("error"):
        raise Unarrangeable(str((detail or {}).get("error")
                                if isinstance(detail, dict) else detail))
    steps = (detail.get("order") or {}).get("operations") or []
    for step in steps:
        if str(step.get("seq")) == seq.strip():
            return str(step.get("status") or "").casefold() != "pending"
    raise Unarrangeable(f"{order} has no step {seq} - its route is "
                        f"{', '.join(str(s.get('seq')) for s in steps) or 'empty'}")


def _make_operation(plant: str, code: str, actor: str) -> None:
    """Start it, through the same tool the suite asks the model to reach for."""
    order, _, seq = code.partition("/")
    _ok(_tools().start_operation(plant, order=order.strip(), seq=int(seq),
                                 dry_run=False, on_behalf_of=actor))


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


def _value_now(plant: str, domain: str, key: str) -> Any:
    return (_ok(_tools().plant_settings(plant, domain=domain, key=key))
            .get("setting") or {}).get("value")


def _has_setting(plant: str, code: str) -> bool:
    """`setting:engineering/default_report_hours` - both halves of the story:
    the trail says somebody took it to 10.0, and it reads 8.0 now.

    A change in the trail is what makes "who changed this?" a question. The
    value is what makes "change it to ten" a change - and a plant sitting at ten
    is a plant where the honest answer is *nothing to change*, which is what the
    live run was scored down for on 2026-09-27.
    """
    domain, _, key = code.partition("/")
    changes = _rows(_tools().setting_changes(plant, key=key, limit=20), "changes")
    return (any(same_value(row.get("to"), SETTING_VALUE) for row in changes)
            and same_value(_value_now(plant, domain, key), SETTING_RESTS_AT))


def _make_setting(plant: str, code: str, actor: str) -> None:
    """Take it to ten, then put it back to eight.

    Two writes, and two audit rows: the first is the fixture the cases about the
    trail read, the second is what leaves the key changeable. A run that finds
    the ten already recorded writes it no second time, so pointing this at the
    same plant twice adds nothing.
    """
    domain, _, key = code.partition("/")
    changes = _rows(_tools().setting_changes(plant, key=key, limit=20), "changes")
    if not any(same_value(row.get("to"), SETTING_VALUE) for row in changes):
        _ok(_tools().write_plant_setting(plant, domain=domain, key=key, value=SETTING_VALUE,
                                        dry_run=False, on_behalf_of=actor))
    if not same_value(_value_now(plant, domain, key), SETTING_RESTS_AT):
        _ok(_tools().write_plant_setting(plant, domain=domain, key=key,
                                        value=SETTING_RESTS_AT, dry_run=False,
                                        on_behalf_of=actor))


def _has_instruction(plant: str, code: str) -> bool:
    return any(i.get("code") == code
               for i in _rows(_tools().instructions(plant), "instructions"))


def _make_instruction(plant: str, code: str, actor: str) -> None:
    _ok(_tools().draft_instruction(
        plant, code=code, title=INSTRUCTION_TITLE,
        body="Write down what the line is doing and what the next shift should watch.",
        material=MATERIAL, characteristic=CHARACTERISTIC,
        dry_run=False, on_behalf_of=actor))


def _has_trigger(plant: str, code: str) -> bool:
    return any(t.get("code") == code
               for t in _rows(_tools().triggers(plant), "triggers"))


def _make_trigger(plant: str, code: str, actor: str) -> None:
    _ok(_tools().draft_trigger(
        plant, code=code, name=TRIGGER_NAME, tag=TRIGGER_TAG, condition="above",
        threshold=TRIGGER_THRESHOLD, machine=MACHINE, action="log_event",
        dry_run=False, on_behalf_of=actor))


def _has_reason(plant: str, code: str) -> bool:
    return any(r.get("code") == code
               for r in _rows(_tools().downtime_reasons(plant), "reasons"))


def _make_reason(plant: str, code: str, actor: str) -> None:
    _ok(_tools().draft_downtime_reason(
        plant, code=code, name=REASON_NAME,
        description="The line is stopped waiting on a spare part. Drafted by a "
                    "faithfulness run; nothing labels a stop with it until somebody "
                    "signs it.",
        dry_run=False, on_behalf_of=actor))


def _has_severity(plant: str, code: str) -> bool:
    return any(s.get("code") == code
               for s in _rows(_tools().nc_severities(plant), "severities"))


def _make_severity(plant: str, code: str, actor: str) -> None:
    _ok(_tools().draft_nc_severity(
        plant, code=code, name=SEVERITY_NAME,
        description="A light surface mark on the packaging. Drafted by a faithfulness "
                    "run; nothing is graded with it until somebody signs it.",
        dry_run=False, on_behalf_of=actor))


#: Why a plant may not be able to carry a recommended setpoint change, in the
#: words a result file prints.
_NO_WRITABLE_SETPOINT = (
    "a recommended setpoint change names a tag this plant's own manifest declares "
    "writable with bounds - the first of the three guards between a recommendation "
    "and a PLC - and nothing here writes a plant's manifest, so a plant that declares "
    "none has this declared rather than invented for it")

#: And what a person would do about it. The first guard between a recommendation
#: and a PLC is a declaration, so the thing that is missing is a line in a file -
#: which is the fact a reader of a result file needs, because it says the plant is
#: short of a declaration rather than the suite short of sense.
_DECLARE_A_SETPOINT = (
    "one writable setpoint on this machine in the plant's tag manifest - a tag in "
    "`tags.json` beside its replay data, under `tables.<the machine's object>.tags`, "
    'reading `{"kind": "sp", "writable": true, "min": <low>, "max": <high>}`. A line '
    "`fsmes.sim.generate` made carries them already. Nothing else is needed: a run "
    "recommends the change itself once there is a tag to recommend it on")

#: Why a setpoint change was recommended, and how a second run recognises the
#: first run's work: the plant numbers a recommendation itself, like the
#: non-conformance and the corrective order.
ADJUSTMENT_RATIONALE = ("Brix has been drifting high while this setpoint sat where it is "
                        "(assist eval fixture)")


def _open_adjustments(plant: str, machine: str) -> list[dict]:
    """The recommendations waiting for somebody on this machine. Only `proposed`
    ones: an approved or written one has had its decision made, and "approve the
    setpoint change on MIX01" is not a question about one of those."""
    rows = _rows(_ok(_tools().adjustments(plant, status="proposed", limit=50)),
                 "adjustments")
    return [row for row in rows if row.get("equipment") == machine]


def _has_adjustment(plant: str, code: str) -> bool:
    """`adjustment:MIX01` - a setpoint change waiting for a decision on that
    machine. The code here is the machine, not the recommendation: the plant
    numbers recommendations itself, and what the case's sentence names is the
    machine."""
    return bool(_open_adjustments(plant, code))


def _make_adjustment(plant: str, code: str, actor: str) -> None:
    """Recommend a setpoint change on the machine, so that "approve the setpoint
    change on MIX01" is a question this plant can be asked.

    A recommendation is the agent's own half of the write-back story and a draft
    like the other four: an engineer decides it, the agent never does (decision
    0035). It needs a tag this plant's manifest declares writable, with bounds -
    three guards stand between a recommendation and a PLC, and the first of them
    is that manifest. A plant that declares no writable setpoint on this machine
    is told so, and the cases that name one are reported not arranged rather
    than scored against a model that correctly said nothing is waiting.
    """
    if _open_adjustments(plant, code):
        return
    writable = [row for row in _rows(_ok(_tools().browse_tags(
        plant, machine=code, writable_only=True)), "rows")
        if row.get("min") is not None and row.get("max") is not None]
    if not writable:
        raise Unarrangeable(f"{code} declares no writable setpoint with bounds: "
                            f"{_NO_WRITABLE_SETPOINT}")
    tag = writable[0]
    # Half way between the declared bounds: inside them whatever they are, and
    # not a value chosen here for a process nobody in this run has seen.
    value = (float(tag["min"]) + float(tag["max"])) / 2
    _ok(_tools().propose_adjustment(
        plant, machine=code, tag=tag["tag"], value=value,
        rationale=ADJUSTMENT_RATIONALE,
        evidence={"note": "arranged by fsmes assist eval so that the approval cases "
                          "have something to approve"},
        dry_run=False, on_behalf_of=actor))


_PERSON = ("who works at a plant is the plant's own record, like its materials and its "
           "machines. A run declares this rather than adding somebody - and it is only "
           "ever asked in the negative: \"add Jo Patel\" is a question you can put to a "
           "plant that has not got a Jo Patel")


def _has_person(plant: str, code: str) -> bool:
    return any(p.get("code") == code
               for p in _rows(_tools().people(plant, q=code), "people"))


_GAUGE = ("the gauge register is master data, and the AGENT account does not hold "
          "masterdata.write unless an admin has granted it (decision 0035) - so a run "
          "cannot put a gauge on a plant, and only asks in the negative: \"register "
          "GA-BRIX01\" is a question you can put to a plant that has not got one")


def _has_gauge(plant: str, code: str) -> bool:
    return any(g.get("code") == code for g in _rows(_tools().gauges(plant), "gauges"))


def _has_unit(plant: str, code: str) -> bool:
    return _found(_tools().unit(plant, serial=code))


def _make_unit(plant: str, code: str, actor: str) -> None:
    """One identified unit, minted with the serial the case names.

    `produce_batch` is the tool that takes the serial, and it is one of the nine
    this fixture's own cases are about - so the arrangement and the case under
    test are the same call, which is true of the vocabulary fixtures above too.
    """
    _ok(_tools().produce_batch(plant, serials=[code], material=MATERIAL,
                               dry_run=False, on_behalf_of=actor))


KINDS: dict[str, Kind] = {k.name: k for k in (
    Kind("material", _has_material, cannot=_MASTER_DATA, fix=_SEED_IT),
    Kind("machine", _has_machine, cannot=_MASTER_DATA, fix=_SEED_IT),
    Kind("lot", _has_lot, cannot=_MASTER_DATA, fix=_SEED_IT),
    Kind("routing", _has_routing, cannot=_MASTER_DATA, fix=_SEED_IT),
    Kind("spec", _has_spec, cannot=_MASTER_DATA, fix=_SEED_IT),
    Kind("order", _has_order, _make_order),
    Kind("operation", _has_operation, _make_operation),
    Kind("nonconformance", _has_nonconformance, _make_nonconformance),
    Kind("maintenance", _has_maintenance, _make_maintenance),
    Kind("setting", _has_setting, _make_setting),
    Kind("instruction", _has_instruction, _make_instruction),
    Kind("trigger", _has_trigger, _make_trigger),
    Kind("reason", _has_reason, _make_reason),
    Kind("severity", _has_severity, _make_severity),
    Kind("adjustment", _has_adjustment, _make_adjustment,
         cannot=_NO_WRITABLE_SETPOINT, fix=_DECLARE_A_SETPOINT),
    Kind("person", _has_person, cannot=_PERSON,
         fix="ask this case on a plant that has not got this person"),
    Kind("gauge", _has_gauge, cannot=_GAUGE,
         fix="ask this case on a plant that has not got this gauge, or put the gauge "
             "on this one as a person who holds masterdata.write"),
    Kind("unit", _has_unit, _make_unit),
)}


#: The non-conformance and the corrective order are numbered by the plant, not
#: by this run. These are the codes the demo plant gives them, which is what the
#: suite's sentences say; a plant that numbers them differently reports those
#: cases not arranged, with the code it did use.
NONCONFORMANCE = "NC-00001"
MAINTENANCE = "CM-00001"

#: What a run puts on a plant, in the order it has to go on: the order before its
#: own first step and before the check that fails against it, the machine's work
#: after it. Everything here is arrangeable by an agent deployment; everything a
#: case needs that is *not* here is master data, and is declared rather than made.
ARRANGES = (
    f"setting:{SETTING}",
    f"order:{ORDER}",
    f"operation:{ORDER}/{STEP_STARTED}",
    f"nonconformance:{NONCONFORMANCE}",
    f"maintenance:{MAINTENANCE}",
    f"instruction:{INSTRUCTION}",
    f"trigger:{TRIGGER}",
    f"reason:{REASON}",
    f"severity:{SEVERITY}",
    f"adjustment:{MACHINE}",
    f"unit:{UNIT}",
    f"unit:{CONTAINER}",
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


def how_to_arrange(requirement: str) -> str:
    """What would make this plant able to carry it - the other half of `why_not`.

    On 2026-09-27 a live run reported `admin-approves-an-adjustment` not arranged
    on a plant whose tag manifest declares no writable setpoint, and said only
    why. A reader of that file cannot tell from "why" alone whether the plant is
    short of one line or the suite is asking for something no plant could do, and
    that is the difference between a fact about a plant and a bug in a suite.
    """
    bare = requirement[3:].strip() if requirement.startswith("no ") else requirement
    kind, code = split(bare)
    if requirement.startswith("no "):
        return (f"ask this case on a plant that has not got `{code}` - nothing here "
                f"removes a plant's own records, and an MES does not delete an "
                f"audited one")
    listed = KINDS.get(kind)
    if listed is None:
        return "nothing, until a fixture of this kind exists"
    if listed.fix:
        return listed.fix
    return ("a run arranges this itself, so the reason beside it is what this plant "
            "said when it tried")


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
