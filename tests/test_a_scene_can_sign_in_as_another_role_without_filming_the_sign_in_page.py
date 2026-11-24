"""A scene can sign in as another role part way through, without filming the sign-in page.

Class 4.2 is about who may do what to a non-conformance: an operator raises
one and a supervisor decides it. The recorder signed in once, before the
first frame, so a take could only ever be one person. `sign_in` signs the
browser in as another user off camera and loads the page it is on again, so
the header shows the new name and role; the product's sign-in page, which
prints the demo accounts' hint, is never on camera. No plant and no browser
here.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def write_scene(tmp_path: Path, steps: str, top: str = "") -> Path:
    path = tmp_path / "scene.toml"
    path.write_text('name = "roles"\nbase = "http://127.0.0.1:8040"\n' + top + steps, encoding="utf-8")
    return path


def test_sign_in_is_a_word_a_step_can_be_and_its_marks_are_page_loads(runner):
    assert "sign_in" in runner.VERBS
    assert runner.KINDS["sign_in"] == "goto"


def test_the_stage_is_handed_the_user_and_the_password_the_scene_names(runner, tmp_path):
    scene = runner.load_scene(write_scene(tmp_path, '''
[[step]]
do = "sign_in"
user = "WALK-SUP"
password = "walk-lab-only"
settle = 1.5
'''))
    handed = []

    class Stage:
        def sign_in(self, user, password, settle):
            handed.append((user, password, settle))

    runner.play(scene, Stage())

    assert handed == [("WALK-SUP", "walk-lab-only", 1.5)]


def test_a_sign_in_without_a_password_uses_the_scenes_own(runner, tmp_path):
    scene = runner.load_scene(write_scene(
        tmp_path, '[[step]]\ndo = "sign_in"\nuser = "WALK-SUP"\n',
        top='user = "WALK-OP"\npassword = "walk-lab-only"\n'))

    (step,) = scene["step"]
    assert step["password"] == "walk-lab-only"
    assert step["settle"] == runner.SETTLE_SECONDS


def test_a_sign_in_without_a_user_is_refused_before_filming(runner, tmp_path):
    with pytest.raises(SystemExit, match="needs the `user` it signs in as"):
        runner.load_scene(write_scene(tmp_path, '[[step]]\ndo = "sign_in"\n'))


def test_a_sign_in_takes_a_page_loads_time_in_the_plan(runner, tmp_path):
    scene = runner.load_scene(write_scene(tmp_path, '[[step]]\ndo = "sign_in"\nuser = "WALK-SUP"\nsettle = 2.5\n'))

    assert runner.planned_seconds(scene) == pytest.approx(2.5)


def test_the_browser_stage_signs_in_by_the_pages_own_cookie_jar_and_loads_the_page_again(runner, tmp_path):
    calls = []

    class Answer:
        status = 200

    class Request:
        def post(self, url, data, headers):
            calls.append(("post", url, data))
            return Answer()

    class Context:
        request = Request()

    class Page:
        context = Context()
        url = "http://127.0.0.1:8040/dashboard/quality"

        def reload(self, wait_until):
            calls.append(("reload", wait_until))

        def wait_for_timeout(self, ms):
            calls.append(("wait", ms))

    stage = runner.BrowserStage(Page(), tmp_path, 0.0, base="http://127.0.0.1:8040")
    stage.sign_in("WALK-SUP", "walk-lab-only", 2.0)

    assert calls[0] == ("post", "http://127.0.0.1:8040/auth/login",
                        '{"code": "WALK-SUP", "password": "walk-lab-only"}')
    assert calls[1:] == [("reload", "load"), ("wait", 2000)]
    (mark,) = stage.marks
    assert mark["kind"] == "goto" and mark["user"] == "WALK-SUP" and mark["url"].endswith("/dashboard/quality")


def test_a_sign_in_the_plant_refuses_stops_the_take(runner, tmp_path):
    class Answer:
        status = 401

    class Request:
        def post(self, url, data, headers):
            return Answer()

    class Context:
        request = Request()

    class Page:
        context = Context()

    stage = runner.BrowserStage(Page(), tmp_path, 0.0, base="http://127.0.0.1:8040")
    with pytest.raises(SystemExit, match="could not sign in as WALK-SUP"):
        stage.sign_in("WALK-SUP", "wrong", 2.0)
