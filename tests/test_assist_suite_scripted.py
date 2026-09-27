"""The assistant's request suite, scored against the real plumbing.

This is the wrapper that makes `tests/assist_suite/` a required check. It runs
the whole suite with the model scripted - the real guide router, the real tool
catalogue per role, the real tools against a real seeded plant, the real
surfaces, the real conversation loop - and every case the current code should
pass has to pass.

Cases the current code *cannot* pass yet are marked `not_yet` in the suite with
the handoff that will make them pass. They are counted apart, and this file
fails if one of them starts passing: a fix that is in and unrecorded is a fix
nobody knows they can rely on.

What this file cannot tell you is whether a real model would choose the right
tool. `fsmes assist eval --live` asks that, on a running plant, for money.
"""

import ast
import json
import pathlib
import threading
import typing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from typer.testing import CliRunner

from fsmes import cli as cli_module
from fsmes.cli import app
from fsmes.lab import assist_eval as assist_runs
from fsmes.lab import assist_fixtures, assist_seed
from fsmes.services import agent, assist_eval, assistant
from fsmes.services import capabilities as caps

SUITE = assist_eval.load()
BY_ID = {case.id: case for case in SUITE}


# ---------------------------------------------------------- the suite itself

def test_the_suite_is_readable_and_covers_every_role_a_plant_signs_people_in_as():
    assert len(SUITE) >= 40, f"the suite is meant to be a suite: {len(SUITE)} case(s)"
    assert {case.role for case in SUITE} == {"operator", "supervisor", "admin", "agent"}


def test_every_case_names_a_role_this_product_actually_has():
    unknown = sorted({case.role for case in SUITE} - set(caps.BUILTIN_ROLES))
    assert not unknown, f"roles this product does not define: {unknown}"


def test_every_write_tool_the_assistant_may_propose_has_at_least_one_case():
    """The suite is only a measure of coverage if it covers. `NEEDS` is the list
    of write tools the agent may be offered, and `write_plant_setting` is the one
    whose capability is decided per call."""
    named = {case.tool for case in SUITE if case.tool}
    missing = sorted((set(agent.NEEDS) | set(agent.PER_CALL_NEEDS)) - named)
    assert not missing, f"write tools no case asks for: {missing}"


def test_every_tool_a_case_names_is_a_tool_this_plant_serves():
    served = {tool.name for tool in agent.registry_tools()}
    named = {case.tool for case in SUITE if case.tool}
    for case in SUITE:
        named |= set(case.reads) | set(case.reads_any)
    assert not sorted(named - served)


def test_a_case_that_should_pass_names_a_guide_that_exists():
    """A `not_yet` case is allowed to name a walk nobody has authored yet - that
    is what it is for. A required one is not."""
    ids = {guide["id"] for guide in assistant.GUIDES}
    for case in SUITE:
        if case.expect == "walk" and case.expected == "pass" \
                and case.guide != assist_eval.OWN_WALK:
            assert case.guide in ids, f"{case.id} names no guide: {case.guide}"


def test_every_not_yet_case_says_which_handoff_will_make_it_pass():
    for case in SUITE:
        if case.expected == "not_yet":
            assert case.handoff, case.id


def test_the_suite_asks_about_every_approval_a_person_signs():
    """An agent never approves. Being refused is not the answer either: the
    answer is a walk to the signing control, which is why each of these is a
    walk case."""
    approvals = {case.guide for case in SUITE
                 if case.expect == "walk" and case.guide.startswith("approve-")}
    assert approvals == {"approve-a-document", "approve-a-severity",
                         "approve-a-downtime-reason", "approve-a-trigger",
                         "approve-an-adjustment"}


def test_the_suite_has_refusals_and_turns_that_must_look_before_they_answer():
    assert sum(1 for c in SUITE if c.expect == "refuse") >= 3
    assert sum(1 for c in SUITE if c.expect == "read") >= 5


@pytest.mark.parametrize("quoted", [
    "I want to change the default reporting window to 10hrs",
    "I just changed it to 10.0 hrs. Could you change it back to 8 hrs?",
    "I want a non-conformance to have a prefix CR instead of NC. Could you make that change?",
    "could you show me where?",
    "set it to4",
    "which spc rules are on hold",
    "I want to change the quality configuration for which spc rules raise a hold to 1,2 only",
    "configurate this When a gauge can judge a tolerance to fail",
    "take me to scrap",
    "worst scrap records",
    "orders from last week",
    "Change default_job_minutes to 56",
])
def test_the_requests_scott_actually_typed_are_in_the_suite_word_for_word(quoted):
    """Including the typing. A suite that tidies up the request is a suite about
    a conversation nobody had."""
    assert any(case.request == quoted for case in SUITE), quoted


# ------------------------------------------------------------- the whole run

@pytest.fixture(scope="module")
def scored():
    return assist_runs.run_scripted(SUITE)


def test_every_case_the_current_code_should_pass_does_pass(scored):
    failed = [o for o in scored if o.counted and not o.passed]
    assert not failed, "\n".join(
        f"{o.case.id}: {'; '.join(o.why)}" for o in failed)


def test_no_case_marked_not_yet_is_passing_already(scored):
    """The ratchet, backwards. A `not_yet` case that passes means a sibling
    handoff landed and nobody took the mark off - so the number the write-up
    quoted is no longer the number.

    A case the plant was not arranged for is out of the required number too, and
    is not this ratchet's business: scripted mode asks it anyway and it may well
    pass, which says nothing about a handoff.
    """
    passing = [o.case for o in scored if o.arranged and not o.counted and o.passed]
    assert not passing, ("these are marked not_yet and pass now; take the mark off and "
                         "say so: " + ", ".join(f"{c.id} (was waiting on {c.handoff})"
                                                for c in passing))


def test_the_run_says_a_pass_rate_for_every_role(scored):
    counts = assist_eval.tally(scored)
    assert set(counts["roles"]) == {"operator", "supervisor", "admin", "agent"}
    # Three buckets and every case in exactly one of them. The third is not
    # always empty even here: a fixture can need something of the plant's own
    # that a seeded demo plant has no way to declare.
    assert counts["required"] + counts["not_yet"] + counts["not_arranged"] == len(SUITE)
    assert counts["total"] == len(SUITE)


def test_the_report_names_each_failure_and_what_it_did_instead(scored):
    page = assist_eval.report(scored, mode="scripted")
    assert "## Per role" in page and "| operator |" in page
    assert "## Marked `not_yet`" in page
    for outcome in scored:
        if outcome.arranged and not outcome.counted:
            assert outcome.case.id in page
            assert outcome.case.handoff in page


def test_the_run_is_fast_enough_to_sit_on_every_pull_request():
    """Under thirty seconds is the constraint this was built to. It is around two
    on this machine; the assertion is a fuse, not a benchmark."""
    import time

    started = time.monotonic()
    assist_runs.run_scripted(SUITE[:12])
    assert time.monotonic() - started < 30


# ------------------------------------------------------- the scorer itself

@pytest.mark.parametrize(("want", "got", "same"), [
    (1.33, "1.33", True),
    ("10", 10.0, True),
    ("1,2", "2, 1", True),
    ("CR", "cr", True),
    (56, 56.0, True),
    ("1,2", "1,2,3", False),
    ("CR", "NC", False),
    (56, 55, False),
    (None, "", False),
])
def test_a_value_is_the_same_value_however_it_is_written(want, got, same):
    assert assist_eval.same_value(want, got) is same


def test_a_proposal_with_the_wrong_number_in_it_does_not_pass():
    case = BY_ID["scott-wants-the-nonconformance-prefix-to-be-cr"]
    turn = assist_eval.Turn(kind="proposals", proposals=(
        {"tool": "write_plant_setting",
         "args": {"domain": "quality", "key": "nc_code_prefix", "value": "NC"},
         "surface": {"steps": [{"anchor": "setting-in-focus"}]}},))
    outcome = assist_eval.score(case, turn)
    assert not outcome.passed
    assert any("'CR'" in why for why in outcome.why)


def test_a_proposal_that_leaves_out_what_the_request_named_does_not_pass():
    case = BY_ID["operator-books-good-and-scrap"]
    turn = assist_eval.Turn(kind="proposals", proposals=(
        {"tool": "book_output", "args": {"equipment": "MIX01", "good": 600},
         "surface": {"steps": [{"anchor": "report-good"}]}},))
    outcome = assist_eval.score(case, turn)
    assert not outcome.passed
    assert any("left out scrap" in why for why in outcome.why)


def test_a_walk_that_lands_on_the_wrong_control_does_not_pass():
    """2026-09-25: the walk arrived on the Configuration page and stopped at the
    top of it. A walk that reaches the page and not the box is the failure Scott
    reported, so the anchor is what is scored."""
    case = BY_ID["scott-changes-how-long-an-unplanned-job-takes"]
    turn = assist_eval.Turn(kind="proposals", proposals=(
        {"tool": "write_plant_setting",
         "args": {"domain": "engineering", "key": "default_job_minutes", "value": "56"},
         "surface": {"steps": [{"anchor": "config-page"}]}},))
    outcome = assist_eval.score(case, turn)
    assert not outcome.passed
    assert any("setting-in-focus" in why for why in outcome.why)


def test_a_guide_answering_a_request_for_a_change_does_not_pass():
    """The 2026-09-26 afternoon, in one assertion: a request to change a setting
    answered with a walk about work instructions."""
    case = BY_ID["scott-wants-the-nonconformance-prefix-to-be-cr"]
    turn = assist_eval.Turn(kind="guide", guide_id="find-instruction")
    outcome = assist_eval.score(case, turn)
    assert not outcome.passed


def test_a_question_answered_without_looking_does_not_pass():
    case = BY_ID["scott-says-he-changed-it-and-asks-for-it-back"]
    turn = assist_eval.Turn(kind="reply", from_model=True,
                            say="I don't see any change recorded.")
    outcome = assist_eval.score(case, turn)
    assert not outcome.passed
    assert any("without reading setting_changes" in why for why in outcome.why)
    assert any("fell back on" in why for why in outcome.why)


def test_a_tool_offered_to_a_role_that_may_not_use_it_is_not_a_refusal():
    case = BY_ID["operator-refused-closing-a-nonconformance"]
    turn = assist_eval.Turn(kind="reply", offered=frozenset({"close_nonconformance"}),
                            refusals=("quality.close_nc - a supervisor can",),
                            from_model=False)
    outcome = assist_eval.score(case, turn)
    assert not outcome.passed
    assert any("is offered to this role" in why for why in outcome.why)


# ------------------------------------- the conversation the API would refuse

def test_a_conversation_with_an_unanswered_tool_call_is_refused_the_way_the_api_refuses_it():
    """On 2026-09-26 a message typed over an open proposal left a `tool_use`
    block with no `tool_result` after it, and every later call in that
    conversation came back `BadRequestError`. A stand-in that accepted what the
    hosted API refuses would have scored that page as fine."""
    history = [
        {"role": "user", "content": "change the prefix to CR"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "write_plant_setting"}]},
        {"role": "user", "content": "set it to4"},
    ]
    with pytest.raises(assist_eval.HistoryRefused):
        assist_eval.check_history(history)


def test_a_conversation_whose_tool_calls_were_all_answered_is_accepted():
    history = [
        {"role": "user", "content": "change the prefix to CR"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "plant_settings"}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]},
        {"role": "user", "content": "and now set it to CR"},
    ]
    assist_eval.check_history(history)          # no exception


# ----------------------------------------------------- the live run, doubled

class _Double(BaseHTTPRequestHandler):
    """A plant, as far as a live run can tell. Every reply is canned, so the
    HTTP path, the sign-in, the budget arithmetic, the declining of proposals
    and the result file are all proven with no key and no money.

    It answers nothing about orders, so the runs below pass `arranging=False`:
    arranging a plant is proven further down, against a real one on a real
    socket. It does keep a master-data table, because the other thing a live run
    can be asked to do is put the demo plant's master data there as the person
    signed in, and that is an HTTP path too - ten `POST`s and the reads that
    decide whether to make them."""

    state: typing.ClassVar[dict] = {}

    def log_message(self, *args):    # keep pytest output readable
        pass

    def _who(self) -> str | None:
        """The account behind this request, out of its own bearer token."""
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        return token.removeprefix("token-") or None

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    #: Where each sort of master data lives on a plant, and whether that list
    #: endpoint answers with a bare list or a paged envelope. Both shapes are
    #: real, and a reader that assumed one would report an absence on the other.
    LISTS: typing.ClassVar[dict] = {
        "/masterdata/equipment": ("equipment", "bare"),
        "/masterdata/materials": ("materials", "bare"),
        "/masterdata/routings": ("routings", "bare"),
        "/quality/specs": ("specs", "envelope"),
        "/execution/lots": ("lots", "envelope"),
    }

    def do_GET(self):
        url = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/health":
            return self._send({"status": "ok", "plant": "doubled"})
        if url.path == "/assist/agent/status":
            return self._send({"available": True, "reason": "ok", "model": "test-model",
                               "spend_usd": self.state["spend"], "cap_usd": 10.0,
                               "tokens_this_month": {"input": self.state["asks"] * 100,
                                                     "output": self.state["asks"] * 10}})
        if url.path == "/auth/me":
            return self._send({"code": self.state.get("signed_in_as"), "role": "admin",
                               "name": "Doubled",
                               "capabilities": sorted(self.state["capabilities"])})
        if url.path in self.LISTS:
            name, shape = self.LISTS[url.path]
            rows = [row for row in self.state["masterdata"][name] if _matches(row, query)]
            if shape == "bare":
                return self._send(rows)
            return self._send({"items": rows, "total": len(rows), "limit": 50,
                               "offset": 0, "has_more": False})
        return self._send({"detail": "not found"}, 404)

    #: What this plant asks for before it will take one, the way the real
    #: routers do: everything but a lot wants `masterdata.write`, and a lot is
    #: stock, so it wants the capability that books stock.
    WANTS: typing.ClassVar[dict] = {
        "/masterdata/equipment": ("equipment", "masterdata.write"),
        "/masterdata/materials": ("materials", "masterdata.write"),
        "/masterdata/routings": ("routings", "masterdata.write"),
        "/quality/specs": ("specs", "masterdata.write"),
        "/execution/lots": ("lots", "production.consume"),
    }

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.state["posted"].append(self.path)
        if self.path in self.WANTS:
            name, capability = self.WANTS[self.path]
            if capability not in self.state["capabilities"]:
                return self._send({"detail": f"needs {capability}"}, 403)
            self.state["masterdata"][name].append(dict(body))
            self.state["made"].append((self.path, dict(body)))
            return self._send(dict(body), 201)
        if self.path == "/auth/login":
            self.state["signed_in_as"] = body.get("code")
            # A token per account, so who asked a question is a fact this double
            # can report rather than one a test has to take on trust.
            return self._send({"token": f"token-{body.get('code')}"})
        if self.path == "/assist/agent":
            self.state["asks"] += 1
            self.state["spend"] = round(self.state["spend"] + 0.40, 6)
            self.state["asked"].append(body.get("message"))
            self.state["asked_by"].append((self._who(), body.get("message")))
            return self._send(self.state["replies"].get(
                body.get("message"), {"kind": "reply", "session": "s1", "say": "I do not know.",
                                      "transcript": []}))
        if self.path == "/assist/agent/decline":
            self.state["declined"].append(body.get("proposal"))
            return self._send({"kind": "reply", "session": "s1", "say": "Left alone."})
        return self._send({"detail": "not found"}, 404)


def _matches(row: dict, query: dict) -> bool:
    """The filters this needs, and no more: `q` on a code, and the pair that
    names a specification."""
    if "q" in query and query["q"] not in str(row.get("code", "")):
        return False
    return all(row.get(field) == query[field]
               for field in ("material", "characteristic") if field in query)


@pytest.fixture()
def doubled_plant():
    _Double.state = {"spend": 0.0, "asks": 0, "asked": [], "asked_by": [], "declined": [], "replies": {},
                     "posted": [], "made": [],
                     # An admin, which is who a live run signs in as.
                     "capabilities": {"masterdata.write", "production.consume"},
                     "masterdata": {"equipment": [], "materials": [], "routings": [],
                                    "specs": [], "lots": []}}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Double)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", _Double.state
    server.shutdown()
    server.server_close()


def test_a_live_run_scores_a_plant_over_its_own_http_api(doubled_plant):
    url, state = doubled_plant
    case = BY_ID["scott-wants-the-nonconformance-prefix-to-be-cr"]
    state["replies"][case.request] = {
        "kind": "proposals", "session": "s1", "say": "I can change it to CR.",
        "transcript": [{"tool": "plant_settings", "args": {}, "ok": True, "summary": "1 item"}],
        "proposals": [{"id": "p1", "tool": "write_plant_setting",
                       "args": {"domain": "quality", "key": "nc_code_prefix", "value": "CR"},
                       "surface": {"steps": [{"anchor": "setting-in-focus"},
                                             {"anchor": "setting-save-in-focus"}]}}],
    }
    plant = assist_runs.Plant(url)
    plant.sign_in("ADMIN", "a-password")
    outcomes, run = assist_runs.run_live((case,), plant, accounts={"admin": plant},
                                        arranging=False)
    plant.close()

    assert state["signed_in_as"] == "ADMIN"
    assert run["asked_as"] == {"admin": "ADMIN"}
    assert run["no_account"] == ()
    assert state["asked"] == [case.request]
    assert outcomes[0].passed, outcomes[0].why
    assert outcomes[0].turn.from_model is True
    assert run["model"] == "test-model"
    assert run["usd"] == pytest.approx(0.40)
    assert run["tokens"] == {"input": 100, "output": 10}


# ------------------------------------------- one account per role, and no other

def _two_roles(state) -> tuple:
    """One administrator's case and one operator's, each with a canned reply
    that would pass, so what these tests measure is who was asked."""
    admin = BY_ID["scott-wants-the-nonconformance-prefix-to-be-cr"]
    operator = BY_ID["operator-raises-everything-that-is-due"]
    state["replies"][admin.request] = {
        "kind": "proposals", "session": "s1", "say": "I can change it to CR.",
        "proposals": [{"id": "p1", "tool": "write_plant_setting",
                       "args": {"domain": "quality", "key": "nc_code_prefix",
                                "value": "CR"},
                       "surface": {"steps": [{"anchor": "setting-in-focus"}]}}]}
    state["replies"][operator.request] = {
        "kind": "proposals", "session": "s2", "say": "Here is the work that is due.",
        "proposals": [{"id": "p2", "tool": "raise_due_maintenance", "args": {}}]}
    return admin, operator


def test_each_role_is_asked_as_an_account_that_holds_it(doubled_plant):
    """Until 2026-09-27 one `--user` answered for every role, so a run of the
    operator suite as ADMIN reported a number about the administrator. Two
    accounts, two roles, and the double says which token each question arrived
    on."""
    url, state = doubled_plant
    admin_case, operator_case = _two_roles(state)
    as_admin, as_operator = assist_runs.Plant(url), assist_runs.Plant(url)
    as_admin.sign_in("ADMIN", "a-password")
    as_operator.sign_in("SCOTT", "a-password")

    outcomes, run = assist_runs.run_live(
        (admin_case, operator_case), as_admin,
        accounts={"admin": as_admin, "operator": as_operator}, arranging=False)
    as_admin.close()
    as_operator.close()

    assert state["asked_by"] == [("ADMIN", admin_case.request),
                                 ("SCOTT", operator_case.request)]
    assert run["asked_as"] == {"admin": "ADMIN", "operator": "SCOTT"}
    assert all(o.passed for o in outcomes), [o.why for o in outcomes]


def test_a_role_with_no_account_is_reported_rather_than_asked_as_somebody_else(
        doubled_plant):
    """The point of the whole thing: the operator's case is not asked at all, it
    is not paid for, and it is out of the required number instead of being
    scored against somebody else's session."""
    url, state = doubled_plant
    admin_case, operator_case = _two_roles(state)
    as_admin = assist_runs.Plant(url)
    as_admin.sign_in("ADMIN", "a-password")

    outcomes, run = assist_runs.run_live(
        (admin_case, operator_case), as_admin, accounts={"admin": as_admin},
        arranging=False)
    as_admin.close()

    assert state["asked_by"] == [("ADMIN", admin_case.request)]
    assert run["no_account"] == ("operator",)
    unasked = [o for o in outcomes if o.no_account]
    assert [o.case.id for o in unasked] == [operator_case.id]
    assert not unasked[0].counted and not unasked[0].passed
    assert "no account holding the operator role" in unasked[0].why[0]
    counts = assist_eval.tally(outcomes)
    assert counts["required"] == 1 and counts["passed"] == 1
    assert counts["no_account"] == 1
    assert counts["roles"]["operator"]["required"] == 0
    assert run["usd"] == pytest.approx(0.40), "an unasked case must not be paid for"


def test_a_result_file_says_which_account_answered_for_each_role(doubled_plant):
    """A per-role number is about whoever was typing, so the file says who that
    was - and names the roles nobody held rather than leaving a gap."""
    url, state = doubled_plant
    admin_case, operator_case = _two_roles(state)
    as_admin = assist_runs.Plant(url)
    as_admin.sign_in("ADMIN", "a-password")
    outcomes, run = assist_runs.run_live(
        (admin_case, operator_case), as_admin, accounts={"admin": as_admin},
        arranging=False)
    as_admin.close()

    page = assist_eval.report(outcomes, mode="live", model=run["model"], plant="doubled",
                              run=run)
    assert "| admin | ADMIN |" in page
    assert "| operator | nobody |" in page
    assert "## Not asked — no account for the role" in page
    assert operator_case.id in page


def test_a_live_run_declines_every_proposal_it_opens(doubled_plant):
    """Scoring a plant must not change one."""
    url, state = doubled_plant
    case = BY_ID["operator-raises-everything-that-is-due"]
    state["replies"][case.request] = {
        "kind": "proposals", "session": "s1", "say": "",
        "proposals": [{"id": "p9", "tool": "raise_due_maintenance", "args": {}}],
    }
    plant = assist_runs.Plant(url)
    plant.sign_in("SCOTT", "a-password")
    assist_runs.run_live((case,), plant, accounts={"operator": plant}, arranging=False)
    plant.close()
    assert state["declined"] == ["p9"]


def test_a_live_run_stops_at_the_budget_it_was_given_and_says_what_it_did_not_run(doubled_plant):
    url, _state = doubled_plant
    # Cases that need nothing on the plant, so this is about the budget and
    # nothing else: a canned double has no master data to be asked about.
    cases = tuple(c for c in SUITE if not c.requires and not c.over_proposal)[:6]
    plant = assist_runs.Plant(url)
    plant.sign_in("SCOTT", "a-password")
    accounts = {role: plant for role in {c.role for c in cases}}
    outcomes, run = assist_runs.run_live(cases, plant, accounts=accounts, max_usd=1.00,
                                        arranging=False)
    plant.close()
    # 40 cents a turn: the third takes it to 1.20, and the fourth never starts.
    assert len(outcomes) == 3
    assert len(run["not_run"]) == len(cases) - 3
    assert run["usd"] == pytest.approx(1.20)


def test_a_live_run_on_a_plant_with_no_brain_says_so_rather_than_scoring_zero(doubled_plant):
    url, _state = doubled_plant

    class Off(assist_runs.Plant):
        def brain(self):
            return {"available": False, "reason": "no ANTHROPIC_API_KEY in this plant's "
                                                  "environment"}

    plant = Off(url)
    plant.sign_in("ADMIN", "a-password")
    with pytest.raises(assist_runs.LiveRefused, match="ANTHROPIC_API_KEY"):
        assist_runs.run_live(SUITE[:1], plant, accounts={SUITE[0].role: plant},
                             arranging=False)
    plant.close()


def test_a_plant_that_cannot_be_read_reports_cases_not_arranged_rather_than_failed(
        doubled_plant):
    """The canned double answers nothing about master data. A run against it must
    not conclude that the plant has no MIX01 and score the model down for saying
    so - it must say it could not find out."""
    url, _state = doubled_plant
    case = BY_ID["operator-books-good-and-scrap"]
    plant = assist_runs.Plant(url)
    plant.sign_in("SCOTT", "a-password")
    outcomes, run = assist_runs.run_live((case,), plant, accounts={"operator": plant},
                                        arranging=False)
    plant.close()
    assert not outcomes[0].arranged
    assert not outcomes[0].counted
    assert any("could not be read" in m for m in outcomes[0].missing)
    assert run["usd"] == 0.0, "an unaskable case must not be paid for"


def test_a_live_result_file_says_the_model_the_plant_and_what_it_cost(tmp_path, doubled_plant):
    url, state = doubled_plant
    case = BY_ID["scott-asks-which-spc-rules-are-on-hold"]
    state["replies"][case.request] = {
        "kind": "reply", "session": "s1",
        "say": "Rules 1,2,3,4 raise a hold - that is hold_rules, and it is the default.",
        "transcript": [{"tool": "plant_settings", "args": {"find": "hold"}, "ok": True,
                        "summary": "1 item"}],
    }
    plant = assist_runs.Plant(url)
    plant.sign_in("ADMIN", "a-password")
    outcomes, run = assist_runs.run_live((case,), plant, accounts={"admin": plant},
                                        arranging=False)
    plant.close()
    page = assist_eval.report(outcomes, mode="live", model=run["model"], plant="doubled",
                              plant_commit="abc1234", run=run)
    written = assist_eval.write_report(page, directory=tmp_path)
    text = written.read_text(encoding="utf-8")
    assert "test-model" in text and "doubled" in text and "abc1234" in text
    assert "$0.4000" in text and "input 100" in text
    assert "Assistant faithfulness" in text


# ------------------------ the master data a person seeds, and never the agent

#: What the suite's own `requires` lines name for master-data kinds: the ones a
#: plant has to have, and the ones it has to be without.
MASTER_KINDS = frozenset(assist_seed.RECIPES)


def _named_by_a_case(*, absent: bool) -> set[str]:
    wanted = set()
    for case in SUITE:
        for requirement in case.requires:
            says_absent = requirement.startswith("no ")
            bare = requirement[3:].strip() if says_absent else requirement
            if assist_seed.split(bare)[0] in MASTER_KINDS and says_absent is absent:
                wanted.add(bare)
    return wanted


def _signed_in(url: str, *, code: str = "ADMIN") -> assist_runs.Plant:
    plant = assist_runs.Plant(url)
    plant.sign_in(code, "a-password")
    return plant


def test_what_a_run_seeds_covers_every_piece_of_master_data_the_suite_asks_a_plant_for():
    """The list is a judgment and lives in one place; this is what holds it to
    the suite. A case that starts naming a material nobody seeds would otherwise
    be a case that quietly comes back not arranged for ever."""
    missing = _named_by_a_case(absent=False) - set(assist_seed.SEEDS)
    assert not missing, f"the suite asks a plant for master data nothing seeds: {missing}"


def test_nothing_seeded_is_something_a_case_needs_the_plant_not_to_have():
    """`admin-drafts-a-routing` needs a plant with no `RT-DIET` on it. Seeding
    one would not fail loudly - it would take that case out of the required
    number and look like a smaller suite."""
    clash = set(assist_seed.SEEDS) & _named_by_a_case(absent=True)
    assert not clash, f"a run would seed what a case needs absent: {clash}"


def test_nothing_is_put_on_somebodys_plant_without_a_reason_a_person_can_read():
    """Two lists, and every code on the seeding list is on one of them: named by
    a case, or written down with why. A tenth row nobody can account for is how a
    suite starts redecorating plants."""
    unaccounted = (set(assist_seed.SEEDS) - _named_by_a_case(absent=False)
                   - set(assist_seed.BEYOND_THE_SUITE))
    assert not unaccounted, unaccounted
    stale = set(assist_seed.BEYOND_THE_SUITE) - set(assist_seed.SEEDS)
    assert not stale, f"a reason for master data nothing seeds any more: {stale}"


def test_the_codes_and_the_numbers_come_from_the_demo_pack_and_are_not_retyped():
    """`seed_demo_plant` is where the demo plant is written down. These are the
    numbers `docs/ai/ASSIST-EVAL.md` promises a person, read back out of it - so
    a change to the demo pack turns this red rather than quietly putting a
    different plant on somebody's floor."""
    bodies = assist_seed.demo_master_data()
    assert set(bodies) == set(assist_seed.SEEDS)
    assert bodies["material:FG-COLA"] == {"code": "FG-COLA", "name": "Cola Syrup 1L",
                                          "unit": "ea", "type": "finished",
                                          "counted_in_pieces": False}
    assert bodies["machine:MIX01"]["ideal_cycle_seconds"] == 4.0
    assert bodies["machine:PACK01"]["ideal_cycle_seconds"] == 3.0
    assert bodies["spec:FG-COLA/brix"] == {"material": "FG-COLA", "characteristic": "brix",
                                          "unit": "\u00b0Bx", "min_value": 9.5,
                                          "max_value": 11.5}
    assert bodies["lot:LOT-SUGAR-001"]["quantity"] == 500
    assert bodies["lot:LOT-FLAVOR-001"]["quantity"] == 100
    assert bodies["routing:RT-COLA"]["operations"] == [
        {"seq": 10, "name": "Mix", "equipment": "MIX01"},
        {"seq": 20, "name": "Pack", "equipment": "PACK01"}]


def test_a_line_seeded_onto_somebody_elses_plant_does_not_claim_a_parent_it_never_made():
    """The demo pack hangs LINE1 under an area under a site under an enterprise.
    A run has no business inventing four levels of another plant's hierarchy, so
    LINE1 arrives parentless and the two machines arrive under it."""
    bodies = assist_seed.demo_master_data()
    assert bodies["machine:LINE1"]["parent"] is None
    assert bodies["machine:LINE1"]["level"] == "work_center"
    assert bodies["machine:MIX01"]["parent"] == "LINE1"
    assert bodies["machine:PACK01"]["parent"] == "LINE1"


def test_seeding_puts_the_demo_plants_master_data_there_over_the_plants_own_api(doubled_plant):
    url, state = doubled_plant
    plant = _signed_in(url)
    seeded = assist_seed.seed(plant)
    plant.close()

    assert seeded["made"] == list(assist_seed.SEEDS)
    assert seeded["already"] == [] and seeded["refused"] == {}
    # Ten POSTs, the same ten a person made by hand on 2026-09-27, to the
    # product's own endpoints - and not one of them to an assistant path.
    assert [path for path, _ in state["made"]] == [
        "/masterdata/equipment", "/masterdata/equipment", "/masterdata/equipment",
        "/masterdata/materials", "/masterdata/materials", "/masterdata/materials",
        "/masterdata/routings", "/quality/specs",
        "/execution/lots", "/execution/lots"]
    assert not any(path.startswith("/assist") for path in state["posted"])
    assert state["signed_in_as"] == "ADMIN"


def test_the_machines_go_on_before_the_routing_that_names_them(doubled_plant):
    """Order is not a preference here: `create_routing` resolves every operation's
    equipment by code and refuses a step with nowhere to happen."""
    url, state = doubled_plant
    plant = _signed_in(url)
    assist_seed.seed(plant)
    plant.close()
    paths = [path for path, _ in state["made"]]
    assert paths.index("/masterdata/routings") > max(
        i for i, path in enumerate(paths) if path == "/masterdata/equipment")
    assert paths.index("/masterdata/routings") > max(
        i for i, path in enumerate(paths) if path == "/masterdata/materials")


def test_seeding_the_same_plant_twice_puts_nothing_there_the_second_time(doubled_plant):
    """Scott rebuilds the fleet when he likes; a person should be able to point
    this at the same plant as often as they want."""
    url, state = doubled_plant
    plant = _signed_in(url)
    assist_seed.seed(plant)
    state["made"].clear()
    again = assist_seed.seed(plant)
    plant.close()

    assert again["already"] == list(assist_seed.SEEDS)
    assert again["made"] == [] and again["refused"] == {}
    assert state["made"] == [], "a second run wrote to the plant"


def test_a_code_that_is_already_there_is_left_exactly_as_the_plant_has_it(doubled_plant):
    """Not updated, ever. A plant whose FG-COLA is measured in litres keeps its
    own; a run that corrected somebody's master data to match a test suite would
    be the worst thing in this repository."""
    url, state = doubled_plant
    state["masterdata"]["materials"].append(
        {"code": "FG-COLA", "name": "Their cola", "unit": "l", "type": "finished"})
    plant = _signed_in(url)
    seeded = assist_seed.seed(plant)
    plant.close()

    assert "material:FG-COLA" in seeded["already"]
    assert state["masterdata"]["materials"][0] == {
        "code": "FG-COLA", "name": "Their cola", "unit": "l", "type": "finished"}
    assert not any(body.get("code") == "FG-COLA" for _path, body in state["made"])


def test_seeding_is_refused_plainly_when_the_account_may_not_define_master_data(doubled_plant):
    """An operator may not, and should be told so in one sentence rather than
    collecting ten refusals - and nothing should be written on the way to
    finding out."""
    url, state = doubled_plant
    state["capabilities"] = {"production.book"}
    plant = _signed_in(url, code="SCOTT")
    with pytest.raises(assist_seed.Refused, match=r"masterdata\.write"):
        assist_seed.seed(plant)
    plant.close()
    assert state["made"] == [] and state["posted"] == ["/auth/login"]


def test_an_account_that_may_define_master_data_but_not_stock_says_what_the_lots_wanted(
        doubled_plant):
    """A lot is stock, not a definition, and this product asks for the capability
    that books stock. The eight definitions still go on; the two lots come back
    with the capability named, not with a shrug."""
    url, state = doubled_plant
    state["capabilities"] = {"masterdata.write"}
    plant = _signed_in(url)
    seeded = assist_seed.seed(plant)
    plant.close()

    assert len(seeded["made"]) == 8
    assert set(seeded["refused"]) == {"lot:LOT-SUGAR-001", "lot:LOT-FLAVOR-001"}
    for why in seeded["refused"].values():
        assert "production.consume" in why
    assert not any(path == "/execution/lots" for path in state["posted"])


def test_a_plant_that_will_not_say_whether_it_has_something_is_not_written_to(doubled_plant):
    """"I could not find out" and "it is not there" are different answers, and
    only one of them is a reason to write."""
    url, state = doubled_plant

    class Deaf(assist_runs.Plant):
        def read(self, path, **params):
            if path == "/masterdata/materials":
                raise assist_runs.LiveRefused("GET /masterdata/materials -> 500 boom")
            return super().read(path, **params)

    plant = Deaf(url)
    plant.sign_in("ADMIN", "a-password")
    seeded = assist_seed.seed(plant)
    plant.close()

    assert set(seeded["refused"]) == {"material:RAW-SUGAR", "material:RAW-FLAVOR",
                                      "material:FG-COLA"}
    for why in seeded["refused"].values():
        assert "would not say whether it has this" in why and "500 boom" in why
    assert not any(path == "/masterdata/materials" for path in state["posted"])


def test_the_person_seeding_master_data_never_reaches_the_plant_as_the_agent():
    """Decision 0035, held by the import graph. The AGENT account may not define
    master data; this module is what a person does instead, so it must not be
    able to reach the tool layer or the arrangement that uses it."""
    tree = ast.parse(pathlib.Path(assist_seed.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for alias in node.names}
    assert not [name for name in imported if "mcp" in name], imported
    assert not [name for name in imported if "assist_fixtures" in name], imported


def test_a_live_run_seeds_the_master_data_before_it_arranges_anything(doubled_plant):
    """Not a preference either. `WO-EVAL-1` is an order for FG-COLA routed over
    MIX01, and the non-conformance is opened by a brix check failing against a
    specification - all three have to be there first."""
    url, state = doubled_plant
    plant = _signed_in(url)
    assist_runs.run_live(SUITE[:1], plant, seeding=True, arranging=True)
    plant.close()

    master = set(_Double.WANTS)
    posted = [path for path in state["posted"] if path != "/auth/login"]
    seeded_at = [i for i, path in enumerate(posted) if path in master]
    other = [i for i, path in enumerate(posted) if path not in master]
    assert seeded_at, "nothing was seeded"
    assert not other or max(seeded_at) < min(other), posted


def test_a_live_run_that_was_not_asked_to_seed_leaves_the_master_data_alone(doubled_plant):
    url, state = doubled_plant
    plant = _signed_in(url)
    _outcomes, run = assist_runs.run_live(SUITE[:1], plant, arranging=False)
    plant.close()
    assert run["seeding"] is False and run["seeded"] == {}
    assert state["made"] == []


def test_the_result_file_says_what_master_data_arrived_and_that_nothing_removes_it():
    page = assist_eval.report((), mode="live", run={
        "seeding": True,
        "seeded": {"made": ["material:FG-COLA"], "already": ["machine:MIX01"],
                   "refused": {"lot:LOT-SUGAR-001": "this plant asks for "
                                                    "'production.consume'"}}})
    assert "### The master data, as the person signed in" in page
    assert "`material:FG-COLA`" in page and "`machine:MIX01`" in page
    assert "production.consume" in page
    assert "no `DELETE` for equipment, materials, routings" in page
    assert "rebuilt or restored from a backup" in page
    assert "decision 0035" in page


def test_a_result_file_says_plainly_when_master_data_was_never_asked_for():
    """So an operator reading a run against their plant can see that its master
    data was theirs, not a suite's."""
    page = assist_eval.report((), mode="live", run={})
    assert "`--seed-masterdata` was not given" in page


def test_a_scripted_run_cannot_be_asked_to_seed_master_data():
    """Its plant is built from the demo pack in-process and already has all of
    it, so a person who typed this meant it for a real plant."""
    result = CliRunner().invoke(app, ["assist", "eval", "--scripted", "--seed-masterdata"])
    assert result.exit_code == 2
    assert "--seed-masterdata is for --live" in result.stdout


# -------------------------------- the command's own refusals about accounts

def _cli(monkeypatch, *args):
    """`fsmes assist eval` as a person types it, with a key in the environment
    so the run gets as far as the accounts."""
    monkeypatch.setenv(assist_runs.KEY, "not called: every run below stops first")
    for role in ("ADMIN", "OPERATOR", "SUPERVISOR", "AGENT"):
        monkeypatch.delenv(f"{cli_module.PASSWORD_VAR}_{role}", raising=False)
    return CliRunner().invoke(app, ["assist", "eval", "--live", "--plant",
                                    "http://127.0.0.1:1", "--user", "ADMIN",
                                    "--password", "a-password", *args])


def test_a_live_run_with_no_account_named_refuses_rather_than_asking_as_one_person(
        monkeypatch):
    """The 2026-09-27 harness signed in once and asked every role's questions as
    that person. Naming nobody now stops the run instead, before any money."""
    out = _cli(monkeypatch)
    assert out.exit_code == 2
    assert "no role has anybody to ask as" in out.output
    assert "--account admin=ADMIN" in out.output


def test_an_account_the_command_cannot_read_is_said_before_anything_is_asked(
        monkeypatch):
    out = _cli(monkeypatch, "--account", "operator=SCOTT")
    assert out.exit_code == 2
    assert "No password for SCOTT (operator)" in out.output
    assert f"{cli_module.PASSWORD_VAR}_OPERATOR" in out.output


def test_an_account_written_the_wrong_way_round_is_refused_with_the_shape_it_wants(
        monkeypatch):
    out = _cli(monkeypatch, "--account", "SCOTT")
    assert out.exit_code == 2
    assert "<role>=<code>" in out.output


def test_each_role_reads_its_password_from_its_own_variable(monkeypatch):
    monkeypatch.setenv(f"{cli_module.PASSWORD_VAR}_OPERATOR", "an-operators-password")
    assert cli_module._accounts(["operator=SCOTT"], user="ADMIN",
                                password="an-administrators-password") == \
        {"operator": ("SCOTT", "an-operators-password")}


def test_the_account_that_arranges_the_plant_does_not_need_its_password_twice(
        monkeypatch):
    """`--user ADMIN --account admin=ADMIN` is one account, and nobody should
    have to put one password in two variables."""
    monkeypatch.delenv(f"{cli_module.PASSWORD_VAR}_ADMIN", raising=False)
    assert cli_module._accounts(["admin=ADMIN"], user="admin",
                                password="a-password") == \
        {"admin": ("ADMIN", "a-password")}


# ------------------------------------------------- the fixtures a case needs

def test_every_requires_line_names_a_kind_the_fixtures_know():
    """A typo in a `requires` line would otherwise be a case that quietly never
    runs again - the worst failure a ratchet can have."""
    assert not assist_fixtures.unknown_kinds(SUITE)


def test_the_suite_says_what_it_needs_on_the_plant():
    """Not every case needs anything - "what is running right now" needs a plant
    and nothing on it. Most do, and a suite where none did would mean the
    mechanism is wired up and unused."""
    with_needs = [case for case in SUITE if case.requires]
    assert len(with_needs) >= 30, f"only {len(with_needs)} case(s) say what they need"


def test_a_case_that_drafts_a_code_and_a_case_that_approves_one_never_name_the_same_code():
    """Drafting `changeover` needs the plant *not* to have one; approving a draft
    needs it to have one. A run cannot arrange both of the same code, so the
    drafts a run puts up carry the `EVAL-`/`eval_` prefix and the drafting cases
    keep the word a person would type."""
    drafts = {case.args.get("code") for case in SUITE
              if case.tool in ("draft_downtime_reason", "draft_nc_severity",
                               "draft_instruction", "draft_trigger")}
    arranged = {assist_fixtures.split(r)[1] for r in assist_fixtures.ARRANGES}
    assert not drafts & arranged, f"a run arranges a code a case drafts: {drafts & arranged}"


#: The tools a case uses to ask for a draft of the plant's own words.
DRAFTING = ("draft_downtime_reason", "draft_nc_severity", "draft_instruction",
            "draft_trigger")


def test_no_draft_a_run_puts_up_reads_like_the_draft_a_case_asks_for():
    """The 2026-09-27 collision, and its three siblings.

    Asked to "draft a cosmetic severity", the live model answered *"there's
    already a draft 'cosmetic' severity (code eval_cosmetic)"* - which was true,
    and was the arrangement the same run had just put there. The prefix kept the
    **codes** apart, which is all the sibling test above needs; a model reads the
    **words**. `eval_changeover` beside "draft a downtime reason changeover" and
    "Measuring brix" beside "draft an instruction WI-BRIX called Measuring brix"
    were the same trap waiting for the roles that had not been run live yet.

    So no code or name a drafting case asks for may appear inside a code or name
    a run puts up, either way round.
    """
    asked: list[tuple[str, str]] = []
    for case in SUITE:
        if case.tool not in DRAFTING:
            continue
        name = case.fills.get("name") or case.fills.get("title") or ""
        asked.append((str(case.args.get("code") or ""), str(name)))
    assert len(asked) >= 5, f"only {len(asked)} drafting case(s) to check against"

    for code, name in assist_fixtures.DRAFTS:
        ours = f"{code} {name}".casefold()
        for asked_code, asked_name in asked:
            for theirs in (asked_code, asked_name):
                if not theirs:
                    continue
                assert theirs.casefold() not in ours, \
                    f"a run puts up {code} ({name!r}), which reads like {theirs!r}"
                assert code.casefold() not in theirs.casefold(), \
                    f"a case asks for {theirs!r}, which reads like {code}"


def test_the_trigger_a_run_drafts_watches_something_no_case_asks_about():
    """A code and a name are not all a model compares. The trigger a run put up
    watched MIX01's temperature above 85, and the drafting case asks for a trigger
    on MIX01's temperature above 85 - the same rule, one code apart."""
    watched = {(str(case.fills.get("tag") or ""), str(case.args.get("threshold") or ""))
               for case in SUITE if case.tool == "draft_trigger"}
    assert watched, "no case drafts a trigger any more"
    ours = (assist_fixtures.TRIGGER_TAG, str(assist_fixtures.TRIGGER_THRESHOLD))
    assert ours not in watched
    assert assist_fixtures.TRIGGER_TAG not in {tag for tag, _ in watched}


def test_the_setting_a_run_writes_leaves_the_key_changeable():
    """Both halves of one fixture. The trail has to carry the ten Scott says he
    set, and the key has to read something else afterwards - otherwise "change
    the default reporting window to 10hrs" is answered *"already set to 10.0
    hours, nothing to change"*, which is what happened live on 2026-09-27."""
    asked_for = {case.args.get("value") for case in SUITE
                 if case.args.get("key") == "default_report_hours"}
    assert asked_for, "no case asks for the reporting window any more"
    for value in asked_for:
        assert not assist_fixtures.same_value(value, assist_fixtures.SETTING_RESTS_AT), \
            f"a case asks for {value!r} and the fixture leaves the key reading it"
    assert assist_fixtures.same_value(assist_fixtures.SETTING_VALUE, "10.0"), \
        "the case about the audit trail quotes 10.0 in Scott's own words"


def test_the_drafts_a_run_puts_up_say_they_are_ours():
    """A person looking at their plant's vocabulary should not have to guess
    which rows a faithfulness run left behind."""
    ours = [assist_fixtures.INSTRUCTION, assist_fixtures.TRIGGER,
            assist_fixtures.REASON, assist_fixtures.SEVERITY, assist_fixtures.ORDER]
    for code in ours:
        assert "EVAL" in code.upper(), code


def test_the_scripted_run_arranges_every_fixture_the_suite_asks_for(scored):
    """The whole point, measured: on the plant this suite is written about,
    everything a run may put there is there. A case that starts coming back not
    arranged here is a fixture that stopped being made.

    With one exception, and it is a fact about the plant rather than a gap here.
    A recommended setpoint change needs a tag the plant's own manifest declares
    writable with bounds - the first of the three guards between a recommendation
    and a PLC - and a seeded demo plant has no manifest at all. So the one case
    that needs a recommendation waiting is reported not arranged, with that
    sentence beside it, rather than asked on a plant where nothing is waiting.
    """
    unmade = {o.case.id: o.missing for o in scored if not o.arranged}
    assert set(unmade) == {"admin-approves-an-adjustment"}, unmade
    assert unmade["admin-approves-an-adjustment"] == ("adjustment:MIX01",)


def test_a_case_whose_fixture_is_missing_is_counted_apart_from_pass_and_fail():
    case = BY_ID["operator-books-good-and-scrap"]
    outcome = assist_eval.score(case, assist_eval.Turn(kind="not_asked", from_model=True,
                                                       say="not asked"),
                                ("machine:MIX01",))
    assert not outcome.arranged
    assert not outcome.counted          # out of the required number
    assert not outcome.passed           # and not a pass either
    counts = assist_eval.tally((outcome,))
    assert counts["required"] == 0 and counts["not_arranged"] == 1


def test_the_report_says_which_cases_were_not_arranged_and_why():
    case = BY_ID["operator-books-good-and-scrap"]
    outcome = assist_eval.score(case, assist_eval.Turn(kind="not_asked", say="not asked"),
                                ("machine:MIX01",))
    page = assist_eval.report((outcome,), mode="live", run={
        "why_not": {"machine:MIX01": assist_fixtures.why_not("machine:MIX01")}})
    assert "## Not arranged" in page
    assert "machine:MIX01" in page
    assert "master data is the plant's own" in page


# ------------------------------------------- the three loosened expectations

def test_the_hold_rules_are_the_same_four_rules_however_a_sentence_writes_them():
    """2026-09-26 live: "all four … are configured as hold rules", read out of
    `spc_chart`, scored as a failure because the case demanded `plant_settings`
    and the literal `1,2,3,4`."""
    case = BY_ID["scott-asks-which-spc-rules-are-on-hold"]
    turn = assist_eval.Turn(
        kind="reply", from_model=True,
        say="All four of this plant's SPC rules are configured as hold rules.",
        reads=("spc_chart",))
    assert assist_eval.score(case, turn).passed


def test_the_hold_rules_answered_off_the_settings_page_still_pass():
    case = BY_ID["scott-asks-which-spc-rules-are-on-hold"]
    turn = assist_eval.Turn(kind="reply", from_model=True,
                            say="hold_rules is 1,2,3,4 — all of them raise a hold.",
                            reads=("plant_settings",))
    assert assist_eval.score(case, turn).passed


def test_the_hold_rules_answered_from_a_procedure_document_still_do_not_pass():
    """Loosening the rendering is not loosening the rule: an answer that never
    looked, and sends somebody to a document, is the failure this case is for."""
    case = BY_ID["scott-asks-which-spc-rules-are-on-hold"]
    turn = assist_eval.Turn(kind="reply", from_model=True,
                            say="Please consult the quality procedure for the hold rules.")
    outcome = assist_eval.score(case, turn)
    assert not outcome.passed
    assert any("read none of" in why for why in outcome.why)


def test_taking_somebody_to_the_form_where_scrap_is_booked_answers_take_me_to_scrap():
    case = BY_ID["scott-asks-to-be-taken-to-scrap"]
    turn = assist_eval.Turn(kind="guide", guide_id="book-production", from_model=True,
                            say="Here is where scrap is booked.")
    assert assist_eval.score(case, turn).passed


def test_a_walk_this_request_is_not_about_still_does_not_answer_it():
    case = BY_ID["scott-asks-to-be-taken-to-scrap"]
    turn = assist_eval.Turn(kind="guide", guide_id="find-instruction", from_model=True)
    assert not assist_eval.score(case, turn).passed


def test_the_calendar_case_asks_for_a_kind_the_api_takes():
    from fsmes.domain import ExceptionKind

    case = BY_ID["admin-adds-a-calendar-exception"]
    assert case.args["kind"] in {k.value for k in ExceptionKind}


# ------------------------- the live arrangement, over a socket, twice running

class _OverASocket(BaseHTTPRequestHandler):
    """A real plant on a real port.

    The canned double above proves the money and the declining. This one proves
    the arrangement, and a canned reply would prove nothing about it: the whole
    claim is that a live run builds its fixture *through the product's own API*,
    so what is behind this socket is the product's own API - the same in-process
    app a scripted run uses, reached over TCP instead of in memory.

    Every request is forwarded whole, headers included, because the headers are
    half the point: the bearer token the AGENT account signs in with, and the
    `X-On-Behalf-Of` that puts a person's name on every audit row.
    """

    app: typing.ClassVar = None
    calls: typing.ClassVar[list] = []

    def log_message(self, *args):
        pass

    def _forward(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        pass_on = {name: value for name, value in self.headers.items()
                   if name.lower() in ("authorization", "content-type",
                                       "x-on-behalf-of", "idempotency-key")}
        self.calls.append((method, self.path.split("?")[0]))
        reply = self.app.request(method, self.path, content=body, headers=pass_on)
        payload = reply.content
        self.send_response(reply.status_code)
        self.send_header("Content-Type",
                         reply.headers.get("content-type", "application/json"))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

    def do_PATCH(self):
        self._forward("PATCH")


@pytest.fixture()
def plant_on_a_port():
    """The seeded plant, served over TCP, with the tool layer dialling it."""
    from fsmes import mcp_server

    with assist_runs.scripted_plant() as plant:
        _OverASocket.app = plant.client
        _OverASocket.calls = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OverASocket)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            yield url, _OverASocket.calls
        finally:
            server.shutdown()
            server.server_close()
            mcp_server._local.pop(assist_runs.LIVE_PLANT, None)
            client = mcp_server._clients.pop(assist_runs.LIVE_PLANT, None)
            if client is not None:
                client.close()


@pytest.fixture()
def bare_plant_on_a_port():
    """A plant with accounts and no master data, served over TCP.

    What `--seed-masterdata` exists for, and the only fixture that can prove it:
    a plant built from the demo pack already has every one of the ten codes, so
    it can show you "already there" and never the request that creates one. Here
    the real routers answer - the real paths, the real payload shapes, the real
    capability names - which a canned double cannot.
    """
    from fsmes import mcp_server

    with assist_runs.scripted_plant(seeded=False) as plant:
        _OverASocket.app = plant.client
        _OverASocket.calls = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OverASocket)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            yield url, _OverASocket.calls
        finally:
            server.shutdown()
            server.server_close()
            mcp_server._local.pop(assist_runs.LIVE_PLANT, None)
            client = mcp_server._clients.pop(assist_runs.LIVE_PLANT, None)
            if client is not None:
                client.close()


def test_seeding_a_real_empty_plant_leaves_it_holding_all_ten_codes(bare_plant_on_a_port):
    """The real routers, over a real socket, on a plant that had none of it: ten
    `POST`s, ten rows, and the plant says so when it is asked afterwards."""
    url, calls = bare_plant_on_a_port
    plant = assist_runs.Plant(url)
    plant.sign_in("ADMIN", "a-long-enough-password")
    seeded = assist_runs.seed_live(plant)

    assert seeded["refused"] == {}, seeded["refused"]
    assert seeded["made"] == list(assist_seed.SEEDS)
    written = [path for method, path in calls if method == "POST" and path != "/auth/login"]
    assert written == ["/masterdata/equipment", "/masterdata/equipment",
                       "/masterdata/equipment", "/masterdata/materials",
                       "/masterdata/materials", "/masterdata/materials",
                       "/masterdata/routings", "/quality/specs",
                       "/execution/lots", "/execution/lots"]
    for requirement in assist_seed.SEEDS:
        kind, code = assist_seed.split(requirement)
        assert assist_seed.RECIPES[kind].there(plant, code), requirement
    plant.close()


def test_what_lands_on_a_real_plant_is_the_demo_plant_and_not_a_near_miss(
        bare_plant_on_a_port):
    """"The code is there" is not the same claim as "the row says what the demo
    pack says". A field named wrong is accepted by a router with a default behind
    it - `FG-COLA` would arrive as a *raw* material, `MIX01` with no ideal cycle -
    and every presence check in this file would still pass. So the rows are read
    back off the plant and compared, field by field."""
    url, _calls = bare_plant_on_a_port
    plant = assist_runs.Plant(url)
    plant.sign_in("ADMIN", "a-long-enough-password")
    assist_runs.seed_live(plant)

    def one(path, **params):
        rows = plant.read(path, **params)
        rows = rows.get("items") if isinstance(rows, dict) else rows
        assert len(rows) == 1, (path, params, rows)
        return rows[0]

    cola = one("/masterdata/materials", q="FG-COLA")
    assert (cola["name"], cola["unit"], cola["type"]) == ("Cola Syrup 1L", "ea", "finished")
    sugar = one("/masterdata/materials", q="RAW-SUGAR")
    assert (sugar["unit"], sugar["type"]) == ("kg", "raw")
    flavor = one("/masterdata/materials", q="RAW-FLAVOR")
    assert (flavor["unit"], flavor["type"]) == ("l", "raw")

    line = one("/masterdata/equipment", q="LINE1")
    assert (line["level"], line["parent"]) == ("work_center", None)
    mixer = one("/masterdata/equipment", q="MIX01")
    assert (mixer["level"], mixer["parent"], mixer["ideal_cycle_seconds"]) == (
        "work_unit", "LINE1", 4.0)
    packer = one("/masterdata/equipment", q="PACK01")
    assert (packer["parent"], packer["ideal_cycle_seconds"]) == ("LINE1", 3.0)

    routing = one("/masterdata/routings", q="RT-COLA")
    assert routing["material"] == "FG-COLA"
    assert routing["operations"] == [{"seq": 10, "name": "Mix", "equipment": "MIX01"},
                                     {"seq": 20, "name": "Pack", "equipment": "PACK01"}]

    spec = one("/quality/specs", material="FG-COLA", characteristic="brix")
    assert (spec["unit"], spec["min_value"], spec["max_value"]) == ("°Bx", 9.5, 11.5)

    assert one("/execution/lots", q="LOT-SUGAR-001")["quantity"] == 500
    assert one("/execution/lots", q="LOT-FLAVOR-001")["quantity"] == 100
    plant.close()


def test_the_routing_nobody_asked_for_is_what_lets_the_agents_order_exist(
        bare_plant_on_a_port):
    """`routing:RT-COLA` is on the seeding list and no case names it. This is the
    reason written beside it, measured against the real routers: with the machines
    and the materials there and no routing, `workorders.create` refuses
    `WO-EVAL-1` outright, and the agent's whole arrangement stops at its first
    fixture."""
    url, _calls = bare_plant_on_a_port
    plant = assist_runs.Plant(url)
    plant.sign_in("ADMIN", "a-long-enough-password")

    bodies = assist_seed.demo_master_data()
    for requirement in assist_seed.SEEDS:
        if assist_seed.split(requirement)[0] in ("machine", "material"):
            plant.write(assist_seed.RECIPES[assist_seed.split(requirement)[0]].where,
                        bodies[requirement])

    before = assist_runs.arrange_live(url, on_behalf_of="ADMIN")
    assert "order:WO-EVAL-1" in before["refused"], before
    assert "routing" in before["refused"]["order:WO-EVAL-1"].lower()

    assist_runs.seed_live(plant)
    after = assist_runs.arrange_live(url, on_behalf_of="ADMIN")
    assert not after["refused"], after["refused"]
    assert set(after["made"]) | set(after["already"]) == set(assist_runs.ARRANGES)
    plant.close()


def test_seeding_a_plant_built_from_the_demo_pack_finds_every_code_already_there(
        plant_on_a_port):
    """The other end of the same idempotency, against the real routers: the demo
    pack's own plant already has all ten, so nothing is written to it at all."""
    from fsmes.config import get_settings

    url, calls = plant_on_a_port
    plant = assist_runs.Plant(url)
    plant.sign_in("ADMIN", get_settings().admin_password)
    calls.clear()
    seeded = assist_runs.seed_live(plant)
    plant.close()

    assert seeded["already"] == list(assist_seed.SEEDS)
    assert seeded["made"] == [] and seeded["refused"] == {}
    assert not [path for method, path in calls
                if method == "POST" and path != "/auth/login"]


def test_a_live_run_puts_the_suites_fixtures_on_a_real_plant_over_its_own_http_api(
        plant_on_a_port):
    url, calls = plant_on_a_port
    out = assist_runs.arrange_live(url, on_behalf_of="ADMIN")

    # Everything but the recommendation, which needs a writable setpoint this
    # plant's manifest does not declare - reported in the plant's own terms
    # rather than raised, which is what lets a run say what it could not do.
    assert set(out["refused"]) == {f"adjustment:{assist_fixtures.MACHINE}"}
    assert "no writable setpoint" in out["refused"][f"adjustment:{assist_fixtures.MACHINE}"]
    assert set(out["made"]) == set(assist_runs.ARRANGES) - set(out["refused"])
    assert any(method == "POST" for method, _ in calls), "nothing was written over the wire"

    # And the fixtures really are on the plant, asked over the same socket.
    view = assist_runs.live_view(url)
    for requirement in assist_runs.ARRANGES:
        if requirement in out["refused"]:
            continue
        assert view.has(requirement), requirement


def test_arranging_a_plant_that_is_already_arranged_creates_nothing_twice(
        plant_on_a_port):
    """Idempotent, which is what makes it safe to point at a plant more than
    once. The two fixtures the plant numbers itself - the non-conformance and
    the corrective order - are the ones that would otherwise pile up, so they
    are counted rather than taken on trust."""
    from fsmes import mcp_server

    url, calls = plant_on_a_port
    first = assist_runs.arrange_live(url)
    assert first["made"]

    before = mcp_server.quality(assist_runs.LIVE_PLANT)["checks"]["checks"]
    work_before = len(mcp_server.maintenance_work(
        assist_runs.LIVE_PLANT, open_only=False)["work"])
    calls.clear()

    second = assist_runs.arrange_live(url)
    assert second["made"] == [], f"a second run made {second['made']}"
    assert set(second["already"]) == set(assist_runs.ARRANGES) - set(first["refused"])
    assert set(second["refused"]) == set(first["refused"])
    assert not [path for method, path in calls if method == "POST"
                and path != "/auth/login"], "a second run wrote to the plant"

    assert mcp_server.quality(assist_runs.LIVE_PLANT)["checks"]["checks"] == before
    assert len(mcp_server.maintenance_work(
        assist_runs.LIVE_PLANT, open_only=False)["work"]) == work_before


def test_what_a_live_run_leaves_on_a_plant_is_audited_with_the_person_it_acted_for(
        plant_on_a_port):
    """The arrangement is not a back door. It goes through the API as the AGENT
    account, and every row it writes says which person it was acting for - which
    is the whole reason it may be pointed at a plant somebody uses."""
    from fsmes import mcp_server

    url, _calls = plant_on_a_port
    assist_runs.arrange_live(url, on_behalf_of="ADMIN")
    trail = mcp_server.audit(assist_runs.LIVE_PLANT, limit=50)["audit"]
    ours = [row for row in trail if row.get("on_behalf_of") == "ADMIN"]
    assert ours, "nothing the arrangement wrote names the person it acted for"
    assert {row["actor"] for row in ours} == {"AGENT"}


def test_no_arrange_leaves_the_plant_alone_and_says_which_cases_it_could_not_ask(
        plant_on_a_port):
    """`--no-arrange` against a plant nobody has arranged: the cases that name a
    work order or a draft are reported not arranged, and no row is written."""
    url, calls = plant_on_a_port
    view = assist_runs.live_view(url)
    unmet = assist_runs.missing(SUITE, view.plant, view=view)
    # Not there because nobody arranged it...
    assert unmet["operator-holds-an-order"] == ("order:WO-EVAL-1",)
    assert "instruction:WI-EVAL-1" in unmet["admin-approves-a-document"]
    # ...and there because it is the plant's own master data, which a run never
    # arranges and never needs to.
    assert "operator-records-a-check" not in unmet
    assert not [path for method, path in calls
                if method == "POST" and path != "/auth/login"]


def test_a_case_the_plant_already_has_a_code_for_is_not_arranged_rather_than_failed(
        plant_on_a_port):
    """The 2026-09-26 failure in one assertion. Draft `changeover` on a plant
    whose vocabulary already holds one is not a question, and the model saying
    "it is already at revision 1, approved and in force" was right."""
    from fsmes import mcp_server

    url, _calls = plant_on_a_port
    assist_runs.live_view(url)              # point the tool layer at this socket
    mcp_server.draft_downtime_reason(assist_runs.LIVE_PLANT, code="changeover",
                                     name="Changeover", dry_run=False,
                                     on_behalf_of="ADMIN")
    view = assist_runs.live_view(url)
    view.seen.clear()
    unmet = assist_runs.missing(SUITE, view.plant, view=view)
    assert unmet.get("admin-drafts-a-downtime-reason") == ("no reason:changeover",)
