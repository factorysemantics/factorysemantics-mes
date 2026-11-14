"""Browser tests share one way of serving a plant.

Thirty-five test files each carried their own sixty lines that seed a demo
plant into a file database, start uvicorn on a free loopback port, wait for
`/health` and sign in as ADMIN - the same lines, copied, drifting a little
each time. `tests/conftest.py` now holds one copy: the `live_server` fixture
(the demo plant, served once for the run) and `serve_plant` (a plant with
rows of the test's own). A new browser test uses one of them.

Read from the source, like `test_core_purity.py`: a test file that imports
`uvicorn` is starting its own server. The files below did so on 2026-11-14
and may until each is moved onto the fixture; moving them is its own piece
of work, and each one moved comes out of this list.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: Test files that start their own server today, by path under `tests/`.
#: Shrinks; never grows.
ALLOWED: frozenset[str] = frozenset({
    "test_a_chart_draws_only_what_the_api_measured.py",
    "test_a_plant_books_while_its_dashboard_is_read.py",
    "test_a_walk_waits_for_a_control_the_page_is_still_drawing.py",
    "test_all_machines_on_shift_analysis_in_a_browser.py",
    "test_an_orders_work_instructions_link_opens_the_procedure_for_what_it_makes.py",
    "test_auth.py",
    "test_clicking_a_block_on_the_state_timeline_opens_the_records_behind_it.py",
    "test_clicking_a_lot_on_the_spc_chart_opens_which_defects.py",
    "test_clicking_a_point_on_the_spc_chart_opens_the_records_behind_it.py",
    "test_clicking_a_point_on_the_trend_graph_opens_the_records_behind_it.py",
    "test_clicking_a_sample_on_the_spc_chart_opens_the_five_bottles.py",
    "test_every_export_button_downloads_the_list_it_sits_on.py",
    "test_every_screen_header_dot_says_live.py",
    "test_every_show_me_lands_on_the_real_control.py",
    "test_the_3d_view_draws_the_plants_own_model_and_keeps_its_shape_when_one_fails.py",
    "test_the_3d_view_fits_the_whole_line_with_labels_you_can_read.py",
    "test_the_ai_screen_shows_a_conversation_and_gates_itself.py",
    "test_the_ai_tab_explores_and_draws_what_it_read.py",
    "test_the_ai_tab_says_what_the_assistant_costs_without_local_ai.py",
    "test_the_chart_comes_into_the_chat_and_stays_clickable.py",
    "test_the_dashboard_says_which_node_answered_and_who_reads_the_machines.py",
    "test_the_energy_panel_is_drawn_only_on_a_plant_that_reports_power.py",
    "test_the_export_button_downloads_the_range_the_history_shows.py",
    "test_the_floors_book_output_form_says_what_the_booking_moved.py",
    "test_the_gauges_screen_shows_the_two_scales_and_when_each_was_calibrated.py",
    "test_the_panel_can_ask_the_other_agent.py",
    "test_the_panel_keeps_one_brain_through_a_failed_turn.py",
    "test_the_parts_mix_panel_and_the_last_play_window_in_a_browser.py",
    "test_the_screens_that_show_a_characteristic_inspected_five_at_a_time.py",
    "test_the_station_redraws_only_what_changed.py",
    "test_the_station_says_when_no_order_is_open_on_the_plant.py",
    "test_the_supervisors_screen_reads_the_shift_and_writes_a_rule.py",
    "test_the_trace_page_lists_the_orders_that_drew_on_a_lot.py",
    "test_ui_oee_above_rated.py",
})

MESSAGE = "use the `live_server` fixture in `tests/conftest.py`"


def _starts_its_own_server(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] == "uvicorn" for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "uvicorn":
            return True
    return False


def _test_files() -> list[Path]:
    return sorted(path for path in TESTS.rglob("*.py") if path != TESTS / "conftest.py")


def test_no_new_test_file_starts_its_own_server():
    offenders = [path.relative_to(TESTS).as_posix() for path in _test_files()
                 if _starts_its_own_server(path)]
    new = sorted(set(offenders) - ALLOWED)
    assert not new, f"{new} start their own server - {MESSAGE}"


def test_a_file_moved_onto_the_shared_server_leaves_the_list():
    """The list is what is left to move, so a file that no longer starts a
    server, or no longer exists, comes out of it."""
    still = {path.relative_to(TESTS).as_posix() for path in _test_files()
             if _starts_its_own_server(path)}
    done = sorted(ALLOWED - still)
    assert not done, f"no longer start their own server; take them out of ALLOWED: {done}"


def test_the_shared_server_is_the_one_in_conftest():
    """The fence points at a fixture; the fixture has to be there."""
    assert _starts_its_own_server(TESTS / "conftest.py")
    source = (TESTS / "conftest.py").read_text(encoding="utf-8")
    assert "def live_server(" in source
