"""Shared plumbing for the cutlery run analysis notebook: where the evidence
is, how it loads, and the one palette every figure draws from.

The palette is the validated reference set of the data-viz method (fixed
categorical order, status colours reserved for machine states, one blue ramp
for magnitude). Matplotlib carries the print figures, Plotly the interactive
ones; both read these values so a series keeps its colour across the two.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
LAB = HERE.parent
REPO = LAB.parents[1]
OUT = LAB / "out"
RESULTS = OUT / "results"
FIGURES = OUT / "analysis" / "figures"
FIGURES.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ palette
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
       "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
NEUTRAL = "#c3c2b7"

# Machine states are status, not series: running is good, down is critical,
# setup is a planned warning, idle is neutral.
STATE_COLOR = {"running": STATUS["good"], "down": STATUS["critical"], "setup": STATUS["warning"], "idle": NEUTRAL}

# Fixed identity: the same colour for the same thing on every figure.
KIND_COLOR = {"utensil": SERIES[0], "stacker": SERIES[1], "wrapper": SERIES[2], "palletizer": SERIES[3]}
SPEED_COLOR = {1: SERIES[0], 3: SERIES[1], 5: SERIES[2], 8: SERIES[3], 10: SERIES[4], 30: SERIES[5]}
MATERIAL_COLOR = {"UT-FORK": SERIES[0], "UT-SPOON": SERIES[1], "UT-KNIFE": SERIES[2], "STACK-3": SERIES[3],
                  "PLATE": SERIES[4], "WRAP": SERIES[5], "PALLET": SERIES[6]}
PROCESS_COLOR = {"run-api": SERIES[0], "run-opc-agent": SERIES[1], "run-opc-sim": SERIES[2],
                 "run-operations": SERIES[3], "postgres": SERIES[4]}


def style_matplotlib() -> None:
    import matplotlib as mpl

    mpl.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "grid.linestyle": "-",
        "axes.axisbelow": True, "axes.titleweight": "semibold", "axes.titlesize": 11, "axes.labelsize": 9.5,
        "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5, "text.color": INK, "legend.frameon": False, "legend.fontsize": 8.5,
        "lines.linewidth": 2, "lines.markersize": 5, "font.family": "sans-serif", "font.size": 9.5,
        "figure.dpi": 110, "savefig.dpi": 160, "savefig.bbox": "tight",
        "axes.prop_cycle": mpl.cycler(color=SERIES),
    })


def plotly_template():
    import plotly.graph_objects as go
    import plotly.io as pio

    tpl = go.layout.Template()
    tpl.layout = go.Layout(
        colorway=SERIES, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font={"family": "system-ui, -apple-system, Segoe UI, sans-serif", "color": INK, "size": 12},
        title={"font": {"size": 15, "color": INK}, "x": 0.02},
        xaxis={"gridcolor": GRID, "linecolor": AXIS, "zerolinecolor": AXIS, "tickcolor": MUTED,
               "tickfont": {"color": MUTED}, "title": {"font": {"color": INK2}}},
        yaxis={"gridcolor": GRID, "linecolor": AXIS, "zerolinecolor": AXIS, "tickcolor": MUTED,
               "tickfont": {"color": MUTED}, "title": {"font": {"color": INK2}}},
        legend={"orientation": "h", "y": -0.18, "font": {"color": INK2}},
        margin={"l": 60, "r": 20, "t": 60, "b": 60}, hovermode="x unified",
    )
    pio.templates["fsmes"] = tpl
    pio.templates.default = "fsmes"
    pio.renderers.default = "notebook"


# ------------------------------------------------------------------ evidence
def runs() -> list[dict]:
    """Every scored run kept under out/results, oldest first, each with the
    name of its results file."""
    out = []
    for path in sorted(glob.glob(str(RESULTS / "run-*x.json"))):
        r = json.loads(Path(path).read_text())
        r["_file"] = Path(path).name
        out.append(r)
    return out


def results() -> dict[int, dict]:
    """{speed: results json}, the latest run at each speed."""
    out = {}
    for r in runs():
        out[int(r["speed"])] = r
    return out


def by_speed() -> dict[int, list[dict]]:
    """{speed: [every run at that speed, oldest first]}: the standard wants a
    repeat, and a repeat is only evidence if it is looked at."""
    out: dict[int, list[dict]] = {}
    for r in runs():
        out.setdefault(int(r["speed"]), []).append(r)
    return out


# ------------------------------------------------------------ the run's database
def run_url(res: dict) -> str | None:
    """The URL of a run's database, PostgreSQL or SQLite, or None when it was
    not kept. The results file carries the URL without its password; the
    password is where the registry says."""
    url = (res.get("db") or {}).get("url") or ""
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1]
        return url if Path(path).exists() else None
    if not url:
        return None
    import tomllib

    registry = tomllib.loads((LAB / "registry.toml").read_text(encoding="utf-8"))["plants"]["cutlery"]
    pw_file = Path(registry.get("database_password_file", "~/.config/fsmes/pg-password")).expanduser()
    password = pw_file.read_text(encoding="utf-8").strip() if pw_file.exists() else ""
    return f"postgresql+psycopg://fsmes:{password}@{url}"


_ENGINES: dict[str, object] = {}


def engine(res: dict):
    """A SQLAlchemy engine on a run's kept database, or None."""
    from sqlalchemy import create_engine, text

    url = run_url(res)
    if url is None:
        return None
    if url not in _ENGINES:
        eng = create_engine(url, pool_pre_ping=True)
        try:
            with eng.connect() as con:
                con.execute(text("select 1"))
        except Exception:
            return None
        _ENGINES[url] = eng
    return _ENGINES[url]


def query(res_or_engine, sql: str, params: dict | None = None) -> pd.DataFrame:
    """Run a query against a run's database; dialect-neutral SQL only."""
    from sqlalchemy import text

    eng = res_or_engine if not isinstance(res_or_engine, dict) else engine(res_or_engine)
    if eng is None:
        raise RuntimeError("this run's database was not kept")
    with eng.connect() as con:
        return pd.read_sql_query(text(sql), con, params=params or {})


def session(res: dict):
    """An ORM session on the run's database, for the services that render
    certificates and traces - the same code the API runs."""
    from sqlalchemy.orm import Session

    return Session(engine(res))


def fill_day() -> dict | None:
    p = RESULTS / "fill_day.json"
    return json.loads(p.read_text()) if p.exists() else None


def evidence_dir(res: dict) -> Path | None:
    d = Path((res.get("scorecard") or {}).get("evidence_dir") or "")
    return d if d.is_dir() else None


def agent_reports(res: dict, event: str) -> pd.DataFrame:
    """The agent's own periodic reports of one kind ('inspection ingestion'
    or 'agent ingestion') as a frame with t in minutes of line time."""
    d = evidence_dir(res)
    if d is None:
        return pd.DataFrame()
    rows = [r for r in jsonl(d / "logs" / "opc-agent.jsonl") if r.get("event") == event]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    t = ts(df.timestamp)
    df["t"] = (t - t.min()).dt.total_seconds() * float(res["speed"]) / 60
    df["wall_s"] = (t - t.min()).dt.total_seconds()
    return df


def replay_reports(res: dict) -> pd.DataFrame:
    d = evidence_dir(res)
    if d is None:
        return pd.DataFrame()
    rows = [r for r in jsonl(d / "logs" / "opc-replay.jsonl") if r.get("event") == "inspection groups emitted"]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    t = ts(df.timestamp)
    df["t"] = (t - t.min()).dt.total_seconds() * float(res["speed"]) / 60
    return df


def plant() -> dict:
    return json.loads((LAB / "line.json").read_text())


def tags_manifest() -> dict:
    return json.loads((OUT / "tags.json").read_text())


def jsonl(path: Path) -> list[dict]:
    """A component's structured log, the rotated files before it first."""
    from fsmes.sim.runner import log_files

    rows = []
    for part in log_files(path):
        if not part.exists():
            continue
        with open(part) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def sim_csv(station: str) -> pd.DataFrame:
    return pd.read_csv(OUT / f"{station}.csv")


def ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True)


def save(fig, name: str) -> Path:
    """Save a matplotlib figure as the print copy of a notebook figure."""
    path = FIGURES / f"{name}.png"
    fig.savefig(path)
    return path


def save_plotly(fig, name: str, width: int = 1100, height: int = 560) -> Path | None:
    """Save a static copy of a Plotly figure for the print/vault export;
    kaleido may be unavailable, in which case the interactive copy stands."""
    path = FIGURES / f"{name}.png"
    # The figure itself, for the report page to draw interactively.
    fig.write_json(str(FIGURES / f"{name}.json"))
    try:
        fig.write_image(str(path), width=width, height=height, scale=1.5)
        return path
    except Exception:
        return None


def seq_gaps(res: dict) -> int | None:
    """Published groups that were not recorded, from the record itself: every
    station numbers its groups, so a gap in the sequence held in
    unit_inspections is a group the agent never wrote. None when the run's
    database was not kept and its results file does not carry the count."""
    gaps = (res.get("db") or {}).get("seq_gaps")
    if isinstance(gaps, int):
        return gaps
    eng = engine(res)
    if eng is None:
        return None
    frame = query(eng, "select coalesce(sum(mx - mn + 1 - n), 0) as g from (select equipment_id, max(seq) as mx, "
                       "min(seq) as mn, count(*) as n from unit_inspections group by equipment_id) t")
    return int(frame.g[0])


def kept_up(res: dict) -> bool:
    """The agent recorded every group the stations published: its tally
    agrees with the replay's, no sequence gap in the record, no partial
    group; when the record is not there to ask, the tallies alone."""
    ingested = (res.get("inspection") or {}).get("ingested") or {}
    emitted = ((res.get("inspection") or {}).get("emitted") or {}).get("events")
    if ingested.get("partial") or not emitted or not ingested.get("events"):
        return False
    # A backlog the agent never reached leaves no gap in the sequences it did
    # write, so the tally has to agree too: within a percent, which is what
    # the replay's last periodic report can be off by.
    if ingested["events"] < 0.99 * emitted:
        return False
    gaps = seq_gaps(res)
    if gaps is not None:
        return gaps == 0
    return ingested["events"] >= emitted
