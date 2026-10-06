/* Quality › SPC: the individuals chart, with the two questions kept apart.

   Is the process stable (control limits from its own variation, four
   Western Electric rules)? Is it capable (Cp, Cpk against the specification,
   withheld while unstable)? The Quality screen plots readings against spec;
   this one shows whether the process is behaving, which spec limits alone
   never say. Drawn with the shared kit, from the API's own numbers.

   AND, since 2026-10-05, WHY A READING IS WHERE IT IS. Click a point and the
   panel beside the chart fills with the records behind it: the gauge that took
   it and its calibration, the same characteristic by the other gauge in the
   hour either side, this station's process values over the ten minutes before
   it with the reading marked, what the machine was doing and what it had just
   come out of, and the stops, maintenance orders and findings in that window.

   There is no model in it and nothing is worked out here. Every number on the
   panel is a number `/quality/spc/{material}/{characteristic}/point/{check}`
   measured - the control limits are the chart's, the trends are
   `/analysis/tag`'s, the gauge's due date is the register's - and this file
   lays them out. A panel that did its own arithmetic would be a second opinion
   about a plant that has one (house rule 6, and the chart contract's rule 1).

   The panel's column is on the page before anything is clicked, so opening a
   reading moves nothing else: Scott asked for the panel beside the chart and
   for nothing else to move, and a column that appeared on the first click
   would slide the chart out from under the dot he had just hit. */

const { $, el, api, fmt, kit } = window.FS;

const REFRESH_MS = 10000;
let specs = [];
let chosen = null;
let timer = null;
/* Which reading the panel is open on, so a refresh of the chart redraws the
   selection rather than losing it, and so clicking the same dot twice does
   not re-fetch. Null until somebody opens one. */
let openCheck = null;

function live(ok) {
  $("#live-dot").className = "dot" + (ok ? " ok" : " bad");
  $("#live-text").textContent = ok ? "live" : "reconnecting…";
}

function fail(error) {
  live(false);
  const banner = $("#banner");
  banner.textContent = error.message;
  banner.classList.remove("hidden");
}

const num = (v, d = 3) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));

function draw(data) {
  const host = $("#chart");
  host.replaceChildren();
  if (!data.points.length) return kit.empty(host, "No readings for this characteristic yet.");

  /* This screen draws individuals. A characteristic inspected several pieces
     at a time gets X-bar and R instead (decision 0040): its points are sample
     means and it comes with a second chart of the sample ranges. Drawing the
     means on this chart would put five bottles' average where a reader expects
     one bottle's reading, and the dot would open a panel about a check that is
     one fifth of the point. So it says so, in one sentence, and draws nothing.
     The figures below the chart are the samples' own and are true as they
     stand. Drawing it is the follow-up. */
  if (data.kind && data.kind !== "imr") {
    return kit.empty(host,
      `${data.characteristic} is inspected ${data.sample_size} pieces at a time, so its `
      + `chart is X-bar and R: each point is the mean of a sample, with a range chart `
      + `beside it. This screen draws individuals only and cannot draw that one yet, so `
      + `it draws nothing rather than show you means as if they were single readings. `
      + `The centre, the limits, the capability and the verdict below are the samples' own.`);
  }

  const left = 52, right = 14, top = 12, bottom = 26;
  const width = Math.max(host.clientWidth || 900, 620);
  const height = 260;
  const plot = width - left - right;
  const plotH = height - top - bottom;
  const values = data.points.map((p) => p.value);
  const guides = [data.lower_spec, data.upper_spec,
                  data.control && data.control.lower, data.control && data.control.upper]
    .filter((v) => v !== null && v !== undefined);
  let lo = Math.min(...values, ...guides), hi = Math.max(...values, ...guides);
  if (hi === lo) { hi += 0.5; lo -= 0.5; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const x = (i) => left + (i / Math.max(values.length - 1, 1)) * plot;
  const y = (v) => top + plotH - ((v - lo) / (hi - lo)) * plotH;

  const chart = kit.svg(width, height);
  for (let i = 0; i <= 3; i++) {
    const v = lo + ((hi - lo) * i) / 3;
    kit.add(chart, "line", { x1: left, y1: y(v), x2: left + plot, y2: y(v), class: "grid-line" });
    kit.add(chart, "text", { x: left - 6, y: y(v) + 3, class: "axis", "text-anchor": "end" }, v.toFixed(Math.abs(hi - lo) < 10 ? 2 : 0));
  }
  const guide = (v, cls, label) => {
    if (v === null || v === undefined) return;
    kit.add(chart, "line", { x1: left, y1: y(v), x2: left + plot, y2: y(v), class: cls });
    kit.add(chart, "text", { x: left + plot - 4, y: y(v) - 3, class: "axis", "text-anchor": "end" }, `${label} ${v}`);
  };
  guide(data.lower_spec, "spec-line", "LSL");
  guide(data.upper_spec, "spec-line", "USL");
  if (data.control) {
    guide(data.control.lower, "limit-line", "LCL");
    guide(data.control.upper, "limit-line", "UCL");
    guide(data.control.centre, "centre-line", "x̄");
  }
  const flagged = new Set(data.signals.flatMap((s) => s.points || s.indexes || (s.index !== undefined ? [s.index] : [])));
  kit.add(chart, "polyline", { points: values.map((v, i) => `${x(i)},${y(v)}`).join(" "), class: "trend-line" });
  values.forEach((v, i) => {
    const point = data.points[i];
    const selected = point.check !== undefined && point.check === openCheck;
    const dot = kit.add(chart, "circle", { cx: x(i), cy: y(v),
      r: selected ? 5.5 : flagged.has(i) ? 4.5 : 2.5,
      class: (flagged.has(i) ? "spc-flag" : "spc-dot") + (selected ? " spc-selected" : "") });
    kit.add(dot, "title", {}, `${v}${data.unit ? " " + data.unit : ""} · ${kit.utc(point.ts).toLocaleString()}`
      + (point.check === undefined ? "" : " — open it for the records behind it"));
    /* A reading you can open. There is no <button> inside an SVG, so it
       carries what one would carry and answers a keyboard - the same way the
       kit's own legend and threshold controls do (style rule 6's intent).
       The id comes from the payload: the nth dot on a chart is a different
       reading every time a check is recorded, and the reading is not. */
    if (point.check === undefined) return;
    dot.setAttribute("data-check", point.check);
    dot.setAttribute("role", "button");
    dot.setAttribute("tabindex", "0");
    dot.setAttribute("aria-label",
      `Reading ${i + 1}, ${v}${data.unit ? " " + data.unit : ""} at `
      + `${fmt.stamp(point.ts)} — open the records behind it`);
    dot.addEventListener("click", () => openPoint(point.check));
    dot.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openPoint(point.check);
    });
  });
  kit.add(chart, "text", { x: left + 4, y: top + 10, class: "axis" }, `${data.material} ${data.characteristic}${data.unit ? ` (${data.unit})` : ""}`);
  host.appendChild(chart);
}

async function load() {
  if (!chosen) return;
  const [material, characteristic] = chosen.split("|");
  const data = await api(`/quality/spc/${encodeURIComponent(material)}/${encodeURIComponent(characteristic)}`);
  /* The two figures whose *meaning* changes with the sampling plan, so their
     labels change with it too. On a chart of individuals `n` is readings and
     the sigma is the process's own spread. On an X-bar and R chart `n` is
     samples - twelve points drawn from sixty bottles - and `control.sigma` is
     the spread of a *mean*, which is smaller by root n and is not what
     capability is worked out from. Printing either one under the individuals
     label would be a true number under a false word, which is the failure
     this product exists not to have. So: the count says samples and names the
     readings behind them, and the sigma stays the within-process one the
     label promises, with the sigma of a mean in the tooltip because that is
     what the control limits are drawn from. */
  const sampled = data.kind && data.kind !== "imr";
  $("#l-n").textContent = sampled ? "Samples" : "Readings";
  $("#f-n").textContent = data.n;
  $("#f-n").title = sampled
    ? `${data.n} samples of ${data.sample_size} — ${data.readings} readings`
    : "";
  $("#f-centre").textContent = data.control ? num(data.control.centre, 2) : "—";
  const within = data.control
    && (sampled ? data.control.sigma_within : data.control.sigma);
  $("#f-sigma").textContent = data.control ? num(within, 3) : "—";
  $("#f-sigma").title = sampled && data.control
    ? `Within-process sigma, R̄/d2. A sample mean of ${data.sample_size} varies by `
      + `${num(data.control.sigma, 3)}, and the control limits are drawn from that.`
    : "";
  // Capability is withheld while the chart is out of control: the verdict
  // says the number is not meaningful, so the facts row must not print it.
  // The value stays in the tooltip for the engineer who wants it anyway.
  const cap = data.capability;
  const unstable = data.stable === false;
  const capCell = (id, value, absent) => {
    const node = $(id);
    node.title = "";
    if (!cap || value === null || value === undefined) { node.textContent = absent; return; }
    if (unstable) {
      node.textContent = "withheld";
      node.title = `${num(value, 2)} - not meaningful while the process is out of control`;
      return;
    }
    node.textContent = num(value, 2);
  };
  capCell("#f-cp", cap && cap.cp, "withheld");
  capCell("#f-cpk", cap && cap.cpk, "withheld");
  capCell("#f-pp", cap && cap.pp, "—");
  const verdict = $("#verdict");
  verdict.textContent = data.verdict || data.note || "—";
  // The bar is the plant's, and the server sends it. This line held its own
  // 1.33 until the plant got the key, so a plant that moved the bar got a
  // green figure under a sentence calling it marginal.
  verdict.className = "verdict " + (data.stable === false ? "bad"
    : data.capability && data.cpk_capable !== undefined
      && data.capability.cpk >= data.cpk_capable ? "good" : "");
  $("#chart-note").textContent = data.control ? "" : `— ${data.note || "no control limits"}`;
  draw(data);
  const body = $("#signals tbody");
  body.replaceChildren();
  if (!data.signals.length) {
    const tr = el("tr"); const td = el("td", "muted", data.control ? "No rule fired. The process is in control." : "—"); td.colSpan = 4; tr.append(td); body.append(tr);
  }
  // Which of the four this plant raises a hold on. Said on every chart,
  // including the one where all four are held, because "all four" and "the
  // three this plant chose" are different plants and only one of them has a
  // rule that fires into silence by design (decision 0036).
  const rules = data.rules || [];
  const holds = data.hold_rules || [];
  const off = rules.filter((rule) => !holds.includes(rule));
  // "rule 1 and rule 2", not "rule 1, rule 2": this is a sentence somebody
  // reads, and the plant's own answer deserves to read like one.
  const list = (numbers) => numbers.map((r) => `rule ${r}`)
    .reduce((text, one, i, all) =>
      text + (i === 0 ? "" : i === all.length - 1 ? " and " : ", ") + one, "");
  $("#hold-rules").textContent = !rules.length ? ""
    : !off.length
      ? `Every rule this plant draws raises a hold: ${list(rules)}.`
      : holds.length
        ? `This plant raises a hold on ${list(holds)}. `
          + `${list(off).charAt(0).toUpperCase()}${list(off).slice(1)} `
          + `${off.length === 1 ? "is" : "are"} drawn and recorded and `
          + `${off.length === 1 ? "raises" : "raise"} no hold.`
        : "This plant raises a hold on no rule. Every firing below is drawn and "
          + "recorded, and none of them opened a non-conformance.";

  for (const s of data.signals) {
    const tr = el("tr");
    tr.append(el("td", "code", `rule ${s.rule}`));
    const where = s.points || s.indexes || (s.index !== undefined ? [s.index] : []);
    tr.append(el("td", "muted small", where.length ? `reading ${where.map((i) => i + 1).join(", ")}` : (s.at !== undefined ? `reading ${s.at + 1}` : "")));
    tr.append(el("td", null, s.description || s.meaning || s.what || JSON.stringify(s)));
    // What it set off. A chart that says a rule fired and stops there leaves
    // the reader wondering whether anybody was told.
    const acted = el("td");
    if (s.nonconformance) {
      const link = el("a", "obj", s.nonconformance);
      link.href = `/dashboard/quality?n_q=${encodeURIComponent(s.nonconformance)}&n_status=`;
      acted.append("held — ", link);
    } else if (s.held === false) {
      // Two different facts, and the reader is owed which: a rule this plant
      // does not hold on, or a firing that joined a hold already open.
      acted.append(el("span", "muted small", "no hold — this plant does not hold on this rule"));
    } else {
      acted.append(el("span", "muted small", "no hold of its own"));
    }
    tr.append(acted);
    body.append(tr);
  }
  $("#signal-count").textContent = `— ${data.signals.length}`;
  window.__fsmesPageData = data;

  /* A keyboard's way in, and a mouse's shortcut: the newest reading a rule
     fired on, or the newest reading when none did. It says which, because
     "open the flagged reading" on a chart where nothing fired would be a
     button promising something that is not there. */
  const button = $("#open-point");
  const checkAt = (index) => (data.points[index] || {}).check;
  const flaggedChecks = data.signals
    .map((s) => checkAt(s.index))
    .filter((c) => c !== undefined);
  const target = flaggedChecks.length
    ? flaggedChecks[flaggedChecks.length - 1]
    : checkAt(data.points.length - 1);
  /* On a sampled chart this button has nothing honest to open. The panel
     answers *why is this reading here*, and a point on an X-bar chart is not
     a reading: opening one of the five bottles behind it and calling it the
     point would be the same lie as drawing the means as readings. The sample
     dossier is the follow-up. Until then the button says so rather than
     going grey without a word. */
  button.disabled = sampled || target === undefined;
  button.textContent = sampled
    ? "Each point is a sample, not a reading"
    : flaggedChecks.length
      ? "Open the flagged reading" : "Open the newest reading";
  button.title = sampled
    ? `A point here is the mean of ${data.sample_size} bottles. The panel reads `
      + "one reading's records, so there is nothing for it to open yet."
    : "";
  button.onclick = () => openPoint(target);
}

/* ======================================================================
   WHY THIS READING IS HERE — the dossier panel

   One fetch, one draw. Every line below is a value the read measured; the
   only thing this code decides is where on the page it goes. The three
   sentences that are judgments — the chart's verdict, the gauge's resolution
   verdict and each block's coverage — are the server's words, printed.

   `data-state` and `data-point` on the panel are how a test (and a person
   watching a slow connection) can tell the three states apart: idle, a fetch
   in flight, and a reading drawn. `data-point` is written last and carries
   the reading's own id, so waiting on it is waiting on the thing asserted
   rather than on the box that will hold it (#112, 2026-09-26).
   ====================================================================== */

/* How many stops the panel lists before it starts counting instead. The Gantt
   beside the list draws every one of them, and the line under it states the
   total, the seconds and the named/unlabelled split - so nothing is hidden;
   what this stops is a table of twenty identical two-second idle blips with
   the one named changeover at the bottom of it. */
const STOPS_SHOWN = 6;

/* And how many findings, for the same reason and a sharper one. An excursion
   raises a finding per bad piece: twenty-five of them in a twelve-minute window
   on a replayed line made this panel five thousand pixels tall, and the two
   that mattered - the SPC holds - were buried under twenty-three out-of-spec
   readings all saying the same thing. Majors come first, the total is stated,
   and the Quality screen lists every one of them. */
const FINDINGS_SHOWN = 6;

/* How much of this block's own window anybody watched — three different facts
   and three different sentences. `coverage: "absent"` is the third: this block
   is a list of the records in a window, not a rate over a watched one, and
   printing "watched —%" about it is the confusion the word exists to stop. */
function watchedLine(block) {
  if (!block) return null;
  if (block.coverage === "absent") {
    return block.coverage_note || "No coverage figure: this is a list of records.";
  }
  if (!("coverage" in block)) return null;
  if (block.coverage === null || block.coverage === undefined) {
    return block.coverage_note || "How much of this window was watched is unknown.";
  }
  return `Watched ${fmt.pct(block.coverage)} of this window`
       + (block.coverage_note ? ` — ${block.coverage_note}` : "");
}

/* One block of the dossier: a heading, its records, and the line saying how
   much of its own window was watched. The line is added by the caller at the
   end so it is always last, whatever the block put in between. */
function pointBlock(title) {
  const section = el("section", "point-block");
  section.append(el("h3", null, title));
  return section;
}

function closeWith(section, block) {
  const said = watchedLine(block);
  if (said) section.append(el("p", "point-watched", said));
  return section;
}

/* Label and value in two columns. A value nobody recorded prints "—" and its
   label stays: a row that vanished would read as a question nobody asked
   (style rule 8 — unknown is never 0 and never absent). */
function facts(rows) {
  const list = el("dl", "point-facts");
  for (const [label, value, mono] of rows) {
    list.append(el("dt", null, label));
    const dd = el("dd", mono ? "mono" : null);
    if (value instanceof Node) dd.append(value);
    else dd.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
    list.append(dd);
  }
  return list;
}

/* A table, stating its own total the way every list in this product does. */
function pointTable(headings, rows, empty) {
  if (!rows.length) return el("p", "muted small", empty);
  const wrap = el("div", "table-wrap");
  const table = el("table");
  const head = el("thead");
  const hr = el("tr");
  for (const h of headings) hr.append(el("th", null, h));
  head.append(hr);
  table.append(head);
  const body = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    for (const cell of row) {
      const td = el("td");
      if (cell instanceof Node) td.append(cell);
      else td.textContent = cell === null || cell === undefined ? "—" : String(cell);
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(body);
  wrap.append(table);
  return wrap;
}

/* The chart off the page, with its footer on it. `FS.kit.export` is the one
   implementation, so what lands in a slide carries the total and the coverage
   sentence this panel is reading from (style rule 15). */
async function exportPointChart(node, name, format) {
  try {
    const blob = await kit.export(node, format);
    const file = `${name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")
      || "chart"}.${format}`;
    const url = URL.createObjectURL(blob);
    const link = el("a");
    link.href = url;
    link.download = file;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    FS.toast(`Saved ${file}, footer and all.`);
  } catch (err) {
    fail(err);
  }
}

/* One small chart inside the panel, with the two buttons that take it away.
   The kit refuses an envelope it cannot draw honestly, by name; that refusal
   is printed rather than swallowed, because a second-choice shape would be
   this file deciding what the reader is looking at. */
function smallChart(host, title, kind, envelope, options) {
  const box = el("div", "point-chart");
  if (title) box.append(el("h4", null, title));
  const inner = el("div", "chart-host");
  box.append(inner);
  host.append(box);
  let node = null;
  try {
    node = kit.draw(inner, kind, envelope, { width: inner.clientWidth || 420, ...options });
  } catch (err) {
    inner.append(el("p", "empty", `This could not be drawn as a ${kind}: ${err.message}`));
    return box;
  }
  const tools = el("div", "chart-tools");
  for (const format of ["svg", "png"]) {
    const button = el("button", "ghost small", format.toUpperCase());
    button.dataset.export = format;
    button.setAttribute("aria-label", `Export “${title}” as ${format.toUpperCase()}`);
    button.addEventListener("click", () => exportPointChart(node, title, format));
    tools.append(button);
  }
  box.append(tools);
  return box;
}

/* ---------- the blocks ---------- */

function readingBlock(d) {
  const r = d.reading;
  const section = pointBlock("The reading");
  const value = el("p", "point-value");
  value.append(String(r.value));
  if (r.unit) value.append(el("span", "unit", r.unit));
  section.append(value);
  const spec = r.lower_spec === null && r.upper_spec === null
    ? "no limits on the specification"
    : `${r.lower_spec === null ? "—" : r.lower_spec} to `
      + `${r.upper_spec === null ? "—" : r.upper_spec}${r.unit ? " " + r.unit : ""}`;
  section.append(facts([
    ["Verdict", r.result === "pass" ? "in specification" : "out of specification"],
    ["Specification", spec, true],
    ["Taken at", fmt.stamp(r.ts)],
    ["By", r.checked_by],
    ["Order", r.work_order, true],
    ["Shift", r.shift ? `${r.shift}${r.shift_day ? ` (${fmt.day(r.shift_day)})` : ""}` : null],
    ["Station", r.equipment ? `${r.equipment.code} — ${r.equipment.name}` : null, true],
    ["From", r.source_system],
    /* Their verdict when another system sent one. The two disagreeing is a
       finding, not an error, and dropping theirs would hide it. */
    ["Their verdict", r.supplied_result],
  ]));
  return section;
}

function ruleBlock(d) {
  const section = pointBlock("What the rules said");
  const chart = d.chart || {};
  const signal = chart.signal;
  if (!chart.on_chart) {
    section.append(el("p", "muted small", chart.note || "This reading is not on the chart."));
  } else if (signal) {
    section.append(el("p", null, `Rule ${signal.rule}: ${signal.what}.`));
  } else {
    section.append(el("p", "muted small",
      "No rule fired on this reading. It is on the chart and the rules were run "
      + "when it was recorded."));
  }
  section.append(facts([
    ["Reading", chart.reading ? `${chart.reading} of ${chart.readings}` : null],
    ["Centre", chart.control ? chart.control.centre : null, true],
    ["Control limits", chart.control
      ? `${chart.control.lower} to ${chart.control.upper}` : null, true],
    ["Sigma (within)", chart.control ? chart.control.sigma : null, true],
  ]));
  /* What the MES actually acted on when the reading arrived, which is not the
     same question as what the chart says now: the chart is drawn over a
     history that has moved since. */
  const recorded = (d.recorded && d.recorded.signals) || [];
  section.append(el("p", "muted small",
    `${recorded.length} rule firing${recorded.length === 1 ? "" : "s"} recorded on this `
    + "reading when it arrived" + (recorded.length ? ":" : ".")));
  if (recorded.length) {
    section.append(pointTable(["Rule", "What it means", "What it raised"],
      recorded.map((row) => {
        let raised = el("span", "muted small", "no hold of its own");
        if (row.nonconformance) {
          const link = el("a", "obj", row.nonconformance);
          link.href = `/dashboard/quality?n_q=${encodeURIComponent(row.nonconformance)}&n_status=`;
          raised = el("span");
          raised.append(`${row.nonconformance_status || "held"} — `, link);
        }
        return [`rule ${row.rule}`, row.what, raised];
      }), ""));
  }
  return closeWith(section, d.recorded);
}

function gaugeBlock(d) {
  const section = pointBlock("The gauge, and whether it can be believed");
  const g = d.gauge || {};
  if (!g.gauge) {
    /* Not recorded is not "no gauge took it". The first question anybody asks
       about a point on a control chart must not look answered when it isn't. */
    section.append(el("p", "muted small", g.note || "No gauge is recorded against this reading."));
  } else {
    const due = g.gauge.overdue ? "overdue"
      : g.gauge.due_soon ? `due in ${g.gauge.days_until_due} d`
      : g.gauge.days_until_due === null ? null
      : `in ${g.gauge.days_until_due} d`;
    const cal = g.last_calibration;
    section.append(facts([
      ["Gauge", `${g.gauge.code} — ${g.gauge.name}`, true],
      ["Status", g.gauge.status],
      ["Where", g.gauge.location, true],
      ["Last calibrated", g.gauge.never_calibrated ? "never"
        : `${fmt.day(g.gauge.last_calibrated)}${cal ? ` — ${cal.result}, by ${cal.performed_by}` : ""}`],
      ["Next due", g.gauge.due_on ? `${fmt.day(g.gauge.due_on)}${due ? ` (${due})` : ""}` : null],
      ["Resolves to", g.gauge.resolution, true],
    ]));
    if (g.resolution_check) {
      /* The plant's own verdict on whether this gauge can judge this
         tolerance — printed, never re-derived here: the two bars behind it
         are the plant's and this file does not hold them. */
      section.append(el("p", "muted small", g.resolution_check.verdict));
    }
  }

  /* Did the process move, or did the gauge? The same characteristic by every
     gauge in the hour either side. */
  const n = d.neighbours || {};
  section.append(el("h3", "mt", "The same characteristic, by gauge, "
    + `${n.hours ? `±${n.hours} h` : "either side"}`));
  section.append(pointTable(
    ["Gauge", "Readings", "Mean", "Lowest", "Highest", "Against this gauge"],
    (n.by_gauge || []).map((row) => [
      row.gauge || "not recorded",
      row.readings,
      row.mean,
      row.min,
      row.max,
      row.this_reading ? "this reading's gauge"
        : row.difference_to_this_gauge === null ? "—"
        : `${row.difference_to_this_gauge > 0 ? "+" : ""}${row.difference_to_this_gauge}`,
    ]),
    "No readings of this characteristic in that window."));
  section.append(el("p", "muted small",
    `${n.total || 0} reading${n.total === 1 ? "" : "s"} by ${n.gauges || 0} `
    + `gauge${n.gauges === 1 ? "" : "s"}. ${n.note || ""}`));
  return closeWith(section, n);
}

function machineBlock(d) {
  const section = pointBlock("What the machine was doing");
  const m = d.machine;
  if (!m) {
    section.append(el("p", "muted small",
      "No station is recorded against this reading, so there is no machine to ask about. "
      + "A person with a gauge records none, and working one out from the order's route "
      + "would name a machine nobody stood at."));
    return section;
  }
  const state = m.state;
  const previous = m.previous;
  section.append(facts([
    ["At the reading", state
      ? `${state.state}${state.reason ? ` — ${state.reason}` : ""}` : null],
    ["In that state for", state ? kit.duration(m.seconds_in_state) : null],
    ["Just before", previous
      ? `${previous.state}${previous.reason ? ` — ${previous.reason}` : ""}` : null],
    ["Which ended", m.seconds_since_previous_ended === null ? null
      : `${kit.duration(m.seconds_since_previous_ended)} before the reading`],
  ]));
  if (m.note) section.append(el("p", "muted small", m.note));
  if (state && state.reason_source) {
    section.append(el("p", "muted small",
      `The word on that interval came from ${state.reason_source}, not from this MES.`));
  }

  const stops = d.stops || {};
  section.append(el("h3", "mt", "Stops in this window"));
  /* Longest first, and only a few rows. On a replayed line (2026-10-05) a
     twelve-minute window held twenty-three stretches, twenty-two of them
     two-second idle blips the floor deliberately does not label - and the one
     thing a person needed to see, a named changeover, was the twenty-third row
     of a table of identical lines. Nothing is dropped: the count, the seconds
     and the named/unlabelled split are stated under it (style rule 4), and the
     Gantt beside it draws every interval. */
  const listed = [...(stops.stops || [])].sort((a, b) => b.seconds - a.seconds);
  const shown = listed.slice(0, STOPS_SHOWN);
  section.append(pointTable(["State", "Reason", "For", "From"],
    shown.map((row) => [
      row.state,
      /* House rule 3: an unlabelled stop is reported as unlabelled, never
         filed under a reason nothing observed. */
      row.reason || el("span", "muted small", "unlabelled"),
      kit.duration(row.seconds),
      fmt.clock(row.start),
    ]),
    "The machine did not stop in this window."));
  if (stops.total) {
    const rest = listed.length - shown.length;
    section.append(el("p", "muted small",
      `${stops.total} stretch${stops.total === 1 ? "" : "es"} not running, `
      + `${kit.duration(stops.seconds)} in all — ${stops.labelled} named, `
      + `${stops.unlabelled} unlabelled.`
      + (rest > 0
        ? ` Longest ${shown.length} listed; ${rest} more, each `
          + `${kit.duration(shown[shown.length - 1].seconds)} or shorter — all of `
          + "them on the chart below."
        : "")));
  }
  return closeWith(section, d.timeline);
}

function timelineChart(host, d) {
  if (!d.timeline) return;
  const section = pointBlock("The window, as the machine lived it");
  host.append(section);
  smallChart(section, `${d.reading.equipment ? d.reading.equipment.code : "station"} states`,
             "states", d.timeline,
             { markers: [{ t: d.window.at, label: "this reading" }], rowHeight: 20 });
}

function tagsBlock(d) {
  const section = pointBlock("This station's process values");
  const tags = d.tags || {};
  const trends = tags.trends || [];
  if (!trends.length) {
    section.append(el("p", "muted small",
      d.reading.equipment
        ? "This station published no process values in this window. That is not the "
          + "same as the values having been flat."
        : "No station is recorded against this reading, so there are no tags to draw."));
    return section;
  }
  section.append(el("p", "muted small",
    `${tags.shown} of ${tags.total} signal${tags.total === 1 ? "" : "s"} this station `
    + `published in this window, over the ${d.window.before_minutes} minutes before the `
    + `reading and the ${d.window.after_minutes} after. The reading is marked on each.`));
  for (const trend of trends) {
    const box = smallChart(section, trend.tag || "value", "line", trend, {
      value: "mean",
      /* Min and max along with the mean, so a one-second excursion that an
         average would smooth away still appears. */
      band: { low: "min", high: "max" },
      /* Not zero-based, and the chart says so on itself: a fill weight around
         500 g on a zero axis is a flat line, and the whole question is the
         spread (chart contract rule 4). */
      zero: false,
      height: 130,
      markers: trend.markers,
      y: { label: trend.tag },
    });
    /* A tag that said nothing draws an empty box, and an empty box explains
       nothing. Looking at a replayed line on 2026-10-05, the filler's
       fill-weight setpoint — set once when the order started and never touched
       — was drawn exactly like a tag nobody had been watching. The last value
       it reported before the window is the fact that tells those apart, so it
       goes under the box in so many words, and the chart's own window line
       above says whether the window was truncated. */
    if (trend.samples === 0) {
      const last = trend.last_before_window;
      box.append(el("p", "point-watched", last
        ? `Nothing in the window drawn — it last reported ${last.value} at `
          + `${fmt.stamp(last.ts)}, ${kit.duration(last.seconds_before_window)} `
          + "before the window opened. A value that has not changed publishes "
          + "nothing."
        : "Nothing in the window drawn, and this MES holds no earlier sample of "
          + "it either."));
    }
  }
  return section;
}

function elseBlock(d) {
  const section = pointBlock("Anything else in this window");
  const maint = d.maintenance || {};
  section.append(el("h3", null, "Maintenance"));
  section.append(pointTable(["Order", "Kind", "Status", "What", "Raised"],
    (maint.orders || []).map((row) => [
      row.code, row.kind, row.status, row.summary, fmt.clock(row.raised_at),
    ]),
    maint.note || "No maintenance order on this station touches this window."));
  if (maint.total) {
    section.append(el("p", "muted small",
      `${maint.total} order${maint.total === 1 ? "" : "s"} touching this window.`));
  }

  const found = d.findings || {};
  section.append(el("h3", "mt", "Findings"));
  /* Majors first, then newest, and only a few rows. An excursion raises one
     finding per bad piece: on a replayed line a twelve-minute window held
     twenty-five of them, which made this panel five thousand pixels tall and
     buried the two that mattered - the SPC holds - under twenty-three
     out-of-spec readings saying the same thing. Nothing is dropped: the total
     and the scope are stated under it, and the Quality screen lists them all. */
  const rows = [...(found.nonconformances || [])].sort((a, b) =>
    (a.severity === "major" ? 0 : 1) - (b.severity === "major" ? 0 : 1)
    || String(b.created_at).localeCompare(String(a.created_at)));
  const shown = rows.slice(0, FINDINGS_SHOWN);
  section.append(pointTable(["Finding", "Severity", "Status", "What", "Raised"],
    shown.map((row) => {
      const link = el("a", "obj", row.code);
      link.href = `/dashboard/quality?n_q=${encodeURIComponent(row.code)}&n_status=`;
      return [link, row.severity, row.status,
              row.same_order ? `${row.description} (same order)` : row.description,
              fmt.clock(row.created_at)];
    }),
    "Nothing was raised in this window."));
  const majors = rows.filter((row) => row.severity === "major").length;
  const rest = rows.length - shown.length;
  const all = el("a", "obj", "the Quality screen");
  all.href = "/dashboard/quality?n_status=";
  const line = el("p", "muted small");
  line.append(`${found.total || 0} raised in this window`
    + (majors ? `, ${majors} of them major` : "")
    + (rest > 0 ? `. Majors and the newest ${shown.length} listed; ${rest} more on ` : ". "));
  if (rest > 0) line.append(all, ". ");
  line.append(found.scope || "");
  section.append(line);
  return closeWith(section, found);
}

/* ---------- drawing it, and asking for it ---------- */

function drawPoint(d) {
  const body = $("#point-body");
  body.replaceChildren();
  $("#point-which").textContent =
    `— reading ${d.reading.check}, ${fmt.stamp(d.reading.ts)}`;
  body.append(readingBlock(d));
  body.append(ruleBlock(d));
  body.append(gaugeBlock(d));
  body.append(machineBlock(d));
  timelineChart(body, d);
  body.append(tagsBlock(d));
  body.append(elseBlock(d));
  /* The window the whole panel is about, and how much of it anybody watched.
     Last, because it qualifies everything above it. */
  const foot = el("p", "point-watched",
    `${fmt.clock(d.window.start)}–${fmt.clock(d.window.end)}. `
    + (watchedLine(d) || ""));
  body.append(foot);
  $("#point-idle").hidden = true;
  body.hidden = false;
}

async function openPoint(check) {
  const node = $("#point-panel");
  if (check === undefined || check === null) return;
  node.dataset.state = "loading";
  /* Cleared while the fetch is in flight: the attribute means "this reading is
     drawn", and leaving the last one on it would have a test — and a reader on
     a slow connection — believing the panel beside them is about the dot they
     just clicked. */
  node.dataset.point = "";
  $("#point-which").textContent = "— opening…";
  const [material, characteristic] = chosen.split("|");
  try {
    /* One template literal and not two concatenated: `test_route_coverage`
       matches a route textually, and a path broken across a `+` is a path no
       screen appears to call. */
    const data = await api(`/quality/spc/${encodeURIComponent(material)}/${encodeURIComponent(characteristic)}/point/${encodeURIComponent(check)}`);
    openCheck = check;
    drawPoint(data);
    node.dataset.state = "open";
    /* Written last, after everything above is on the page. */
    node.dataset.point = String(check);
    window.__fsmesPointData = data;
    /* The chart redraws so the dot the reader opened is ringed. Nothing else
       on the page moves: the panel's column was always there. */
    if (window.__fsmesPageData) draw(window.__fsmesPageData);
  } catch (error) {
    node.dataset.state = "error";
    $("#point-which").textContent = "";
    $("#point-body").replaceChildren(el("p", "empty",
      `This reading's records could not be read: ${error.message}`));
    $("#point-body").hidden = false;
    $("#point-idle").hidden = true;
  }
}

(async function boot() {
  await FS.whoami().catch(() => {});
  // Page by page, to a stated ceiling. The picker needs every
  // material/characteristic pair to group them, and a single response is no
  // longer the whole table - so it reads to the end and says whether it got
  // there.
  const all = await FS.allPages("/quality/specs", { limit: 500, cap: 2000 });
  specs = all.items;
  const scope = $("#spec-scope");
  if (scope) {
    scope.textContent = all.complete
      ? `${all.total.toLocaleString()} characteristic${all.total === 1 ? "" : "s"}`
      : `first ${specs.length.toLocaleString()} of ${all.total.toLocaleString()} characteristics`;
  }
  const select = $("#spec");
  // Grouped by material: 127 specifications in one flat list is a scroll,
  // not a choice.
  const byMaterial = new Map();
  for (const s of specs) { if (!byMaterial.has(s.material)) byMaterial.set(s.material, []); byMaterial.get(s.material).push(s); }
  select.replaceChildren(...[...byMaterial.entries()].map(([material, rows]) => {
    const group = document.createElement("optgroup");
    group.label = material;
    group.append(...rows.map((s) => new Option(s.characteristic, `${s.material}|${s.characteristic}`)));
    return group;
  }));
  const wanted = new URL(location).searchParams.get("spec");
  chosen = specs.some((s) => `${s.material}|${s.characteristic}` === wanted) ? wanted : (specs.length ? `${specs[0].material}|${specs[0].characteristic}` : null);
  if (!chosen) return fail(new Error("No quality specification is defined yet."));
  select.value = chosen;
  select.addEventListener("change", () => { chosen = select.value; load().then(() => live(true)).catch(fail); });
  const run = () => load().then(() => live(true)).catch(fail);
  await run();
  timer = setInterval(run, REFRESH_MS);
})().catch(fail);
