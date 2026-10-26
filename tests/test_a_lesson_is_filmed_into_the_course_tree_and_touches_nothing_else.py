"""A lesson is filmed into its folder of the course tree, and touches nothing else.

The class is laid out in `scripts/course/course.toml`. `scripts/record-scene.py`
films each lesson into `<course root>/NN-module/NN-lesson/` and keeps one
`course.json` at the top of the tree, which studio (the narration), the crew's
dashboard and, later, the website read. The lesson hand-off contract says what
that file holds (§2), what `lesson.json` holds (§3), and what a re-recording
may change: the lesson's own folder, and that lesson's `take`, `status`,
`duration_seconds` and `recorded` in course.json — never `voiced_take`, which
studio sets to the take it voiced, and never studio's own tree, which sits
beside ours.

None of this needs a browser or a plant: the take itself is replaced by one
that writes a folder the way a real take does, and everything around it — the
folder beside the old one, the move into place, the four keys — is the
runner's own code.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
COURSE = REPO / "scripts" / "course" / "course.toml"
SCENES = REPO / "scripts" / "scenes"


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene_course", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def course(runner):
    return runner.load_course(COURSE)


# ------------------------------------------------------------ the contract, §2


def problems_with_course_json(document: dict) -> list[str]:
    """Everything in a course.json that the contract's §2 does not allow. Empty is valid."""
    found: list[str] = []

    def need(where: str, holder: dict, key: str, kinds: tuple) -> None:
        if key not in holder:
            found.append(f"{where}: no `{key}`")
        elif not isinstance(holder[key], kinds) or (isinstance(holder[key], bool) and bool not in kinds):
            found.append(f"{where}: `{key}` is {type(holder[key]).__name__}")

    need("top", document, "contract", (int,))
    if document.get("contract") != 1:
        found.append("top: `contract` is not 1")
    need("top", document, "course", (dict,))
    need("top", document, "modules", (list,))
    head = document.get("course") or {}
    for key in ("slug", "title", "generated", "voice", "status_line"):
        need("course", head, key, (str,))
    need("course", head, "product_version", (str, type(None)))
    for m_number, module in enumerate(document.get("modules") or [], start=1):
        where = f"module {m_number}"
        need(where, module, "order", (int,))
        if module.get("order") != m_number:
            found.append(f"{where}: out of order")
        need(where, module, "slug", (str,))
        need(where, module, "title", (str,))
        need(where, module, "lessons", (list,))
        for l_number, lesson in enumerate(module.get("lessons") or [], start=1):
            at = f"lesson {m_number}.{l_number}"
            need(at, lesson, "order", (int,))
            if lesson.get("order") != l_number:
                found.append(f"{at}: out of order")
            for key in ("slug", "title", "path", "status"):
                need(at, lesson, key, (str,))
            if lesson.get("path") != f"{m_number:02d}-{module.get('slug')}/{l_number:02d}-{lesson.get('slug')}":
                found.append(f"{at}: `path` is not NN-module/NN-lesson")
            if lesson.get("status") not in ("planned", "recorded", "voiced", "published"):
                found.append(f"{at}: `status` {lesson.get('status')!r}")
            need(at, lesson, "duration_seconds", (int, float, type(None)))
            need(at, lesson, "take", (int, type(None)))
            need(at, lesson, "recorded", (str, type(None)))
            need(at, lesson, "voiced_take", (int, type(None)))
            need(at, lesson, "outcomes", (list,))
            if lesson.get("status") == "recorded" and not (lesson.get("take") and lesson.get("duration_seconds")):
                found.append(f"{at}: recorded with no take or no duration")
    return found


def test_course_json_written_from_course_toml_is_what_the_contract_describes(runner, course, tmp_path):
    root = tmp_path / "course"
    document = runner.write_course(course, root)

    on_disk = json.loads((root / "course.json").read_text(encoding="utf-8"))
    assert on_disk == document
    assert problems_with_course_json(on_disk) == []
    lessons = [lesson for module in on_disk["modules"] for lesson in module["lessons"]]
    assert len(on_disk["modules"]) == 5
    assert len(lessons) == 18
    # Nothing filmed yet: every lesson planned, no take, no version claimed.
    assert {lesson["status"] for lesson in lessons} == {"planned"}
    assert all(lesson["take"] is None and lesson["voiced_take"] is None for lesson in lessons)
    assert on_disk["course"]["product_version"] is None
    page = (root / "README.md").read_text(encoding="utf-8")
    assert "5 modules, 18 lessons: 18 planned, 0 recorded, 0 voiced, 0 published." in page
    assert "mpv --loop" in page


def test_a_course_json_that_breaks_the_contract_is_caught_by_the_check(runner, course, tmp_path):
    """The check above is worth only what it refuses."""
    document = runner.write_course(course, tmp_path / "course")
    broken = json.loads(json.dumps(document))
    broken["modules"][1]["lessons"][0]["status"] = "filmed"
    broken["modules"][0]["lessons"][2]["path"] = "01-what-an-mes-is-for/3-a-work-orders-day"
    del broken["modules"][4]["lessons"][2]["voiced_take"]

    assert len(problems_with_course_json(broken)) == 3


def test_scenes_02_and_04_are_the_teasers_of_lessons_2_1_and_1_3(runner, course):
    for scene_file, lesson_id, path in [
        ("02-spc-on-the-line.toml", "2.1", "02-quality-on-the-line/01-spc-the-chart-notices-first"),
        ("04-work-orders-and-routings.toml", "1.3", "01-what-an-mes-is-for/03-a-work-orders-day"),
    ]:
        scene = runner.load_scene(SCENES / scene_file)
        assert scene["lesson"] == lesson_id
        assert scene["take_is"] == "teaser"
        assert {step["part"] for step in scene["step"]} == {"hook"}
        _, lesson = runner.find_lesson(course, lesson_id)
        assert lesson["path"] == path


def test_a_course_that_skips_a_number_or_assumes_a_lesson_it_lacks_is_refused(runner, tmp_path):
    good = COURSE.read_text(encoding="utf-8")
    skipped = tmp_path / "skipped.toml"
    skipped.write_text(good.replace('order = 3\nslug = "a-work-orders-day"', 'order = 4\nslug = "a-work-orders-day"'),
                       encoding="utf-8")
    with pytest.raises(SystemExit, match="order = 4"):
        runner.load_course(skipped)
    assumes = tmp_path / "assumes.toml"
    assumes.write_text(good.replace('knows_before = ["1.1"]', 'knows_before = ["9.9"]'), encoding="utf-8")
    with pytest.raises(SystemExit, match=r"9\.9"):
        runner.load_course(assumes)


# ------------------------------------------------------- a re-record, end to end


def a_take_that_writes_a_folder(runner, seconds: float = 41.5, fail: bool = False):
    """Stands in for `take`: writes the folder the way a real teaser take does."""

    def take(scene, args, out, final, name, course, module, lesson):
        number = runner.next_take(final)
        (out / f"{name}.teaser.mp4").write_bytes(b"mp4 of take %d" % number)
        (out / f"{name}.gif").write_bytes(b"gif of take %d" % number)
        (out / f"{name}.script.txt").write_text("\n".join(runner.script_lines(scene)) + "\n", encoding="utf-8")
        (out / "marks.json").write_text("{}", encoding="utf-8")
        if fail:
            raise SystemExit("the badge is not in every frame of the GIF and the mp4; the take cannot be published")
        files = {"video": None, "teaser": f"{name}.gif", "teaser_video": f"{name}.teaser.mp4",
                 "script": f"{name}.script.txt", "marks": "marks.json", "stills": [], "slides": []}
        runner.write_json_atomically(out / "lesson.json", runner.lesson_document(course, module, lesson, scene, {
            "plant": "bottling", "base": scene["base"], "files": files, "duration_seconds": seconds,
            "take": number, "version": "0.4.2", "commit": "abc1234", "scene_file": args.scene.as_posix(),
            "recorded": f"2026-10-26T14:0{number}:00-05:00",
        }))

    return take


def film(runner, monkeypatch, root: Path, scene: str, take) -> None:
    monkeypatch.setattr(runner, "take", take)
    monkeypatch.setattr(runner.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(sys, "argv", ["record-scene.py", str(SCENES / scene), "--out", str(root)])
    runner.main()


def fingerprint(folder: Path) -> dict[str, str]:
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def test_a_re_record_bumps_the_take_in_both_files_and_changes_nothing_but_four_keys(runner, course, tmp_path,
                                                                                    monkeypatch):
    root = tmp_path / "gifs" / "course"
    studio = tmp_path / "studio" / "out"
    folder = root / "02-quality-on-the-line" / "01-spc-the-chart-notices-first"

    film(runner, monkeypatch, root, "02-spc-on-the-line.toml", a_take_that_writes_a_folder(runner))
    first = json.loads((folder / "lesson.json").read_text(encoding="utf-8"))
    assert first["lesson"]["take"] == 1
    assert first["files"]["video"] is None and first["files"]["teaser"] == "spc-the-chart-notices-first.gif"

    # Studio voices take 1, and keeps its own work in its own tree beside ours.
    document = json.loads((root / "course.json").read_text(encoding="utf-8"))
    entry = document["modules"][1]["lessons"][0]
    assert entry["take"] == 1 and entry["voiced_take"] is None
    entry["voiced_take"] = 1
    entry["status"] = "voiced"
    document["course"]["a key from a later contract"] = "kept"
    (root / "course.json").write_text(json.dumps(document, indent=1), encoding="utf-8")
    (studio / "02-quality-on-the-line").mkdir(parents=True)
    (studio / "02-quality-on-the-line" / "spc-the-chart-notices-first.voice.txt").write_text("Brian's lines\n",
                                                                                            encoding="utf-8")
    other = root / "01-what-an-mes-is-for" / "03-a-work-orders-day"
    other.mkdir(parents=True)
    (other / "keep.txt").write_text("another lesson's folder\n", encoding="utf-8")
    studio_before, other_before = fingerprint(studio), fingerprint(other)
    readme_before = (root / "README.md").read_bytes()
    before = json.loads((root / "course.json").read_text(encoding="utf-8"))

    film(runner, monkeypatch, root, "02-spc-on-the-line.toml", a_take_that_writes_a_folder(runner, seconds=43.0))

    after = json.loads((root / "course.json").read_text(encoding="utf-8"))
    second = json.loads((folder / "lesson.json").read_text(encoding="utf-8"))
    assert second["lesson"]["take"] == 2
    assert (folder / "spc-the-chart-notices-first.gif").read_bytes() == b"gif of take 2"
    entry_after = after["modules"][1]["lessons"][0]
    assert entry_after["take"] == 2, "the take is bumped in course.json as in lesson.json"
    assert entry_after["voiced_take"] == 1, "voiced_take is studio's; a re-record never touches it"
    assert entry_after["status"] == "voiced", "a status only moves forward"
    assert entry_after["recorded"] == second["filmed_from"]["recorded"]
    # Everything else in course.json, byte for byte as the reader left it.
    for key in runner.RECORD_KEYS:
        before["modules"][1]["lessons"][0].pop(key)
        entry_after.pop(key)
    assert after == before
    assert fingerprint(studio) == studio_before
    assert fingerprint(other) == other_before
    assert (root / "README.md").read_bytes() == readme_before
    # No folder of a take left lying beside the lesson.
    assert sorted(path.name for path in folder.parent.iterdir()) == [folder.name]


def test_a_take_that_fails_leaves_the_folder_and_course_json_as_they_were(runner, tmp_path, monkeypatch):
    root = tmp_path / "course"
    folder = root / "01-what-an-mes-is-for" / "03-a-work-orders-day"
    film(runner, monkeypatch, root, "04-work-orders-and-routings.toml", a_take_that_writes_a_folder(runner))
    folder_before = fingerprint(folder)
    course_before = (root / "course.json").read_bytes()

    with pytest.raises(SystemExit, match="badge"):
        film(runner, monkeypatch, root, "04-work-orders-and-routings.toml",
             a_take_that_writes_a_folder(runner, fail=True))

    assert fingerprint(folder) == folder_before
    assert (root / "course.json").read_bytes() == course_before
    assert sorted(path.name for path in folder.parent.iterdir()) == [folder.name]


def test_the_recorder_refuses_to_write_anywhere_inside_a_studio_folder(runner, tmp_path, monkeypatch):
    root = tmp_path / "studio" / "out"
    with pytest.raises(SystemExit, match="studio"):
        film(runner, monkeypatch, root, "02-spc-on-the-line.toml", a_take_that_writes_a_folder(runner))
    assert not root.exists()


def test_a_teaser_only_folder_leaves_its_lesson_planned_with_no_duration(runner, course, tmp_path, monkeypatch):
    """The teaser is filmed; the lesson is not. Studio, which voices `recorded`, has nothing to voice yet."""
    root = tmp_path / "course"
    film(runner, monkeypatch, root, "02-spc-on-the-line.toml", a_take_that_writes_a_folder(runner))
    document = runner.write_course(course, root)

    entry = document["modules"][1]["lessons"][0]
    assert (entry["status"], entry["take"], entry["duration_seconds"]) == ("planned", 1, None)
    assert document["course"]["product_version"] == "0.4.2"
    assert problems_with_course_json(document) == []


# ------------------------------------------------------------------ lesson.json


def test_lesson_json_says_the_lesson_the_files_the_reserved_corner_and_what_it_was_filmed_from(runner, course):
    module, lesson = runner.find_lesson(course, "1.2")
    scene = runner.load_scene(SCENES / "02-spc-on-the-line.toml")
    scene["take_is"] = "lesson"
    files = {"video": "never-invent-production.mp4", "teaser": "never-invent-production.gif",
             "script": "never-invent-production.script.txt", "marks": "marks.json",
             "stills": ["stills/01-the-rollup.png"], "slides": ["slides/01-the-word.png"]}
    document = runner.lesson_document(course, module, lesson, scene, {
        "plant": "bottling", "base": scene["base"], "files": files, "duration_seconds": 287.4, "take": 3,
        "version": "0.4.2", "commit": "abc1234", "scene_file": "scripts/scenes/x.toml",
        "recorded": "2026-11-02T13:55:00-05:00",
    })

    assert document["contract"] == 1
    assert document["module"] == {"order": 1, "slug": "what-an-mes-is-for", "title": "What an MES is for"}
    assert document["lesson"]["knows_before"] == ["1.1"]
    assert len(document["lesson"]["outcomes"]) == 3
    assert (document["lesson"]["duration_seconds"], document["lesson"]["take"]) == (287.4, 3)
    assert document["files"] == files
    assert document["frame"] == {"width": 1280, "height": 720, "fps": 20}
    assert document["reserved"]["avatar"] == {"corner": "bottom-right", "width": 240, "height": 200}
    assert document["reserved"]["caption_bar"]["right_inset"] == 260
    assert document["reserved"]["status_badge"] == {"corner": "top-right"}
    assert document["filmed_from"]["plant"] == "labs/multiplant/bottling at 20x, scratch, loopback"
    assert "simulated plant" in document["honesty"]
    assert any("no audio" in line for line in document["honesty"])


# ------------------------------------------------------- marks, holds and slides


@pytest.mark.parametrize(
    ("line", "hold"),
    [
        ("In control.", 2.5),  # two words would be 1.5 s; the floor is 2.5
        ("The operator takes it under review. On the record.", 4.3),  # 9 words: 3.6 + 0.7
        # 14 words — the dash is not one — 5.6 + 0.7
        ("Capability is withheld now — a number on an unstable process would be a lie.", 6.3),
    ],
)
def test_a_caption_with_no_hold_of_its_own_stays_up_for_its_words(runner, line, hold):
    assert runner.hold_for_words(line) == hold


def write_scene(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "scene.toml"
    path.write_text('name = "t"\n' + body, encoding="utf-8")
    return path


def test_a_part_carries_from_step_to_step_until_another_is_named(runner, tmp_path):
    scene = runner.load_scene(write_scene(tmp_path, '''
part = "hook"
[[step]]
do = "say"
line = "Something happens."
[[step]]
do = "slide"
part = "idea"
name = "the word"
title = "What SPC is"
lines = ["A chart of readings", "and the rules that fire on it"]
[[step]]
do = "hold"
[[step]]
do = "say"
part = "screen"
line = "Here it is."
hold = 3
'''))
    assert [step["part"] for step in scene["step"]] == ["hook", "idea", "idea", "screen"]
    assert scene["step"][0]["hold"] == 2.5
    card_words = "What SPC is A chart of readings and the rules that fire on it"
    assert scene["step"][1]["hold"] == runner.hold_for_words(card_words)
    assert scene["step"][3]["hold"] == 3


@pytest.mark.parametrize(
    ("body", "refusal"),
    [
        ('part = "middle"\n[[step]]\ndo = "hold"\n', "not one of hook"),
        ('[[step]]\ndo = "slide"\nname = "x"\n', "needs a `name`"),
        ('lesson = "2"\n[[step]]\ndo = "hold"\n', "module.lesson"),
        ('take_is = "trailer"\n[[step]]\ndo = "hold"\n', "take_is"),
    ],
)
def test_a_scene_that_names_a_part_lesson_or_card_wrongly_is_refused(runner, tmp_path, body, refusal):
    with pytest.raises(SystemExit, match=refusal):
        runner.load_scene(write_scene(tmp_path, body))


def test_a_lessons_teaser_is_cut_where_the_hook_ends(runner):
    marks = [
        {"at": 0.5, "kind": "goto", "part": "hook"},
        {"at": 3.0, "kind": "say", "part": "hook"},
        {"at": 21.4, "kind": "slide", "part": "idea"},
        {"at": 80.0, "kind": "say", "part": "screen"},
        {"at": 300.0, "kind": "end", "part": None},
    ]
    assert runner.teaser_length(marks, 2.0) == 19.4
    assert runner.teaser_length([m for m in marks if m["part"] in ("hook", None)], 2.0) is None


def test_with_captions_off_the_bar_is_never_drawn_and_with_them_on_it_stops_short_of_the_corner(runner):
    on = runner.overlay_script("pre-alpha", captions=True)
    off = runner.overlay_script("pre-alpha", captions=False)

    assert f"right:{runner.CAPTION_RIGHT_INSET}px" in on.replace(" ", "")
    hidden = "#fs-scene-cap{display:none!important}"
    assert hidden not in on.replace(" ", "")
    assert hidden in off.replace(" ", "")
    assert f"top:{runner.BADGE_TOP}px" in on.replace(" ", "")
