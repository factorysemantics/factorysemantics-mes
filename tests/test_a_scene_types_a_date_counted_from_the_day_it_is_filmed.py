"""A scene types a date counted from the day it is filmed.

A due date typed on camera has to be a day still ahead when the camera rolls,
and a scene file is written on one day and filmed on another. So a `fill` may
say `{today+2}`, and the recorder hands the stage that day's date as a date
field takes it. No browser: the scene is walked the way the recorder walks it.
"""

from __future__ import annotations

import datetime


def test_today_plus_or_minus_a_count_of_days_becomes_that_days_date_as_a_date_field_takes_it(runner):
    friday = datetime.date(2030, 3, 1)

    assert runner.days_from_today("{today}", friday) == "2030-03-01"
    assert runner.days_from_today("{today+2}", friday) == "2030-03-03"
    assert runner.days_from_today("{today-1}", friday) == "2030-02-28"
    assert runner.days_from_today("due {today+30} please", friday) == "due 2030-03-31 please"


def test_text_without_the_token_is_typed_exactly_as_written(runner):
    for text in ("WO-FORK1-0300", "11000", "{tomorrow}", "today+2", ""):
        assert runner.days_from_today(text) == text


def test_the_recorder_hands_the_stage_the_date_and_never_the_token(runner):
    scene = {"step": [{"do": "fill", "selector": "#no-due", "text": "{today+2}"}]}
    typed: list[str] = []

    class Stage:
        def fill(self, selector, text):
            typed.append(text)

    runner.play(scene, Stage())

    assert typed == [(datetime.date.today() + datetime.timedelta(days=2)).isoformat()]
