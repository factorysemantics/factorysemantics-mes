"""Three hundred tradespeople, forty machines, twelve rules, two thousand orders.

A dispatcher that works on a demo and falls over on a plant is not a feature,
it is a demo. So this file builds the plant the handoff asked for - three
hundred people across three trades on three shifts, forty machines on four
lines, twelve rules a supervisor could have written, and a week's backlog of
two thousand due orders - and hands the whole backlog out in one call.

What it holds the dispatcher to:

* every order that *could* have gone to somebody did, in that one call;
* the call costs a bounded number of reads of that backlog on SQLite, which
  is what CI runs on - a figure in the machine's own speed rather than in
  seconds, because a shared runner is twenty times slower than a desk and a
  gate written in seconds fails there for the runner's reasons;
* nobody is given two jobs at the same time;
* each of the three reasons an order stays at `due` happens, and each one is
  the true reason for the orders carrying it.

The figures come out on stdout (`pytest -s`) because they are the answer to
"will this hold on my plant?" and a number nobody can read is not an answer.

ONE INSTANT, NOT A WEEK. The backlog is raised over a week; it is handed out
at one moment, because that is what the plant's tick does - it dispatches what
is due *now*. One moment means one shift on duty, which is the whole reason
two thousand orders cannot all be assigned: a hundred people can hold a
hundred jobs, and the other nineteen hundred are tomorrow's and the day
after's. An MES that pretended otherwise would be inventing capacity.
"""

import json
import time as clock
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select

from fsmes.domain import (
    DISPATCHABLE_LEVEL,
    LEVEL_EXPERT,
    LEVEL_TRAINEE,
    Equipment,
    EquipmentLevel,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenanceStatus,
    Person,
    UnassignedReason,
)
from fsmes.services import calendar, dispatch, masterdata

#: A Thursday morning, inside the early shift, in naive UTC - the product's one
#: timestamp convention. The instant matters: it decides which hundred of the
#: three hundred are on duty, and so what the other nineteen hundred orders are
#: waiting for.
MORNING = datetime(2026, 10, 8, 9, 0)

#: Three shifts covering the day end to end, so there is no hour with nobody
#: on it - a gap would make `nobody_on_shift_with_skill` mean two things.
PATTERNS = (
    ("EARLY", time(6, 0), time(14, 0)),
    ("LATE", time(14, 0), time(22, 0)),
    ("NIGHT", time(22, 0), time(6, 0)),
)

#: Who is on each shift, by trade. **The early shift employs no general
#: maintenance hand**, and that is the arrangement, not an oversight: it is
#: what makes `nobody_on_shift_with_skill` reachable at nine in the morning.
#: Real plants look like this - the handyman works days-on-days, or lates.
CREW = {
    "EARLY": (("ELEC", 45), ("MECH", 55)),
    "LATE": (("ELEC", 34), ("MECH", 33), ("GEN", 33)),
    "NIGHT": (("ELEC", 33), ("MECH", 33), ("GEN", 34)),
}

#: One in twenty is a trainee - level 1, never sent to a job alone - and one in
#: twenty-five is off. Both are ordinary, both subtract from what the shift can
#: actually take, and a scale test with neither would overstate the plant.
EVERY_TRAINEE = 20
EVERY_ABSENCE = 25

LINES = 4
MACHINES_PER_LINE = 10
ORDERS = 2_000
TRADES = ("ELEC", "MECH", "GEN")

#: The line no rule mentions. An order on it is `no_rule`, which is the
#: cheapest of the three to fix and the first a supervisor should be shown.
UNRULED_LINE = LINES


def _machine(line: int, unit: int) -> str:
    return f"M{line}-{unit:02d}"


@pytest.fixture()
def big_plant(session):
    """The plant the handoff describes, arranged once."""
    for code, starts, ends in PATTERNS:
        calendar.create_pattern(session, code=code, name=f"{code.title()} shift",
                                starts=starts, ends=ends, days="1111111")
    for code, name in (("ELEC", "Electrician"), ("MECH", "Mechanic"),
                       ("GEN", "General maintenance")):
        dispatch.create_skill(session, code=code, name=name)

    machines = []
    for line in range(1, LINES + 1):
        masterdata.create_equipment(session, code=f"SIMLINE{line}",
                                    name=f"Simulated line {line}",
                                    level=EquipmentLevel.WORK_CENTER,
                                    parent_code="PKG")
        for unit in range(1, MACHINES_PER_LINE + 1):
            code = _machine(line, unit)
            masterdata.create_equipment(session, code=code, name=f"Machine {code}",
                                        level=EquipmentLevel.WORK_UNIT,
                                        parent_code=f"SIMLINE{line}")
            machines.append(code)

    nth = 0
    for shift, trades in CREW.items():
        for trade, how_many in trades:
            for _ in range(how_many):
                nth += 1
                code = f"MT-{nth:03d}"
                masterdata.create_person(session, code=code, name=f"Tradesperson {nth}",
                                         role="operator",
                                         home_equipment=machines[nth % len(machines)])
                dispatch.grant_skill(
                    session, person_code=code, skill_code=trade,
                    level=LEVEL_TRAINEE if nth % EVERY_TRAINEE == 0 else LEVEL_EXPERT)
                dispatch.set_roster(
                    session, person_code=code, shift_code=shift,
                    available=nth % EVERY_ABSENCE != 0,
                    reason="on holiday" if nth % EVERY_ABSENCE == 0 else None)

    # Twelve sentences a supervisor would say. Every one of them names a line,
    # which is what leaves the fourth line to nobody: a rule with no machine on
    # it would quietly sweep up the work nobody wrote a rule for.
    for line in range(1, UNRULED_LINE):
        dispatch.create_rule(
            session, code=f"SAFETY-{line}", name=f"Safety work on line {line} first",
            equipment_code=f"SIMLINE{line}", priority_at_least=1,
            strategy="least_loaded", sequence=10 + line)
    how = {"ELEC": "nearest", "MECH": "least_loaded", "GEN": "round_robin"}
    for line in range(1, UNRULED_LINE):
        for trade in TRADES:
            dispatch.create_rule(
                session, code=f"{trade}-{line}",
                name=f"{trade} work on line {line}",
                equipment_code=f"SIMLINE{line}", skill_code=trade,
                strategy=how[trade], sequence=100 + line * 10 + TRADES.index(trade))

    # A week of backlog. Every machine, every trade, every priority including
    # none at all - an order raised before the plant started saying which trade
    # it needs is a row this plant still has, and it has to go somewhere.
    ids = {e.code: e.id for e in session.scalars(select(Equipment))}
    orders = []
    for nth in range(ORDERS):
        code = machines[nth % len(machines)]
        orders.append(MaintenanceOrder(
            code=f"CM-{nth:05d}", equipment_id=ids[code],
            kind=MaintenanceKind.CORRECTIVE,
            summary=f"something on {code}",
            skill_code=TRADES[nth % 3],
            priority=(1, 2, 3, None)[nth % 4],
            raised_at=MORNING - timedelta(hours=nth % 168)))
    session.add_all(orders)
    session.flush()
    return session


def _eligible(session, shift_code: str, trade: str) -> set[str]:
    """Who on that shift could actually be sent to that trade's work.

    Worked out from the plant rather than from the arrangement above, so this
    is a second opinion and not the same arithmetic twice.
    """
    on_shift = dispatch.roster(session, shift=f"2026-10-08/{shift_code}")
    return {row["person"] for row in on_shift["people"] if row["available"]
            and any(held["skill"] == trade and held["level"] >= DISPATCHABLE_LEVEL
                    for held in row["skills"])}


def test_a_weeks_backlog_is_handed_out_in_one_pass_and_says_what_nobody_could_take(
        big_plant):
    """Two thousand orders, one call, and the figures a plant manager would ask
    for. The pass has to be quick enough to sit on the plant's own tick: a
    dispatcher that takes four seconds cannot run every supervise pass, and one
    that cannot run every pass is one somebody runs by hand, which is where we
    started."""
    # What one read of the whole backlog costs on THIS machine, averaged over
    # ten, before anything is dispatched. A shared CI runner is ten and twenty
    # times slower than a desk, and a gate written in seconds fails there for
    # the runner's reasons rather than the dispatcher's: this test asked for
    # under a second and got 1.47 s and 1.50 s on 2026-10-09 against 0.09 s on
    # the desk, on `main` as well as on the branch. So the figure the gate is
    # written in is the machine's own speed, and the seconds stay on stdout
    # where a person reading "will this hold on my plant?" can see them.
    rounds = 10
    started = clock.perf_counter()
    for _ in range(rounds):
        big_plant.execute(select(
            MaintenanceOrder.id, MaintenanceOrder.skill_code,
            MaintenanceOrder.priority, MaintenanceOrder.equipment_id)).all()
    one_read = (clock.perf_counter() - started) / rounds

    started = clock.perf_counter()
    report = dispatch.dispatch(big_plant, MORNING)
    seconds = clock.perf_counter() - started
    reads = seconds / one_read if one_read else float("inf")

    orders = list(big_plant.scalars(select(MaintenanceOrder)))
    assigned = [o for o in orders if o.status is MaintenanceStatus.ASSIGNED]
    unassigned = [o for o in orders if o.status is MaintenanceStatus.DUE]
    by_reason = Counter(o.unassigned_reason for o in unassigned)

    # The register, and the crew inside it. Not the same number: the demo
    # plant this suite builds on comes with people of its own, who hold no
    # trade and are on no roster, and quietly calling the register "the crew"
    # would be the sort of rounding this product exists not to do.
    crew = sum(1 for p in big_plant.scalars(select(Person)) if p.code.startswith("MT-"))
    print(f"\n  tradespeople    {crew}")
    print(f"  on the register {report['people']} "
          f"({report['people'] - crew} the demo plant came with)")
    print(f"  shifts          {len(PATTERNS)}")
    print(f"  machines        {LINES * MACHINES_PER_LINE} on {LINES} lines")
    print(f"  rules           {report['rules']}")
    print(f"  orders due      {report['considered']}")
    print(f"  assigned        {report['assigned']}")
    print(f"  unassigned      {report['unassigned']}")
    for reason, count in sorted(by_reason.items()):
        print(f"    {reason:<32} {count}")
    print(f"  seconds         {seconds:.3f}")
    print(f"  one read of the backlog {one_read * 1000:.2f} ms, "
          f"so the pass cost {reads:.0f} of them")

    assert crew == 300
    assert report["rules"] == 12
    assert report["considered"] == ORDERS
    assert report["assigned"] == len(assigned)
    assert report["unassigned"] == len(unassigned)
    assert report["assigned"] + report["unassigned"] == ORDERS
    # The gate: the pass costs a bounded number of reads of the same backlog.
    # It is a loose bound on purpose - it is here to catch a dispatcher that
    # has gone quadratic, which at two thousand orders is not a few per cent
    # but orders of magnitude - and it cannot fail because the runner was
    # busy. Measured at 81 to 86 reads on 2026-10-09.
    assert reads < 400, (
        f"one pass over {ORDERS} orders cost {reads:.0f} reads of the backlog "
        f"({seconds:.2f}s against {one_read * 1000:.2f} ms a read); the plant's "
        "tick cannot carry that, and a pass that grows faster than the backlog "
        "is one somebody ends up running by hand")


def test_everybody_the_early_shift_could_send_was_sent_and_nobody_twice(big_plant):
    """The two halves of "it works": every pair of hands that could have taken
    a job has one, and no pair of hands has two. The second is the one that
    bites - a dispatcher that forgets what it just handed out double-books the
    whole shift in a single pass."""
    dispatch.dispatch(big_plant, MORNING)

    assigned = list(big_plant.scalars(select(MaintenanceOrder).where(
        MaintenanceOrder.status == MaintenanceStatus.ASSIGNED)))
    holders = Counter(o.assigned_to for o in assigned)

    assert holders, "a plant with a hundred people on shift assigned nothing"
    assert holders.most_common(1)[0][1] == 1, (
        f"{holders.most_common(1)[0][0]} was given "
        f"{holders.most_common(1)[0][1]} jobs in the same window")

    # Every electrician and mechanic the early shift had, and only those: no
    # general hand is on this shift, so no GEN work was given out.
    could = _eligible(big_plant, "EARLY", "ELEC") | _eligible(big_plant, "EARLY", "MECH")
    assert set(holders) == could, (
        f"{len(could - set(holders))} people on shift held the trade and were "
        f"given nothing; {len(set(holders) - could)} were given work they are "
        "not on shift for or not signed off for")
    assert not _eligible(big_plant, "EARLY", "GEN")
    assert {o.skill_code for o in assigned} == {"ELEC", "MECH"}


def test_each_order_was_given_to_somebody_who_holds_its_trade_by_a_rule_that_said_so(
        big_plant):
    """A dispatcher that assigns fast and wrong is worse than none. Two checks
    the supervisor would make by eye on the first morning: the trade on the
    order matches the trade in the hands, and the rule named on the row is one
    that covers that machine."""
    dispatch.dispatch(big_plant, MORNING)

    for order in big_plant.scalars(
            select(MaintenanceOrder).where(
                MaintenanceOrder.status == MaintenanceStatus.ASSIGNED)):
        held = _eligible(big_plant, "EARLY", order.skill_code)
        assert order.assigned_to in held, (
            f"{order.code} needs {order.skill_code} and went to "
            f"{order.assigned_to}, who does not hold it on this shift")
        line = order.equipment.parent.code
        assert order.assigned_by in {f"SAFETY-{line[-1]}", f"{order.skill_code}-{line[-1]}"}, (
            f"{order.code} on {line} says it was assigned by {order.assigned_by}")
        assert order.scheduled_for == MORNING
        assert order.assigned_at == MORNING
        assert order.unassigned_reason is None


def test_nobody_wrote_a_rule_for_the_fourth_line_and_the_backlog_says_exactly_that(
        big_plant):
    """`no_rule` is the reason a supervisor can act on this morning: it is a
    sentence they have not written yet, not a shortage of people. So it has to
    be exactly the orders on the line no rule names - no wider, and no
    narrower."""
    dispatch.dispatch(big_plant, MORNING)

    unruled = {o.code for o in big_plant.scalars(
        select(MaintenanceOrder))
        if o.equipment.parent.code == f"SIMLINE{UNRULED_LINE}"}
    said = {o.code for o in big_plant.scalars(
        select(MaintenanceOrder).where(
            MaintenanceOrder.unassigned_reason == UnassignedReason.NO_RULE.value))}

    assert said == unruled
    assert len(said) == ORDERS // LINES


def test_the_general_work_waited_because_no_general_hand_was_on_the_early_shift(
        big_plant):
    """The second reason, and a different morning: the rules are written, the
    work is covered, and the trade is at home in bed. A supervisor reading this
    calls somebody in; reading `all_busy` they would not."""
    dispatch.dispatch(big_plant, MORNING)

    waiting = list(big_plant.scalars(
        select(MaintenanceOrder).where(
            MaintenanceOrder.unassigned_reason
            == UnassignedReason.NOBODY_ON_SHIFT_WITH_SKILL.value)))

    assert waiting
    assert {o.skill_code for o in waiting} == {"GEN"}
    assert all(o.equipment.parent.code != f"SIMLINE{UNRULED_LINE}" for o in waiting)
    # And it is the whole of the covered general work, not some of it.
    covered_gen = [o for o in big_plant.scalars(
        select(MaintenanceOrder).where(
            MaintenanceOrder.skill_code == "GEN"))
        if o.equipment.parent.code != f"SIMLINE{UNRULED_LINE}"]
    assert len(waiting) == len(covered_gen)


def test_the_rest_waited_because_every_electrician_and_mechanic_was_already_out(
        big_plant):
    """The third reason, and the one that is not a problem: a hundred people
    took a hundred jobs and the rest is tomorrow's work. It is only honest if
    there is genuinely nobody left - so the check is that every single person
    who could have taken one of these is holding one."""
    dispatch.dispatch(big_plant, MORNING)

    busy = list(big_plant.scalars(
        select(MaintenanceOrder).where(
            MaintenanceOrder.unassigned_reason == UnassignedReason.ALL_BUSY.value)))
    holders = {o.assigned_to for o in big_plant.scalars(
        select(MaintenanceOrder).where(
            MaintenanceOrder.status == MaintenanceStatus.ASSIGNED))}

    assert busy
    assert {o.skill_code for o in busy} == {"ELEC", "MECH"}
    for trade in ("ELEC", "MECH"):
        idle = _eligible(big_plant, "EARLY", trade) - holders
        assert not idle, (
            f"{len(idle)} {trade} tradespeople were free and "
            f"{sum(1 for o in busy if o.skill_code == trade)} orders needing "
            f"{trade} were left at due saying everybody was busy")


def test_running_the_whole_pass_a_second_time_changes_nothing(big_plant):
    """Idempotent at scale, not only on one order: the plant's tick calls this
    every pass, so a second call a second later must hand out nothing and must
    not revisit the nineteen hundred it could not place."""
    first = dispatch.dispatch(big_plant, MORNING)
    before = {o.code: (o.status, o.assigned_to, o.unassigned_reason)
              for o in big_plant.scalars(
                  select(MaintenanceOrder))}

    second = dispatch.dispatch(big_plant, MORNING)
    after = {o.code: (o.status, o.assigned_to, o.unassigned_reason)
             for o in big_plant.scalars(
                 select(MaintenanceOrder))}

    assert second["assigned"] == 0
    assert second["considered"] == first["unassigned"]
    assert after == before


def test_one_order_out_of_two_thousand_still_explains_itself_in_a_sentence(big_plant):
    """Scale is no excuse for a decision nobody can read. The walk is built for
    one order on request - `explain` - and never for the two thousand, which is
    what keeps the pass quick; this is the proof that asking for it still
    answers."""
    dispatch.dispatch(big_plant, MORNING)
    assigned = next(o for o in big_plant.scalars(
        select(MaintenanceOrder).where(
            MaintenanceOrder.status == MaintenanceStatus.ASSIGNED)))

    walk = dispatch.explain(big_plant, assigned.code)

    assert walk["held_by"] == assigned.assigned_to
    assert walk["rule_says"].startswith("Work on SIMLINE")
    assert len(walk["considered"]) == 100, "the walk should show the whole shift"
    assert sum(1 for row in walk["considered"] if row["chosen"]) == 1


# --------------------------------------------- the supervisor's screen, at scale

@contextmanager
def _selects(session):
    """Every SELECT the session's engine runs inside the block, in order."""
    from sqlalchemy import event

    seen: list[str] = []
    bind = session.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(" ".join(statement.split()))

    event.listen(bind, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(bind, "before_cursor_execute", record)


def test_the_supervisors_screen_reads_this_shift_in_a_handful_of_queries(big_plant):
    """The page the supervisor opens is two reads, and each one is a query per
    table rather than a query per row.

    Three hundred people and two thousand orders is exactly where a screen
    built the obvious way dies: a roster that asks each person what they have
    on is three hundred queries, and a shift list that asks each order which
    machine and which rule is two thousand more. The counts are printed
    because "will this open on my plant?" is answered by a number.
    """
    dispatch.dispatch(big_plant, MORNING)

    with _selects(big_plant) as statements:
        started = clock.perf_counter()
        screen = dispatch.shift_view(big_plant, shift="2026-10-08/EARLY")
        shift_seconds = clock.perf_counter() - started
        shift_read = list(statements)

    with _selects(big_plant) as statements:
        started = clock.perf_counter()
        crew = dispatch.roster(big_plant, shift="2026-10-08/EARLY")
        roster_seconds = clock.perf_counter() - started
        roster_read = list(statements)

    shift_queries, roster_queries = len(shift_read), len(roster_read)

    print(f"\n  on shift          {crew['total']} ({crew['available']} available)")
    print(f"  orders this shift {screen['total']}, waiting {screen['waiting_total']}")
    print(f"  shift read        {shift_queries} queries, {shift_seconds * 1000:.0f} ms")
    print(f"  roster read       {roster_queries} queries, {roster_seconds * 1000:.0f} ms")
    print(f"  sentence          {screen['sentence']}")

    assert crew["total"] == 100, "the early shift is a hundred people"
    assert screen["waiting_total"] > 0, "nothing is waiting, so nothing is being counted"
    # The gate is on queries, not seconds: a shared runner is twenty times
    # slower than a desk and a gate in seconds fails there for the runner's
    # reasons. A query per table is a handful; a query per row would be
    # hundreds. Measured on 2026-10-09: the shift read 4 queries and 26 ms
    # over two thousand orders, the roster 8 queries and 6 ms over a hundred
    # people. Both started higher - the roster was 48 queries, one per busy
    # person for the machine they were at and one per head for where they are
    # based - and this test is what found that.
    assert shift_queries < 20, (
        f"one shift read ran {shift_queries} queries over {screen['total']} "
        "orders; a screen that asks the database once per row does not open "
        "on a plant this size\n  " + "\n  ".join(shift_read))
    assert roster_queries < 20, (
        f"one roster read ran {roster_queries} queries over {crew['total']} "
        "people; the counts have to come out of the database, not out of a "
        "loop\n  " + "\n  ".join(roster_read))


def test_the_shift_read_is_a_screenful_and_says_how_much_of_the_plant_it_is(
        big_plant):
    """Few queries is not the same as little to send.

    Opened on this plant on 2026-10-09, the shift read was 1.7 MB of JSON -
    two thousand open orders and nineteen hundred waiting ones, every one of
    them a row the tab would build. That is a download, not a screen, and on
    a phone over a tunnel it is seconds of nothing. So each list carries its
    newest `MOST_SHOWN` rows and says how many of its total that is, while
    the totals and the sentence are still taken over the whole backlog.
    """
    dispatch.dispatch(big_plant, MORNING)

    screen = dispatch.shift_view(big_plant, shift="2026-10-08/EARLY")
    size = len(json.dumps(screen, default=str))

    print(f"\n  shift read        {size / 1024:.0f} KiB of JSON")
    print(f"  orders            {screen['shown']} shown of {screen['total']}")
    for group in screen["waiting"]:
        print(f"  {group['reason']:<24} {group['shown']} shown of {group['total']}")

    assert screen["total"] > dispatch.MOST_SHOWN, "too small a plant to be the test"
    assert screen["shown"] == dispatch.MOST_SHOWN
    assert len(screen["orders"]) == dispatch.MOST_SHOWN
    # The totals are the plant's, not the page's: a screen that showed fifty
    # and counted fifty would be telling a supervisor their backlog is clear.
    assert screen["counts"]["waiting"] == screen["waiting_total"] > dispatch.MOST_SHOWN
    for group in screen["waiting"]:
        assert len(group["orders"]) == group["shown"] <= group["total"]
    assert size < 400_000, (
        f"one shift read sends {size / 1024:.0f} KiB; the tab refreshes itself "
        "every few seconds and a supervisor on a phone pays for all of it")
