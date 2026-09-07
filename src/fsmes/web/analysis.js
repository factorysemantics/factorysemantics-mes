/* Shift analysis.

   Five views over one line and one window. Everything is drawn as inline SVG
   from the API's own numbers — no chart library, matching the rest of this UI,
   which has to keep running on a plant PC for years without a toolchain.

   The one rule inherited from the MES itself: never draw a number that was not
   measured. A null component renders as a hatched "unknown" band, not as zero,
   because a machine reporting 0% availability and a machine nobody has watched
   yet look identical on a bar chart and mean opposite things. */

const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";

let state = { line: null, hours: 8, machine: null };

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
    if (s.oee === null) {
      // Not measurable yet — say so rather than drawing a zero.
      const unknown = document.createElement("i");
      unknown.className = "b-unknown";
      unknown.title = "Not enough observed history to compute OEE";
      bar.appendChild(unknown);
    } else {
      // A waterfall: what survived, then each loss in the order it is taken.
      const segments = [
        ["b-oee", s.oee, `OEE ${pct(s.oee)}`],
        ["b-avail", 1 - s.availability, `Availability loss — ${duration(s.loss.availability_seconds)} not running`],
        ["b-perf", s.availability * (1 - (s.performance ?? 1)), `Performance loss — ${num(s.loss.performance_units)} units below rated rate`],
        ["b-qual", s.availability * (s.performance ?? 1) * (1 - (s.quality ?? 1)), `Quality loss — ${num(s.loss.quality_units)} scrapped`],
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

    const score = document.createElement("div");
    score.className = "score";
    score.textContent = pct(s.oee);
    score.title =
      `A ${pct(s.availability)} · P ${pct(s.performance)} · Q ${pct(s.quality)}\n` +
      `${num(s.good_qty)} good, ${num(s.scrap_qty)} scrap\n` +
      `running ${duration(s.runtime_seconds)}, down ${duration(s.downtime_seconds)}`;

    row.append(who, bar, score);
    host.appendChild(row);
  }
}

/* ---------- state timeline ---------- */
function renderTimeline(data) {
  FS.kit.timeline($("#timeline"), data);
}

/* ---------- downtime pareto ---------- */
function renderPareto(data) {
  const host = $("#pareto");
  host.replaceChildren();
  if (!data.reasons.length) {
    return empty(host, "No downtime in this window — nothing to explain.");
  }

  const left = 96, right = 34, top = 8, barH = 24, gap = 8;
  const width = Math.max(host.clientWidth || 420, 360);
  const plot = width - left - right;
  const height = top + data.reasons.length * (barH + gap) + 24;
  const max = data.reasons[0].seconds || 1;

  const chart = svg(width, height);
  data.reasons.forEach((reason, index) => {
    const y = top + index * (barH + gap);
    add(chart, "text", { x: left - 8, y: y + barH / 2 + 4, class: "row-label", "text-anchor": "end" },
        reason.reason.length > 14 ? reason.reason.slice(0, 13) + "…" : reason.reason);
    const w = Math.max((reason.seconds / max) * plot, 2);
    const rect = add(chart, "rect", {
      x: left, y, width: w, height: barH, rx: 3,
      fill: reason.reason === "unlabelled" ? "var(--unknown)" : "var(--down)",
      opacity: reason.reason === "unlabelled" ? 0.55 : 0.85,
    });
    add(rect, "title", {}, `${reason.reason}: ${duration(reason.seconds)} over ${reason.events} stop(s)
${Object.entries(reason.machines).map(([m, s]) => `${m} ${duration(s)}`).join(", ")}`);
    add(chart, "text", { x: left + w + 6, y: y + barH / 2 + 4, class: "bar-label" }, duration(reason.seconds));
  });

  // Cumulative share — the line that tells you where to stop reading.
  if (data.reasons.length > 1) {
    const points = data.reasons.map((r, i) => {
      const x = left + (r.cumulative ?? 0) * plot;
      return `${x},${top + i * (barH + gap) + barH / 2}`;
    });
    add(chart, "polyline", { points: points.join(" "), class: "cum-line" });
  }
  host.appendChild(chart);
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

/* ---------- load ---------- */
async function load() {
  // The shared header has a live dot; this screen finally drives it.
  const flip = (ok) => {
    const dot = document.getElementById("live-dot");
    const text = document.getElementById("live-text");
    if (dot) dot.className = "dot" + (ok ? "" : " bad");
    if (text) text.textContent = ok ? "live" : "reconnecting…";
  };
  const query = `?hours=${state.hours}` + (state.line ? `&line=${encodeURIComponent(state.line)}` : "");
  const banner = $("#banner");
  banner.classList.add("hidden");

  const [oee, timeline, downtime, production] = await Promise.all([
    api(`/analysis/oee${query}`),
    api(`/analysis/timeline${query}`),
    api(`/analysis/downtime${query}`),
    api(`/analysis/production${query}`),
  ]);

  $("#kpi-oee").textContent = pct(oee.line_oee);
  $("#kpi-constraint").textContent = oee.constraint ? `worst: ${oee.constraint}` : "";
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
  $("#window-note").textContent =
    `${utc(oee.window.start).toLocaleString()} → ${utc(oee.window.end).toLocaleTimeString()}`;

  renderOee(oee);
  renderTimeline(timeline);
  renderPareto(downtime);
  renderProduction(production);

  const picker = $("#tag-machine");
  const codes = oee.stations.map((s) => s.code);
  if (!codes.includes(state.machine)) state.machine = oee.constraint || codes[0] || null;
  picker.replaceChildren(...codes.map((code) => new Option(code, code, false, code === state.machine)));
  if (state.machine) renderTag(await api(`/analysis/tag/${encodeURIComponent(state.machine)}${query}`));
  else empty($("#tag"), "No machines on this line.");
  flip(true);
}

async function boot() {
  await FS.whoami().catch(() => {});
  const lines = await api("/analysis/lines");
  const select = $("#line");
  select.replaceChildren(
    ...lines.map((l) => new Option(`${l.code} — ${l.name} (${l.stations})`, l.code))
  );
  const wanted = new URL(window.location).searchParams.get("line");
  state.line = lines.some((l) => l.code === wanted) ? wanted : (lines.length ? lines[0].code : null);
  if (state.line) select.value = state.line;

  select.onchange = () => { state.line = select.value; load().catch(fail); };
  $("#hours").onchange = (e) => { state.hours = parseFloat(e.target.value); load().catch(fail); };
  $("#tag-machine").onchange = async (e) => {
    state.machine = e.target.value;
    const query = `?hours=${state.hours}` + (state.line ? `&line=${encodeURIComponent(state.line)}` : "");
    renderTag(await api(`/analysis/tag/${encodeURIComponent(state.machine)}${query}`));
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
  flip(false);
  const banner = $("#banner");
  banner.textContent = error.message;
  banner.classList.remove("hidden");
}

boot().catch(fail);
