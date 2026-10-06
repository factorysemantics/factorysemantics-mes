"""The sampling plan in a browser: where it is written, and what the chart says.

Two screens, in Chromium, on a plant that really inspects something five at a
time.

**The Specifications tab** gains one field. A person who has never heard of
X-bar and R has to be able to see what the number means before they type it,
and a person who leaves it empty has to get back the chart they have always
had.

**The SPC screen** draws it, since 2026-10-06: the mean of each five bottles
above, the spread inside each five below, one dot per sample on both halves.
That is most of why this file exists, because the ways this drawing can lie
are all ways only a browser can see them. A true number under a false word
("Readings 12" over sixty bottles; the spread of a mean under "Sigma
(within)") is the failure this product exists not to have. So is a true
picture under a false caption: on 2026-10-06 the lab's bottling plant drew a
fill-height chart with no samples yet under the heading INDIVIDUALS AND MOVING
RANGE, and a reader believes the caption before they believe the picture.
Every word around the chart that is about *which chart this is* is asserted
here, with samples and without them.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for, and a Playwright file marked only `slow` is a test nothing runs.
"""

import socket
import threading
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

#: Fifteen samples of five on the sampled characteristic - comfortably past
#: this plant's `[quality] spc_min_points`, so the limits are drawn from more
#: than the fewest points that mean anything. The means wander by a tenth,
#: every range is exactly 2.0, and nothing fires.
MEANS = [142.0, 142.1, 141.9] * 5

#: And readings on a characteristic that has no sampling plan at all, so the
#: last test can see that the individuals chart is untouched.
BRIX = [11.0, 10.9, 11.1, 11.0, 10.95, 11.05, 11.0,
        10.9, 11.1, 11.0, 10.98, 11.02, 10.96, 11.04]

#: What the figures row reads on the sampled chart, worked out by hand for
#: fifteen samples of five with R̄ = 2.0 (A2 0.577, d2 2.326): X̿ is 142.0,
#: the within-process sigma is 2.0/2.326 = 0.8598, a mean of five varies by
#: 0.8598/√5 = 0.3847, and Cp is 6.0/(6 * 0.8598).
CENTRE, SIGMA_WITHIN, CP = "142.00", "0.860", "1.16"


def _sample(mean: float) -> list[float]:
    """Five readings whose mean is `mean` and whose range is exactly 2.0."""
    return [mean - 1.0, mean - 0.5, mean, mean + 0.5, mean + 1.0]


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A demo plant with one sampled characteristic and fifteen samples on it.

    `fill_height`, five pieces at a time, 139 to 145 mm - the bottling pack's
    own plan, on the demo plant's finished good so the screens have a sampled
    specification to list beside the one that is inspected one at a time. Brix
    gets its ordinary readings through the same gauge, because "the chart
    everybody already has still draws" is one of the things asserted here. And
    `crown_torque`, four at a time with nothing recorded on it, because a
    sampled characteristic with no samples yet is a screen that has to say
    which chart it is about before it has anything to draw.
    """
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine, utcnow
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, gauges, quality

    path = tmp_path_factory.mktemp("sampled") / "plant.db"
    url = f"sqlite:///{path}"

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("MES_PLANT_TIMEZONE", "UTC")
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

        engine = make_engine(url)
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            seed_demo_plant(session)
            auth.ensure_builtin_roles(session)
            gauges.register(session, code="HEIGHT-01", name="Bench height gauge",
                            kind="height gauge", resolution=0.1, interval_days=180,
                            location="MIX01", actor="test")
            gauges.calibrate(session, "HEIGHT-01", result="pass", performed_by="QA-LEAD",
                             performed_on=utcnow().date() - timedelta(days=10),
                             certificate="CERT-HEIGHT-01", actor="test")
            quality.create_spec(session, material_code="FG-COLA",
                                characteristic="fill_height", unit="mm",
                                min_value=139.0, max_value=145.0, sample_size=5,
                                actor="test")
            # Four at a time and not one reading taken: the state the lab
            # plant was in when it drew the wrong heading.
            quality.create_spec(session, material_code="FG-COLA",
                                characteristic="crown_torque", unit="Nm",
                                min_value=1.0, max_value=3.0, sample_size=4,
                                actor="test")
            for mean in MEANS:
                quality.record_sample(session, material_code="FG-COLA",
                                      characteristic="fill_height",
                                      values=_sample(mean), gauge_code="HEIGHT-01",
                                      equipment_code="MIX01", actor="OP-NIGHT")
                session.flush()
            for value in BRIX:
                quality.record_check(session, material_code="FG-COLA",
                                     characteristic="brix", value=value,
                                     gauge_code="HEIGHT-01", equipment_code="MIX01",
                                     actor="OP-NIGHT")
                session.flush()
            session.commit()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(
            create_app(), log_level="warning", lifespan="on"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
            _wait_until_answering(base)
            yield base
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


def _wait_until_answering(base: str, seconds: float = 20.0) -> None:
    import time
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1) as reply:
                if reply.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    raise AssertionError(f"the plant at {base} never answered /health")


@pytest.fixture(scope="module")
def chromium(plant):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="the [dev] extra is not installed").sync_playwright

    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            yield browser
            browser.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


@pytest.fixture(scope="module")
def admin(chromium, plant):
    """Signed in as somebody who may write master data."""
    import json

    context = chromium.new_context(viewport={"width": 1500, "height": 1100})
    reply = context.request.post(
        f"{plant}/auth/login",
        data=json.dumps({"code": "ADMIN", "password": "admin"}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in as ADMIN failed: {reply.status}"
    yield context
    context.close()


# --------------------------------------------------- the field, on the screen


@pytest.fixture()
def specs_tab(admin, plant):
    """Master data, Specifications tab, with the listing drawn.

    Waited on by the table's own rows and not by the tab existing: the tab is
    in the markup before the fetch behind it answers, and a test that read the
    table then would read an empty one - #112, 2026-09-26.
    """
    page = admin.new_page()
    page.goto(f"{plant}/dashboard/masterdata", wait_until="load", timeout=30000)
    page.click(".tab[data-tab='specs']")
    page.wait_for_function(
        "() => document.querySelectorAll('#spec-table tbody tr').length > 1",
        timeout=30000)
    yield page
    page.close()


def _cells(page) -> list[list[str]]:
    return page.eval_on_selector_all(
        "#spec-table tbody tr",
        "trs => trs.map(tr => Array.from(tr.children).map(td => td.textContent.trim()))")


def _column(page, heading: str) -> int:
    headers = page.eval_on_selector_all(
        "#spec-table thead th", "ths => ths.map(th => th.textContent.trim())")
    assert heading in headers, headers
    return headers.index(heading)


def _row(page, characteristic: str) -> list[str]:
    rows = [r for r in _cells(page) if r[1] == characteristic]
    assert len(rows) == 1, f"{characteristic}: {[r[1] for r in _cells(page)]}"
    return rows[0]


def test_the_specifications_table_says_how_many_pieces_each_one_is_inspected_at(specs_tab):
    """A column, with a dash where no plan has been written down. Not blank: a
    reader has to be able to tell *one at a time* from *nobody has said which*,
    and on this product the dash is what says the second one."""
    column = _column(specs_tab, "Per sample")
    assert _row(specs_tab, "fill_height")[column] == "5"
    assert _row(specs_tab, "brix")[column] == "—"


def test_the_field_says_what_the_number_will_do_before_anybody_types_it(specs_tab):
    """One field, labelled, with a sentence a person can hover. Somebody who
    has never heard of X-bar and R is about to change how a chart is drawn, and
    the screen owes them that much. Capped at ten, because this product has
    constants for two to ten and will not guess past them."""
    field = specs_tab.query_selector("#spec-n")
    assert field is not None, "the Specifications form has no sample-size field"
    assert field.get_attribute("type") == "number"
    assert (field.get_attribute("min"), field.get_attribute("max")) == ("1", "10")
    assert field.get_attribute("aria-label") == "Pieces per sample"
    said = field.get_attribute("title")
    assert "measured at a time" in said and "Empty or 1 is one at a time" in said


def _add_spec(page, base, characteristic, low, high, per_sample=None):
    page.select_option("#spec-material", "FG-COLA")
    page.fill("#spec-char", characteristic)
    page.fill("#spec-unit", "mm")
    page.fill("#spec-min", str(low))
    page.fill("#spec-max", str(high))
    if per_sample is not None:
        page.fill("#spec-n", str(per_sample))
    page.click("#spec-submit")
    # Wait on the row the save produces, which the page draws only after the
    # listing comes back - not on the toast, which is there sooner.
    page.wait_for_function(
        "characteristic => Array.from(document.querySelectorAll('#spec-table tbody tr'))"
        ".some(tr => tr.children[1].textContent.trim() === characteristic)",
        arg=characteristic, timeout=30000)
    answered = page.request.get(f"{base}/quality/specs?limit=200").json()
    mine = [s for s in answered["items"] if s["characteristic"] == characteristic]
    assert len(mine) == 1, mine
    return mine[0]


def test_writing_a_sample_size_into_the_form_records_the_plan(specs_tab, plant):
    """The whole point of the field: filled in, saved, and the plan is on the
    specification the API answers with - not only in the row on the screen."""
    assert _add_spec(specs_tab, plant, "cap_height", 9.0, 11.0,
                     per_sample=3)["sample_size"] == 3
    assert _row(specs_tab, "cap_height")[_column(specs_tab, "Per sample")] == "3"


def test_leaving_the_field_empty_writes_no_plan_at_all(specs_tab, plant):
    """Empty is *nobody has written a sampling plan down*, which is sent as
    null and charted as individuals. It is not quietly turned into one: a
    screen that filled in a plan on the operator's behalf would be inventing
    master data, and the dash in the row is the plant admitting it."""
    assert _add_spec(specs_tab, plant, "label_offset", -1.0, 1.0)["sample_size"] is None
    assert _row(specs_tab, "label_offset")[_column(specs_tab, "Per sample")] == "—"


# ------------------------------------------------------- the chart it draws


@pytest.fixture()
def sampled_chart(admin, plant):
    """The SPC screen on the sampled characteristic, finished drawing.

    Waited on by the dots themselves - fifteen of them on each half - and not
    by the <svg> existing: the chart is appended before the read behind it
    settles, and a test that counted circles then would count none (#112,
    2026-09-26).
    """
    page = admin.new_page()
    page.goto(f"{plant}/dashboard/spc?spec=FG-COLA%7Cfill_height",
              wait_until="load", timeout=30000)
    page.wait_for_function(
        "n => document.querySelectorAll("
        " '#chart circle[data-series=\"sample-range\"]').length === n",
        arg=len(MEANS), timeout=30000)
    yield page
    page.close()


def _dots(page, series: str) -> list[dict]:
    return page.eval_on_selector_all(
        f"#chart circle[data-series='{series}']",
        "cs => cs.map(c => ({sample: c.dataset.sample, check: c.dataset.check,"
        " cx: Math.round(parseFloat(c.getAttribute('cx'))),"
        " cy: parseFloat(c.getAttribute('cy')),"
        " label: c.getAttribute('aria-label'), role: c.getAttribute('role')}))")


def test_the_screen_draws_one_dot_per_sample_on_each_half_of_one_chart(sampled_chart):
    """Fifteen samples of five: fifteen means above, fifteen ranges below, and
    sixty bottles behind them. Both halves live in one <svg> - that is what
    makes one export carry both - and a dot on either half is the same sample,
    at the same place along the chart, opening the same five readings."""
    assert sampled_chart.get_attribute("#chart svg", "data-kind") == "spc-xbar-r"
    assert len(sampled_chart.query_selector_all("#chart svg")) == 1
    means, ranges = _dots(sampled_chart, "xbar"), _dots(sampled_chart, "sample-range")
    assert len(means) == len(ranges) == len(MEANS)
    assert [d["sample"] for d in means] == [d["sample"] for d in ranges], \
        "the two halves are one series of samples seen twice"
    assert [d["cx"] for d in means] == [d["cx"] for d in ranges], \
        "a range sits under the mean it belongs to, or neither can be read"
    assert all(d["check"] is None for d in means), \
        "a point here is a sample and must not also claim to be one reading"
    assert all(d["role"] == "button" for d in means + ranges)
    assert "Sample 15, mean 141.9 mm of 5 readings" in means[-1]["label"]
    assert "Sample 15, range 2 mm across its 5 readings" in ranges[-1]["label"]


def test_the_two_halves_are_drawn_from_the_samples_own_centre_and_limits(sampled_chart):
    """X̿ above and R̄ below, named on the chart in the letters an SPC book
    uses, so a reader can tell the centre of fifteen means from the centre of
    fifteen readings. The limits either side are the sample mean's, which are
    narrower than the process's own by root n - drawing the process's spread
    around a mean is the mistake that makes an unstable chart look calm."""
    labels = sampled_chart.eval_on_selector_all(
        "#chart text", "ts => ts.map(t => t.textContent.trim())")
    assert "X̿" in labels and "R̄" in labels
    assert "x̄" not in labels, "the individuals centre label on a sampled chart"
    assert any(t.startswith("FG-COLA fill_height (mm) — mean of 5") for t in labels)
    assert any(t.startswith("Range within each sample (mm)") for t in labels)


def test_the_words_around_the_chart_say_which_chart_it_is(sampled_chart):
    """The heading, the legend and the paragraph under the chart. All three
    follow `kind`, because a chart of five-bottle averages captioned
    *individuals and moving range* is read as single bottles by anybody who
    reads the caption first - and everybody does."""
    assert sampled_chart.inner_text("#chart-heading") == "Sample average and range (X̄ and R)"
    legend = sampled_chart.inner_text("#chart-legend")
    assert "sample average (mean of 5)" in legend
    assert "X̿ ± A2·R̄" in legend and "D4·R̄ and D3·R̄" in legend
    said = sampled_chart.inner_text("#chart-explainer")
    assert "how far apart the 5 readings inside each sample" in said
    assert "where the sigma behind both sets of limits comes from" in said


def test_the_figures_beside_it_count_samples_and_not_readings(sampled_chart):
    """Fifteen points drawn from seventy-five bottles. "Readings 15" over that
    would be a true number under a false word, so the label follows the
    sampling plan, and the readings behind the samples are named in the tooltip
    rather than dropped."""
    assert sampled_chart.inner_text("#l-n") == "Samples"
    assert sampled_chart.inner_text("#f-n") == str(len(MEANS))
    said = sampled_chart.get_attribute("#f-n", "title")
    assert said == f"{len(MEANS)} samples of 5 — {len(MEANS) * 5} readings"


def test_the_sigma_under_the_within_label_is_still_the_within_one(sampled_chart):
    """The spread of a mean is smaller than the process's own by root n, and it
    is not what capability is worked out from. The label says within, so the
    figure is within - R̄/d2 - and the mean's own spread, which is what the
    control limits are drawn from, is in the tooltip beside it."""
    assert sampled_chart.inner_text("#f-sigma") == SIGMA_WITHIN
    said = sampled_chart.get_attribute("#f-sigma", "title")
    assert "A sample mean of 5 varies by 0.385" in said
    assert "control limits are drawn from that" in said


def test_the_centre_the_capability_and_both_verdicts_are_the_samples_own(sampled_chart):
    """Every figure on the row, and a sentence for each half. The lower half
    gets its own: every range inside its upper limit is the thing that makes
    the sigma behind both sets of limits worth trusting, and a chart that
    passed judgement only on the averages would be half a verdict."""
    assert sampled_chart.inner_text("#f-centre") == CENTRE
    assert sampled_chart.inner_text("#f-cp") == CP
    assert "in control" in sampled_chart.inner_text("#verdict")
    said = sampled_chart.inner_text("#mr-verdict")
    assert said.startswith("in control")
    assert f"every one of {len(MEANS)} sample ranges" in said


def test_the_button_opens_a_sample_and_says_that_is_what_it_is(sampled_chart):
    """Nothing fired here, so the button offers the newest sample and says so -
    "open the flagged sample" on a chart where nothing is flagged would promise
    something that is not there. And it says, where a reader can see it before
    clicking, that what opens is five bottles and not one."""
    button = sampled_chart.query_selector("#open-point")
    assert not button.is_disabled()
    assert button.inner_text() == "Open the newest sample"
    assert "the mean of 5 pieces" in button.get_attribute("title")
    assert "opens all 5 of them" in button.get_attribute("title")


def test_a_sampled_characteristic_with_no_samples_yet_still_says_which_chart(admin, plant):
    """The lab's 2026-10-06 reading, as a test. `crown_torque` is inspected
    four at a time and has never been measured: there is nothing to draw, and
    the heading, the legend and the paragraph still have to be the sampled
    ones, because a caption is believed whether or not there is a picture under
    it. The empty box says what would make the first point."""
    page = admin.new_page()
    try:
        page.goto(f"{plant}/dashboard/spc?spec=FG-COLA%7Ccrown_torque",
                  wait_until="load", timeout=30000)
        page.wait_for_function(
            "() => { const p = document.querySelector('#chart .empty');"
            " return p && p.textContent.includes('crown_torque'); }",
            timeout=30000)
        assert page.inner_text("#chart-heading") == "Sample average and range (X̄ and R)"
        assert "sample average (mean of 4)" in page.inner_text("#chart-legend")
        assert "4 readings inside each sample" in page.inner_text("#chart-explainer")
        said = page.inner_text("#chart .empty")
        assert "inspected 4 pieces at a time" in said
        assert "4 readings are recorded together as one sample" in said
        assert page.inner_text("#l-n") == "Samples"
        assert page.inner_text("#f-n") == "0"
        assert page.query_selector("#open-point").is_disabled()
    finally:
        page.close()


def test_a_characteristic_inspected_one_at_a_time_still_draws_its_chart(admin, plant):
    """The promise to every plant already running: brix has no sampling plan,
    its chart is individuals, its words are the individuals words, and nothing
    about it has moved."""
    page = admin.new_page()
    try:
        page.goto(f"{plant}/dashboard/spc?spec=FG-COLA%7Cbrix",
                  wait_until="load", timeout=30000)
        page.wait_for_function(
            "() => document.querySelectorAll('#chart circle[data-check]').length > 10",
            timeout=30000)
        assert not page.query_selector("#chart .empty")
        assert page.get_attribute("#chart svg", "data-kind") == "spc-imr"
        assert page.inner_text("#chart-heading") == "Individuals and moving range"
        assert "reading" in page.inner_text("#chart-legend")
        assert "X̿" not in page.inner_text("#chart-legend")
        assert "gap between each reading and the one before" in \
            page.inner_text("#chart-explainer")
        assert not page.query_selector("#chart circle[data-sample]")
        assert page.inner_text("#l-n") == "Readings"
        assert page.inner_text("#f-n") == str(len(BRIX))
        assert page.get_attribute("#f-n", "title") in ("", None)
        assert not page.query_selector("#open-point").is_disabled()
    finally:
        page.close()
