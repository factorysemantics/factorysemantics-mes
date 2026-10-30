/* Shift analysis.

   Five views over one line and one window. Everything is drawn as inline SVG
   from the API's own numbers — no chart library, matching the rest of this UI,
   which has to keep running on a plant PC for years without a toolchain. The
   timeline, the trend and the pareto come from `kit.js`, which is the one set
   of chart shapes and the one place the chart contract is kept; what is still
   drawn here is drawn here because no other screen wants it yet.

   The one rule inherited from the MES itself: never draw a number that was not
   measured. A null component renders as a hatched "unknown" band, not as zero,
   because a machine reporting 0% availability and a machine nobody has watched
   yet look identical on a bar chart and mean opposite things. */

const $ = (s) => document.querySelector(s);

let state = { line: null, hours: 8, shift: null, machine: null };

/* The window, in the one place that builds it. Either a trailing span of
   hours or a named shift on the plant's clock — never both, because a screen
   that said "night shift" while showing the last eight hours would be worse
   than an error. */
function query() {
  const q = new URLSearchParams();
  if (state.shift) q.set("shift", state.shift);
  else q.set("hours", String(state.hours));
  if (state.line) q.set("line", state.line);
  return "?" + q.toString();
}

/* Keep the picks in the address bar so a screen can be handed to somebody
   else and come back the same. */
function remember() {
  const url = new URL(window.location);
  const p = url.searchParams;
  if (state.line) p.set("line", state.line); else p.delete("line");
  if (state.shift) { p.set("shift", state.shift); p.delete("hours"); }
  else { p.set("hours", String(state.hours)); p.delete("shift"); }
  if (state.machine) p.set("machine", state.machine); else p.delete("machine");
  history.replaceState(null, "", url);
}

const api = (path) => FS.api(path);
const { utc, duration, svg, add, empty, timeTicks } = FS.kit;

/* ---------- small helpers ---------- */
const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");
const num = (v) => (v === null || v === undefined ? "—" : Math.round(v).toLocaleString());

/* ---------- OEE by station ---------- */
function renderOee(data) {
  const host = $("#oee");
  host.replaceChildren();
  if (!data.stations.length) return empty(host, "No machines on this line.");

  for (const s of data.stations) {
    const row = document.createElement("div");
    row.className = "oee-row" + (data.constraint === s.code ? " is-constraint" : "");

    const who = document.createElement("div");
    who.className = "who";
    const code = FS.link("machine", s.code, "code");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = s.name;
    who.append(code, name);

    const bar = document.createElement("div");
    bar.className = "bar";
    if (s.coverage_note && s.coverage_floor) {
      // Below this plant's floor the bar is not drawn smaller — a shorter
      // waterfall reads as a worse machine, and what actually happened is
      // that nobody watched it. The row says so and the ledger is printed
      // under it instead.
      const withheld = document.createElement("i");
      withheld.className = "b-unknown";
      withheld.title = s.coverage_note;
      bar.appendChild(withheld);
    } else if (s.counts_outrun_run_time) {
      // A waterfall only adds up while every loss is a loss, and here one of
      // them is negative: the station counted more work than its own run time
      // can hold. Between 2026-09-14 and 2026-09-18 this drew one full
      // segment and printed the ratio in the score, which is how a plant
      // replaying at 10x came to show 980 %. There is no figure to draw now —
      // the disagreement is the finding — so the bar says unknown and the row
      // says why, in words, underneath.
      const unknown = document.createElement("i");
      unknown.className = "b-unknown";
      unknown.title = s.performance_note;
      bar.appendChild(unknown);
    } else if (s.oee === null) {
      // Not measurable yet — say so rather than drawing a zero.
      const unknown = document.createElement("i");
      unknown.className = "b-unknown";
      unknown.title = "Not enough observed history to compute OEE";
      bar.appendChild(unknown);
    } else {
      // A waterfall: what survived, then each loss in the order it is taken.
      const perf = s.performance ?? 1;
      const segments = [
        ["b-oee", s.oee, `OEE ${pct(s.oee)}`],
        ["b-avail", 1 - s.availability, `Availability loss — ${duration(s.loss.availability_seconds)} not running`],
        ["b-perf", s.availability * (1 - perf), `Performance loss — ${num(s.loss.performance_units)} units below rated rate`],
        ["b-qual", s.availability * perf * (1 - (s.quality ?? 1)), `Quality loss — ${num(s.loss.quality_units)} scrapped`],
      ];
      for (const [cls, width, title] of segments) {
        if (!width || width <= 0.0005) continue;
        const i = document.createElement("i");
        i.className = cls;
        i.style.width = (width * 100).toFixed(2) + "%";
        i.title = title;
        bar.appendChild(i);
      }
    }

    const placed = FS.kit.tooNew(s);
    const score = document.createElement("div");
    score.className = "score";
    score.textContent = placed ? "new" : pct(s.oee);
    score.title =
      `A ${pct(s.availability)} · P ${pct(s.performance)} · Q ${pct(s.quality)}\n` +
      `${num(s.good_qty)} good, ${num(s.scrap_qty)} scrap\n` +
      `running ${duration(s.runtime_seconds)}, down ${duration(s.downtime_seconds)}` +
      (s.performance_note ? `\nPerformance: ${s.performance_note}` : "") +
      `\nWatched ${pct(s.coverage)} of the window`;

    row.append(who, bar, score);
    // How much of the window anybody watched, on the row itself and not only
    // in a tooltip. A waterfall drawn over eleven observed minutes of a shift
    // is not a smaller waterfall, and nothing else on this row says so.
    const watched = document.createElement("div");
    watched.className = "watched" + (placed ? " too-new"
      : s.coverage_note && s.coverage_floor ? " withheld" : "");
    watched.textContent = placed
      ? placed
      : s.coverage_note && s.coverage_floor
        ? `Watched ${pct(s.coverage)} — below this plant's floor, so the figures are unknown`
        : `Watched ${pct(s.coverage)}`;
    watched.title = s.coverage_note
      || "How much of this window the MES actually watched. `fsmes oee explain "
         + s.code + "` prints every second of it.";
    row.appendChild(watched);
    // Counted work that will not fit inside the run time is worth a mark on
    // the row rather than only a tooltip: the figure it replaces used to be a
    // silent 1.0, and then a loud 881 %. The mark names the disagreement, not
    // a culprit — the MES cannot tell whether the rating is slow or the run
    // time is short.
    if (s.counts_outrun_run_time) {
      const flag = document.createElement("div");
      flag.className = "counts-outrun";
      // No percentage here. The figure this used to print was the ratio
      // between the two numbers named below, and a percentage on a row is
      // read as a measurement of the machine.
      flag.textContent =
        `Counted work outruns the run time — ${num(s.good_qty + s.scrap_qty)} units at `
        + `${s.ideal_cycle_seconds} s is more work than the run time recorded holds, `
        + `so there is no performance figure`;
      flag.title = s.performance_note;
      row.appendChild(flag);
    }
    host.appendChild(row);
  }
}

/* ---------- state timeline ---------- */
function renderTimeline(data) {
  FS.kit.timeline($("#timeline"), data);
}

/* ---------- downtime pareto ----------

   Drawn by the kit, not by this file. It used to be forty lines of SVG here,
   which is how the same picture came to say less than the payload behind it
   knew: the bars were drawn but the total was not, the unlabelled bucket was
   a dimmer red rather than a rendering of its own, and the 15 minutes nobody
   was watching were in the envelope and nowhere on the screen. The kit draws
   it against the six rules, so all of that is on the chart now — and the
   analysis screen and the AI tab cannot come to draw the same pareto
   differently, which is the whole reason kit.js exists. */
function renderPareto(data) {
  const host = $("#pareto");
  if (!data.reasons.length) {
    host.replaceChildren();
    // Not "nothing to explain" alone: a window with no downtime in it is still
    // a window over some number of machines, and the total says which.
    return empty(host,
      `No downtime in this window — nothing to explain, across ${data.machines_total} machine(s).`);
  }
  FS.kit.draw(host, "pareto", data, { labelWidth: 96, noun: "reason" });
}

/* ---------- production over time ---------- */
function renderProduction(data) {
  const host = $("#production");
  host.replaceChildren();
  if (!data.points.length) return empty(host, "Nothing booked in this window.");

  const left = 40, right = 10, top = 10, bottom = 24;
  const width = Math.max(host.clientWidth || 420, 360);
  const height = 190;
  const plot = width - left - right;
  const plotH = height - top - bottom;
  const start = utc(data.window.start).getTime();
  const end = utc(data.window.end).getTime();
  const max = Math.max(...data.points.map((p) => p.good + p.scrap), 1);
  const barW = Math.max(plot / data.points.length - 1, 1);

  const chart = svg(width, height);
  for (let i = 0; i <= 2; i++) {
    const y = top + plotH - (i / 2) * plotH;
    add(chart, "line", { x1: left, y1: y, x2: left + plot, y2: y, class: "grid-line" });
    add(chart, "text", { x: left - 6, y: y + 3, class: "axis", "text-anchor": "end" },
        num((max * i) / 2));
  }
  timeTicks(chart, start, end, plot, top + plotH, left);

  for (const point of data.points) {
    const x = left + ((utc(point.t).getTime() - start) / Math.max(end - start, 1)) * plot;
    const goodH = (point.good / max) * plotH;
    const scrapH = (point.scrap / max) * plotH;
    if (goodH > 0) {
      const r = add(chart, "rect", { x, y: top + plotH - goodH, width: barW, height: goodH, class: "series-good" });
      add(r, "title", {}, `${num(point.good)} good`);
    }
    if (scrapH > 0) {
      const r = add(chart, "rect", {
        x, y: top + plotH - goodH - scrapH, width: barW, height: scrapH, class: "series-scrap",
      });
      add(r, "title", {}, `${num(point.scrap)} scrap`);
    }
  }
  host.appendChild(chart);
}

/* ---------- process value trend ---------- */
function renderTag(data) {
  FS.kit.trend($("#tag"), data, {
    emptyText: `No process-value history for ${data.equipment} in this window.`,
  });
}

/* ---------- energy ---------- */
/* Energy beside OEE, never in it. The API leaves the block out on a plant
   with no power readings, and then the panel is not drawn at all: an empty
   energy panel on a plant with no power figures would say "0 MW", which nobody measured. */
const mw = (v) => (v === null || v === undefined ? "—" : `${(+v).toFixed(1)} MW`);
const kwh = (v) => (v === null || v === undefined ? "—" : `${(+v).toFixed(2)} kWh`);
const mwhText = (v) => (v === null || v === undefined ? "—" : `${(+v).toFixed(3)} MWh`);

function cell(text, cls) {
  const td = document.createElement("td");
  if (cls) td.className = cls;
  if (text instanceof Node) td.appendChild(text); else td.textContent = text;
  return td;
}

function renderEnergy(e) {
  const panel = $("#energy-panel");
  if (!e) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");

  // One bar: what the circuits draw now, against what they can give, with
  // the peak demand marked - the draw if every building wanted power at once.
  const host = $("#energy-bar");
  host.replaceChildren();
  const cap = e.capacity_mw, used = e.consumed_mw, peak = e.peak_demand_mw;
  if (cap === null && used === null) {
    empty(host, "No circuit figures in this window.");
  } else {
    const top = Math.max(cap || 0, peak || 0, used || 0) || 1;
    const at = (v) => `${((v / top) * 100).toFixed(2)}%`;
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label",
      `drawing ${mw(used)} of ${mw(cap)} capacity, peak demand ${mw(peak)}`);
    const fill = document.createElement("i");
    fill.className = "used";
    fill.style.width = at(used || 0);
    fill.title = `drawing now: ${mw(used)}`;
    bar.appendChild(fill);
    if (cap !== null && peak !== null && peak > cap) {
      const over = document.createElement("i");
      over.className = "over";
      over.style.left = at(cap);
      over.style.width = at(peak - cap);
      over.title = `peak demand over capacity by ${mw(peak - cap)}`;
      bar.appendChild(over);
    }
    if (cap !== null) {
      const c = document.createElement("i");
      c.className = "cap";
      c.style.left = at(cap);
      c.title = `capacity: ${mw(cap)}`;
      bar.appendChild(c);
    }
    if (peak !== null) {
      const p = document.createElement("i");
      p.className = "peak";
      p.style.left = at(peak);
      p.title = `peak demand: ${mw(peak)}`;
      bar.appendChild(p);
    }
    const key = document.createElement("div");
    key.className = "key";
    key.innerHTML =
      `<b style="background:var(--running)"></b>drawing now ${mw(used)}` +
      `<b style="background:var(--idle)"></b>peak demand ${mw(peak)}` +
      `<b style="background:var(--text)"></b>capacity ${mw(cap)}`;
    host.append(bar, key);
  }

  const circuits = e.circuits_total === 1 ? "1 circuit" : `${e.circuits_total} circuits`;
  const facts = [
    `${mwhText(e.mwh)} used in this window`,
    e.headroom_mw === null ? "headroom unknown"
      : e.headroom_mw < 0 ? `${mw(-e.headroom_mw)} short at peak demand`
      : `${mw(e.headroom_mw)} headroom at peak demand`,
    circuits,
  ];
  if (e.generators_total) facts.push(`${mwhText(e.mwh_made)} made by ${e.generators_total} generators`);
  $("#energy-facts").textContent = facts.join(" · ");

  const trip = $("#energy-trip");
  if (e.tripped || e.last_trip) {
    const t = e.last_trip;
    trip.textContent = e.tripped
      ? `Fuse out now on ${(e.circuits.find((c) => c.tripped) || {}).circuit || "a circuit"}: ` +
        ((e.circuits.find((c) => c.tripped) || {}).why || t?.why || "")
      : `Fuse tripped in this window (${t.circuit}, ${FS.fmt.clock(t.at)}, ` +
        `${e.tripped_polls} poll${e.tripped_polls === 1 ? "" : "s"} out): ${t.why}`;
    trip.classList.remove("hidden");
  } else {
    trip.classList.add("hidden");
  }

  const body = $("#energy-table tbody");
  body.replaceChildren();
  for (const b of e.buildings) {
    const tr = document.createElement("tr");
    const who = document.createElement("span");
    who.append(FS.link("machine", b.code, "code"));
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = b.name;
    who.append(name);
    tr.appendChild(cell(who));
    tr.appendChild(cell(kwh(b.kwh_per_unit), "num"));
    if (b.ideal_kwh_per_unit === null) {
      const td = cell(b.ideal_note && b.ideal_note.startsWith("unknown") ? "unknown" : "—",
                      "num unknown");
      td.title = b.ideal_note || "";
      tr.appendChild(td);
    } else {
      tr.appendChild(cell(kwh(b.ideal_kwh_per_unit), "num"));
    }
    const gap = b.gap_kwh_per_unit;
    tr.appendChild(cell(gap === null ? "—" : `${gap > 0 ? "+" : ""}${kwh(gap)}`,
                        "num" + (gap !== null && gap > 0.005 ? " gap-over" : "")));
    const now = cell(mw(b.mw_now), "num");
    if (b.mw_now === null && b.last_read) {
      now.title = `not in the game's last building list; last read ${FS.fmt.clock(b.last_read)}`;
    }
    tr.appendChild(now);
    tr.appendChild(cell(mwhText(b.mwh), "num"));
    tr.appendChild(cell(num(b.units), "num"));
    body.appendChild(tr);
  }
  if (!e.buildings.length) {
    const tr = document.createElement("tr");
    const td = cell("No building drew power in this window.", "muted");
    td.colSpan = 7;
    tr.appendChild(td);
    body.appendChild(tr);
  }

  const gens = e.generators.map((g) =>
    `${g.code}: ${mw(g.output_mw)} of ${mw(g.capacity_mw)}, burning ${g.fuel || "unknown fuel"}`);
  $("#energy-generators").textContent = e.generators_total
    ? `${e.generators_total} generators — ${gens.join(" · ")}`
    : "No generators reported.";
  $("#energy-note").textContent =
    `${e.buildings_total} buildings, worst gap first. ` + e.note +
    " Ideal is the recipe's rate at 100 % clock against the building's nominal draw; " +
    "unknown when the recipe is not in the game's reference.";
}

/* ---------- parts mix ---------- */

const perMin = (v) => (v === null || v === undefined ? "—" : (+v).toFixed(1));
const share = (v) => (v === null || v === undefined ? "—" : `${(+v).toFixed(0)} %`);

/* One row per item in the order or made in the window, shortest first. With
   no rate order the target columns are not drawn at all: no order is no
   target, which is not a target of zero. */
function renderPartsMix(m) {
  const panel = $("#mix-panel");
  if (!m) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");
  const table = $("#mix-table");
  table.classList.toggle("no-target", !m.has_target);

  const facts = [];
  if (m.order) {
    facts.push(`Rate order ${m.order.name}: ${perMin(m.order.per_minute_total)} units/min ` +
               `over ${m.order.items_total} item${m.order.items_total === 1 ? "" : "s"}`);
  } else {
    facts.push("No rate order on this plant, so no target");
  }
  facts.push(m.watched_minutes === null ? "watched time unknown"
    : `${perMin(m.watched_minutes)} min watched`);
  facts.push(`${num(m.units_booked)} units booked`);
  $("#mix-facts").textContent = facts.join(" · ");

  const body = table.querySelector("tbody");
  body.replaceChildren();
  for (const r of m.items) {
    const tr = document.createElement("tr");
    tr.dataset.item = r.item;
    tr.appendChild(cell(r.item));
    if (r.target_per_minute === null) {
      const td = cell("no target", "num tgt unknown");
      td.title = r.why || "";
      tr.appendChild(td);
    } else {
      tr.appendChild(cell(perMin(r.target_per_minute), "num tgt"));
    }
    const actual = cell(perMin(r.actual_per_minute), "num");
    actual.title = `${num(r.units)} units from ${r.machines} building${r.machines === 1 ? "" : "s"}`;
    tr.appendChild(actual);
    tr.appendChild(cell(share(r.target_share_percent), "num tgt"));
    tr.appendChild(cell(share(r.actual_share_percent), "num"));
    const gap = r.gap_per_minute;
    tr.appendChild(cell(gap === null ? "—" : `${gap > 0 ? "+" : ""}${(+gap).toFixed(1)}`,
      "num tgt" + (gap !== null && gap < -0.05 ? " short" : gap > 0.05 ? " over-target" : "")));
    tr.appendChild(cell(perMin(r.game_made_per_minute), "num"));
    body.appendChild(tr);
  }
  if (!m.items.length) {
    const tr = document.createElement("tr");
    const td = cell("Nothing in the order and nothing made in this window.", "muted");
    td.colSpan = 7;
    tr.appendChild(td);
    body.appendChild(tr);
  }
  const notes = [
    `${m.items_total} item${m.items_total === 1 ? "" : "s"}, shortest first.`,
    "Actual is the good units the MES booked over the minutes the game was watched; " +
    `shares are of ${m.share_basis}. Game made is the game's own figure (getProdStats), ` +
    "the check on the bookings: it counts every building of the save.",
  ];
  if (m.game_items_not_booked_total) {
    notes.push(`The game also made ${m.game_items_not_booked_total} item` +
      `${m.game_items_not_booked_total === 1 ? "" : "s"} the MES books none of ` +
      `(${m.game_items_not_booked.join(", ")}).`);
  }
  if (m.note) notes.push(m.note + ".");
  $("#mix-note").textContent = notes.join(" ");
}

/* "last played 04:41–05:23 today": the stretch's first poll rounded down to
   the minute and its last rounded up, on the plant's clock. */
function lastPlayed(start, end) {
  const at = (ts) => new Date(/Z$|[+-]\d\d:?\d\d$/.test(String(ts)) ? ts : `${ts}Z`);
  const zone = FS.fmt.zone() ? { timeZone: FS.fmt.zone() } : {};
  const minute = 60000;
  const a = new Date(Math.floor(at(start).getTime() / minute) * minute);
  const b = new Date(Math.ceil(at(end).getTime() / minute) * minute);
  const hm = (d) => d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false, ...zone });
  const day = (d) => d.toLocaleDateString(undefined, zone);
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86400000);
  const when = day(a) === day(today) ? "today" : day(a) === day(yesterday) ? "yesterday" : day(a);
  return `last played ${hm(a)}–${hm(b)} ${when}`;
}

/* ---------- load ---------- */
async function load() {
  const q = query();
  const banner = $("#banner");
  banner.classList.add("hidden");
  remember();

  const [oee, timeline, downtime, production] = await Promise.all([
    api(`/analysis/oee${q}`),
    api(`/analysis/timeline${q}`),
    api(`/analysis/downtime${q}`),
    api(`/analysis/production${q}`),
  ]);

  $("#kpi-oee").textContent = pct(oee.line_oee);
  // The line's figure is drawn from the stations that have one, so the sub
  // line says how many that was out of how many there are. Every list states
  // its total, and this is a list of one number.
  const rated = [`${oee.stations_rated} of ${oee.machines_total} stations`];
  if (oee.stations_counts_outrun) {
    rated.push(`${oee.stations_counts_outrun} counting past its run time`);
  }
  const tooNew = oee.stations_too_new || 0;
  if (tooNew) rated.push(`${tooNew} too new`);
  if (oee.stations_withheld - tooNew > 0) {
    rated.push(`${oee.stations_withheld - tooNew} below the coverage floor`);
  }
  $("#kpi-constraint").textContent =
    (oee.constraint ? `worst: ${oee.constraint} · ` : "") + rated.join(" · ");
  $("#kpi-good").textContent = num(oee.good_qty);
  $("#kpi-scrap").textContent = num(oee.scrap_qty);
  const made = oee.good_qty + oee.scrap_qty;
  $("#kpi-scrap-pct").textContent = made ? `${((oee.scrap_qty / made) * 100).toFixed(1)}% of output` : "";
  $("#kpi-downtime").textContent = duration(downtime.total_seconds);
  $("#kpi-unlabelled").textContent = downtime.total_seconds
    ? `${Math.round((downtime.unlabelled_share || 0) * 100)}% unlabelled`
    : "";

  if (oee.window.clamped) {
    // Honest about the window: the MES has not been watching for as long as asked.
    banner.textContent =
      `Showing ${oee.window.hours.toFixed(2)} h, not ${oee.window.requested_hours} h — ` +
      `that is how long the MES has been watching this line. Time before that is not downtime.`;
    banner.classList.remove("hidden");
  }
  // The window read on the plant's clock. A span of hours has no wall-clock
  // boundary in it and the zone only decides how a reader sees its two ends;
  // a shift window is a boundary, drawn where the plant's own clock puts it,
  // so it says which shift it is and how much of it has run.
  const shift = oee.window.shift;
  $("#window-note").textContent = oee.window.named === "last_play"
    ? `${lastPlayed(oee.window.start, oee.window.end)} · ${(oee.window.hours * 60).toFixed(0)} min`
    : shift
    ? `${shift.code} ${shift.day} · ${FS.fmt.stamp(oee.window.start)} → ` +
      `${FS.fmt.clock(oee.window.end)}` +
      (shift.in_progress
        ? ` · ${oee.window.hours.toFixed(2)} h of ${shift.nominal_hours} h so far`
        : ` · ${shift.nominal_hours} h`)
    : `${FS.fmt.stamp(oee.window.start)} → ${FS.fmt.clock(oee.window.end)}`;

  renderOee(oee);
  renderEnergy(oee.energy);
  renderPartsMix(oee.parts_mix);
  renderTimeline(timeline);
  renderPareto(downtime);
  renderProduction(production);

  const picker = $("#tag-machine");
  const codes = oee.stations.map((s) => s.code);
  if (!codes.includes(state.machine)) state.machine = oee.constraint || codes[0] || null;
  picker.replaceChildren(...codes.map((code) => new Option(code, code, false, code === state.machine)));
  if (state.machine) renderTag(await api(`/analysis/tag/${encodeURIComponent(state.machine)}${q}`));
  else empty($("#tag"), "No machines on this line.");
  FS.live(true);
}

/* The shift picker. Only the shifts the plant really has are offered: a
   screen that offered "this shift" on a plant that has told the MES nothing
   about its shifts would be offering a window nobody can compute. */
async function fillShifts(wanted) {
  const select = $("#shift");
  const zone = $("#shift-zone");
  const data = await api("/analysis/shifts").catch(() => null);
  const options = [new Option("by hours", "")];
  // A plant a game feeds is run while somebody plays, not in shifts: the
  // last time they played is the window it is asked about.
  if (data && data.last_play) {
    options.push(new Option(lastPlayed(data.last_play.starts, data.last_play.ends), data.last_play.key));
  }
  if (data && data.shifts_total) {
    if (data.current) options.push(new Option("this shift", "current"));
    if (data.previous) options.push(new Option("last shift", "previous"));
    for (const s of data.shifts) {
      options.push(new Option(`${s.code} · ${s.day}${s.in_progress ? " (running)" : ""}`, s.key));
    }
  }
  select.replaceChildren(...options);
  select.disabled = !(data && (data.shifts_total || data.last_play));
  // Which clock those boundaries are drawn on, and whether anybody chose it.
  // A shift boundary read in the browser's zone on a plant five hours away is
  // the whole reason `MES_PLANT_TIMEZONE` exists.
  zone.textContent = !data
    ? ""
    : data.shifts_total
      ? `on ${data.timezone || "this machine's own clock"}` +
        (data.timezone_defaulted ? " (defaulted — nobody set MES_PLANT_TIMEZONE)" : "")
      : data.last_play ? "" : (data.note || "");
  if (wanted && [...select.options].some((o) => o.value === wanted)) {
    state.shift = wanted;
    select.value = wanted;
  }
  $("#hours").disabled = Boolean(state.shift);
}

async function boot() {
  await FS.whoami().catch(() => {});
  const lines = await api("/analysis/lines");
  const select = $("#line");
  select.replaceChildren(
    ...lines.map((l) => new Option(`${l.code} — ${l.name} (${l.stations})`, l.code))
  );
  const params = new URL(window.location).searchParams;
  const wanted = params.get("line");
  state.line = lines.some((l) => l.code === wanted) ? wanted : (lines.length ? lines[0].code : null);
  if (state.line) select.value = state.line;

  const askedHours = parseFloat(params.get("hours"));
  if (Number.isFinite(askedHours) && askedHours > 0) {
    state.hours = askedHours;
    const hoursSelect = $("#hours");
    if ([...hoursSelect.options].some((o) => parseFloat(o.value) === askedHours)) {
      hoursSelect.value = String(askedHours);
    }
  }
  state.machine = params.get("machine") || null;
  // No window in the address: on a plant a game feeds, the last time you
  // played; elsewhere the plant's own default span. Asking for hours keeps
  // hours.
  const askedShift = params.get("shift") ||
    (params.has("hours") ? null : "last_play");
  await fillShifts(askedShift);

  select.onchange = () => { state.line = select.value; load().catch(fail); };
  $("#hours").onchange = (e) => { state.hours = parseFloat(e.target.value); load().catch(fail); };
  $("#shift").onchange = (e) => {
    state.shift = e.target.value || null;
    // The two windows are alternatives, and the disabled picker says so
    // rather than leaving a stale "8 hours" sitting beside "night shift".
    $("#hours").disabled = Boolean(state.shift);
    load().catch(fail);
  };
  $("#tag-machine").onchange = async (e) => {
    state.machine = e.target.value;
    remember();
    renderTag(await api(`/analysis/tag/${encodeURIComponent(state.machine)}${query()}`));
  };
  $("#refresh").onclick = () => load().catch(fail);
  // Re-lay-out the SVGs when the window changes width; they are sized in pixels.
  let resize;
  window.addEventListener("resize", () => {
    clearTimeout(resize);
    resize = setTimeout(() => load().catch(fail), 250);
  });

  await load();
}

function fail(error) {
  FS.live(false);
  const banner = $("#banner");
  banner.textContent = error.message;
  banner.classList.remove("hidden");
}

boot().catch(fail);
