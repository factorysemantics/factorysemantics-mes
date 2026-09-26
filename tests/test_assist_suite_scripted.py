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

import json
import threading
import typing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fsmes.lab import assist_eval as assist_runs
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
    quoted is no longer the number."""
    passing = [o.case for o in scored if not o.counted and o.passed]
    assert not passing, ("these are marked not_yet and pass now; take the mark off and "
                         "say so: " + ", ".join(f"{c.id} (was waiting on {c.handoff})"
                                                for c in passing))


def test_the_run_says_a_pass_rate_for_every_role(scored):
    counts = assist_eval.tally(scored)
    assert set(counts["roles"]) == {"operator", "supervisor", "admin", "agent"}
    assert counts["required"] + counts["not_yet"] == len(SUITE)
    assert counts["total"] == len(SUITE)


def test_the_report_names_each_failure_and_what_it_did_instead(scored):
    page = assist_eval.report(scored, mode="scripted")
    assert "## Per role" in page and "| operator |" in page
    assert "## Marked `not_yet`" in page
    for outcome in scored:
        if not outcome.counted:
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
    and the result file are all proven with no key and no money."""

    state: typing.ClassVar[dict] = {}

    def log_message(self, *args):    # keep pytest output readable
        pass

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send({"status": "ok", "plant": "doubled"})
        if self.path == "/assist/agent/status":
            return self._send({"available": True, "reason": "ok", "model": "test-model",
                               "spend_usd": self.state["spend"], "cap_usd": 10.0,
                               "tokens_this_month": {"input": self.state["asks"] * 100,
                                                     "output": self.state["asks"] * 10}})
        return self._send({"detail": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/auth/login":
            self.state["signed_in_as"] = body.get("code")
            return self._send({"token": "a-token"})
        if self.path == "/assist/agent":
            self.state["asks"] += 1
            self.state["spend"] = round(self.state["spend"] + 0.40, 6)
            self.state["asked"].append(body.get("message"))
            return self._send(self.state["replies"].get(
                body.get("message"), {"kind": "reply", "session": "s1", "say": "I do not know.",
                                      "transcript": []}))
        if self.path == "/assist/agent/decline":
            self.state["declined"].append(body.get("proposal"))
            return self._send({"kind": "reply", "session": "s1", "say": "Left alone."})
        return self._send({"detail": "not found"}, 404)


@pytest.fixture()
def doubled_plant():
    _Double.state = {"spend": 0.0, "asks": 0, "asked": [], "declined": [], "replies": {}}
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
    outcomes, run = assist_runs.run_live((case,), plant)
    plant.close()

    assert state["signed_in_as"] == "ADMIN"
    assert state["asked"] == [case.request]
    assert outcomes[0].passed, outcomes[0].why
    assert outcomes[0].turn.from_model is True
    assert run["model"] == "test-model"
    assert run["usd"] == pytest.approx(0.40)
    assert run["tokens"] == {"input": 100, "output": 10}


def test_a_live_run_declines_every_proposal_it_opens(doubled_plant):
    """Scoring a plant must not change one."""
    url, state = doubled_plant
    case = BY_ID["operator-books-good-and-scrap"]
    state["replies"][case.request] = {
        "kind": "proposals", "session": "s1", "say": "",
        "proposals": [{"id": "p9", "tool": "book_output",
                       "args": {"equipment": "MIX01", "good": 600, "scrap": 12,
                                "order": "WO-EVAL-1"},
                       "surface": {"steps": [{"anchor": "report-good"}]}}],
    }
    plant = assist_runs.Plant(url)
    plant.sign_in("SCOTT", "a-password")
    assist_runs.run_live((case,), plant)
    plant.close()
    assert state["declined"] == ["p9"]


def test_a_live_run_stops_at_the_budget_it_was_given_and_says_what_it_did_not_run(doubled_plant):
    url, _state = doubled_plant
    cases = tuple(c for c in SUITE if c.role == "operator")[:6]
    plant = assist_runs.Plant(url)
    plant.sign_in("SCOTT", "a-password")
    outcomes, run = assist_runs.run_live(cases, plant, max_usd=1.00)
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
        assist_runs.run_live(SUITE[:1], plant)
    plant.close()


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
    outcomes, run = assist_runs.run_live((case,), plant)
    plant.close()
    page = assist_eval.report(outcomes, mode="live", model=run["model"], plant="doubled",
                              plant_commit="abc1234", run=run)
    written = assist_eval.write_report(page, directory=tmp_path)
    text = written.read_text(encoding="utf-8")
    assert "test-model" in text and "doubled" in text and "abc1234" in text
    assert "$0.4000" in text and "input 100" in text
    assert "Assistant faithfulness" in text
