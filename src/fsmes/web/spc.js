/* Quality › SPC: a control chart of either kind, with the two questions kept
   apart.

   WHICH kind follows the sampling plan and nothing else (decision 0040, and
   `kind` in the envelope says which in a word). A characteristic inspected one
   piece at a time is individuals and moving range; one inspected n at a time
   is X-bar and R - the mean of each sample above, the spread inside each
   sample below. One layout draws both: the same geometry, the same x scale
   shared by the two halves, the same one <svg> so one export carries both, the
   same dot that opens the records behind the point. Two layouts would be two
   charts to keep honest, and on 2026-10-06 the sampled one arrived as the
   second kind rather than as a second screen.

   The heading, the legend and the two sentences under the chart follow `kind`
   too, with or without points: a lab plant drew a sampled chart with no
   samples yet under the words INDIVIDUALS AND MOVING RANGE, and a true
   picture with a false caption is believed caption-first.

   Is the process stable (control limits from its own variation, four Western
   Electric rules on the individuals half and, since 2026-10-06, the one rule
   the moving-range half has)? Is it capable (Cp, Cpk against the
   specification, withheld while unstable)? The Quality screen plots readings against spec;
   this one shows whether the process is behaving, which spec limits alone
   never say. Drawn with the shared kit, from the API's own numbers.

   AND, since 2026-10-05, WHY A POINT IS WHERE IT IS. Click one and the panel
   beside the chart fills with the records behind it: the gauge that took it
   and its calibration, the same characteristic by the other gauge in the hour
   either side, this station's process values over the ten minutes before it
   with the reading marked, what the machine was doing and what it had just
   come out of, and the stops, maintenance orders and findings in that window.

   On a sampled chart the point is an average, so the panel opens the n
   readings behind it - each one's distance from the sample's own mean, and the
   one furthest out - and its window is the stretch those n readings span
   rather than an instant, with every one of them marked on each trend. Two
   blocks differ; nothing below them knows which kind it is reading.

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
/* And which sample, on a chart whose points are sample means. Two variables
   and not one: a check id and a sample id are both integers and both small,
   and one variable holding either would ring the dot numbered 7 on the wrong
   chart the first time a reader switched characteristic. */
let openSample = null;

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

/* The two halves, in one <svg>, drawn by the kit.

   The picture itself is `FS.kit`'s `spc` shape since 2026-10-07 and this
   function is the twelve lines that hand it the payload. It was 250 lines of
   geometry here until then, which meant the AI tab had no way to draw a
   control chart at all: asked to put one in an answer it fell back on `line`
   and produced thirty sample means on a zero-based axis with no limits, no
   spec band and no flagged dots. One implementation, drawn two places.

   What stayed on this page is what the page knows and the kit must not: that a
   dot here opens the records behind it (`openable`), and which point the panel
   beside the chart is already open on (`selected`). The kit dispatches
   `fs-spc-open` and this file decides what opening means - the same division
   the graph's expand event keeps. */

/* Which chart this is, in one place and in the server's own word (`kind`,
   decision 0040). Two kinds are drawn: `imr`, one piece at a time with the
   gap between consecutive readings below it, and `xbar_r`, a sample of n
   pieces whose points are the sample means with the spread inside each sample
   below. Anything else is a newer plant than this page, and drawing its points
   as one of these two is exactly the failure 0040 exists to stop.

   The kit's shape refuses a third word too, in its own sentence. Both checks
   are deliberate: the kit's is the contract, and this one is the SCREEN's, so
   a reader gets the page's own words about the characteristic they picked
   rather than a thrown error in a console they will never open. */
const INDIVIDUALS = "imr";
const SAMPLED = "xbar_r";
const sampledChart = (data) => (data && data.kind) === SAMPLED;

/* The question this chart would be asked about, in the words Scott asked it in
   (2026-10-07): a plant manager who wants two sentences and one picture. The
   names travel in the sentence and nothing else travels at all - no payload,
   no scrape of this page's DOM - so the chat reads the plant itself and the
   answer is the plant's rather than this screen's copy of it.

   The sample or the reading open beside the chart is added when there is one,
   because *why is this point where it is* is the question somebody who has
   already opened a panel is holding. */
function explainQuestion(data) {
  const chart = `${data.material || ""} ${data.characteristic || ""}`.trim();
  const question =
    "I'm the plant manager and I don't understand the SPC tab. How does the "
    + `${chart} chart look? Two sentences for my boss and the one graph for a `
    + "slide.";
  if (openSample !== null && openSample !== undefined) {
    return `${question} And why is sample ${openSample} where it is?`;
  }
  if (openCheck !== null && openCheck !== undefined) {
    return `${question} And why is reading ${openCheck} where it is?`;
  }
  return question;
}

/* `FS.tabs` reads the hash as the tab name, so the question cannot ride in the
   hash: it travels as a query and the hash stays `#explore`.

   Three things travel beside the sentence, and all three are NAMES: which
   characteristic's chart this is, and which sample or which reading had a
   panel open beside it. No payload and no scrape of this page (#150): the AI
   tab reads `/quality/spc/{material}/{characteristic}` itself, so the chart it
   draws is the plant's answer and not this screen's copy of one, and a reader
   who left this tab ten minutes ago gets the chart as it is now.

   `spec` is the material and the characteristic with a slash between them,
   split on the LAST one, because a characteristic is one word of the plant's
   own and a material code is whatever a customer's ERP calls it. */
function explainLink(data) {
  const link = $("#explain-chart");
  if (!link) return;
  const query = new URLSearchParams({ ask: explainQuestion(data) });
  if (data.material && data.characteristic) {
    query.set("spec", `${data.material}/${data.characteristic}`);
  }
  if (openSample !== null && openSample !== undefined) {
    query.set("sample", String(openSample));
  } else if (openCheck !== null && openCheck !== undefined) {
    query.set("check", String(openCheck));
  }
  link.href = `/dashboard/ai?${query.toString()}#explore`;
}

function draw(data) {
  const host = $("#chart");
  /* Written before the early returns below: the link follows the chart on the
     screen, and a characteristic with no samples yet is still a chart somebody
     can ask about - the honest answer being that there are none. */
  explainLink(data);
  const kind = data.kind || INDIVIDUALS;
  if (kind !== INDIVIDUALS && kind !== SAMPLED) {
    return kit.empty(host,
      `${data.characteristic} is charted as “${kind}”, which this screen does not `
      + `draw: it draws individuals and moving range, and X-bar and R. It draws `
      + `nothing rather than plot these points as one of the two. The centre, the `
      + `limits, the capability and the verdict below are the server's own.`);
  }
  const sampled = kind === SAMPLED;
  if (!data.points.length) {
    return kit.empty(host, sampled
      ? `No samples of ${data.characteristic} yet. It is inspected `
        + `${data.sample_size} pieces at a time, so the first point appears when `
        + `${data.sample_size} readings are recorded together as one sample.`
      : "No readings for this characteristic yet.");
  }
  const chart = kit.draw(host, "spc", data, {
    width: Math.max(host.clientWidth || 900, 620),
    /* The dots are buttons on this page and plain marks everywhere else. */
    openable: true,
    /* So a refresh every ten seconds redraws the selection rather than losing
       it. Two keys and not one: a check id and a sample id are both small
       integers, and one field holding either would ring the dot numbered 7 on
       the wrong chart the first time a reader switched characteristic. */
    selected: { sample: openSample, check: openCheck },
  });
  exportTools($("#chart-tools"), chart,
              `${data.material} ${data.characteristic} `
              + `${sampled ? "X-bar and R" : "IMR"} chart`);
}

/* A dot was pressed. Wired once, on the host rather than on the marks, because
   the chart is replaced every ten seconds and a listener on a circle goes with
   it. `what` is the kit's word for which id it is handing over - a sample or a
   reading - so this never has to infer it from the number. */
function openOnClick() {
  const host = $("#chart");
  if (!host) return;
  host.addEventListener("fs-spc-open", (event) => {
    const { what, id } = event.detail || {};
    if (id === undefined || id === null) return;
    if (what === "sample") openSamplePanel(id);
    else openPoint(id);
  });
}

function exportTools(host, node, name) {
  if (!host) return;
  host.replaceChildren();
  for (const format of ["svg", "png"]) {
    const button = el("button", "ghost small", format.toUpperCase());
    button.type = "button";
    button.setAttribute("aria-label", `Save the chart as ${format.toUpperCase()}`);
    button.addEventListener("click", () => exportPointChart(node, name, format));
    host.append(button);
  }
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
  const sampled = sampledChart(data);
  words(data);
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
  /* Both halves' firings in one table, because a reader asking *what fired*
     is asking about the chart and not about one half of it. Each row says
     which rule; rule 5 is the range one - the moving range below this chart,
     or the sample range on a sampled characteristic - and each firing's own
     words say which range it was. */
  const moving = data.moving_range;
  const lower = sampled ? data.range_chart : moving;
  /* Both halves' firings in one list. On an individuals chart the lower half
     keeps its own, because a moving range is the gap between two readings and
     is indexed one along; on a sampled chart rule 5 is about one sample and is
     already in `signals` beside rules 1 to 4, so there is nothing to merge and
     merging a second copy would report every range firing twice. */
  const fired = [...data.signals,
                 ...((moving && moving.signals) || []).map((s) => ({ ...s, mr: true }))];
  if (!fired.length) {
    const tr = el("tr"); const td = el("td", "muted", data.control ? "No rule fired. The process is in control." : "—"); td.colSpan = 4; tr.append(td); body.append(tr);
  }
  // Which of them this plant raises a hold on. Said on every chart, including
  // the one where every rule is held, because "every rule" and "the four this
  // plant chose" are different plants and only one of them has a rule that
  // fires into silence by design (decision 0036).
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
          + `${off.length === 1 ? "is" : "are"} drawn on the chart and `
          + `${off.length === 1 ? "raises" : "raise"} no hold.`
        : "This plant raises a hold on no rule. Every firing below is drawn on "
          + "the chart, and none of them opened a non-conformance.";

  for (const s of fired) {
    const tr = el("tr");
    tr.append(el("td", "code", `rule ${s.rule}`));
    /* Where it fired. A moving-range firing is about two readings and says
       both: its own index counts gaps, and gap n is between readings n and
       n + 1. */
    const where = s.mr ? [s.index, s.index + 1]
      : s.points || s.indexes || (s.index !== undefined ? [s.index] : []);
    /* What a position on this chart counts. Samples on a sampled chart, and
       saying "reading 7" there would send a person looking for a bottle that
       is one of thirty-five. */
    const noun = sampled ? "sample" : "reading";
    tr.append(el("td", "muted small", where.length ? `${noun} ${where.map((i) => i + 1).join(s.mr ? " to " : ", ")}` : (s.at !== undefined ? `${noun} ${s.at + 1}` : "")));
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
  $("#signal-count").textContent = `— ${fired.length}`;
  /* The moving-range half's own sentence, under the chart. Its own, because
     the two halves answer two questions and one sentence for both is how a
     reader comes to think a process that jumps is a process behaving. */
  $("#mr-verdict").textContent = lower ? (lower.verdict || "") : "";
  window.__fsmesPageData = data;

  /* A keyboard's way in, and a mouse's shortcut: the newest reading a rule
     fired on, or the newest reading when none did. It says which, because
     "open the flagged reading" on a chart where nothing fired would be a
     button promising something that is not there. */
  const button = $("#open-point");
  /* By id, of whichever thing a point on this chart is. The nth dot is a
     different point every time a check is recorded; the sample is not. */
  const idAt = (index) => {
    const point = data.points[index] || {};
    return sampled ? point.sample : point.check;
  };
  const flaggedIds = data.signals
    .map((s) => idAt(s.index))
    .filter((id) => id !== undefined);
  const target = flaggedIds.length
    ? flaggedIds[flaggedIds.length - 1]
    : idAt(data.points.length - 1);
  button.disabled = target === undefined;
  button.textContent = flaggedIds.length
    ? (sampled ? "Open the flagged sample" : "Open the flagged reading")
    : (sampled ? "Open the newest sample" : "Open the newest reading");
  button.title = sampled
    ? `A point here is the mean of ${data.sample_size} pieces, and the panel `
      + `opens all ${data.sample_size} of them with the records around them.`
    : "";
  button.onclick = () => (sampled ? openSamplePanel(target) : openPoint(target));
}

/* The words on this screen that are about which chart it is, set in one place
   from `kind`. On 2026-10-06 a lab plant drew a sampled chart with no samples
   yet under the heading INDIVIDUALS AND MOVING RANGE, with the individuals
   legend and the individuals explainer under it - a true chart with a false
   caption, which a reader believes before they believe the picture. So these
   follow `kind` whether or not there is anything to draw. */
function words(data) {
  const sampled = sampledChart(data);
  const n = data.sample_size;
  $("#chart-heading").textContent = sampled
    ? `Sample average and range (X̄ and R)` : "Individuals and moving range";
  const legend = sampled ? [
    ["--accent", `sample average (mean of ${n})`],
    ["--running", "centre line (X̿, R̄)"],
    ["--idle", "control limits (X̿ ± A2·R̄ above, D4·R̄ and D3·R̄ below)"],
    ["--down", "specification limits"],
    ["--quality", "rule fired"],
  ] : [
    ["--accent", "reading"],
    ["--running", "centre line (x̄, R̄)"],
    ["--idle", "control limits (±3σ above, D4·R̄ below)"],
    ["--down", "specification limits"],
    ["--quality", "rule fired"],
  ];
  $("#chart-legend").replaceChildren(...legend.map(([token, label]) => {
    const span = el("span");
    const swatch = el("i", "sw");
    swatch.style.background = `var(${token})`;
    span.append(swatch, label);
    return span;
  }));
  $("#chart-explainer").textContent = sampled
    ? `The lower chart is how far apart the ${n} readings inside each sample `
      + "were. It is where the sigma behind both sets of limits comes from — "
      + "the mean of that series over d2 — so a process whose samples are "
      + "spread wider every hour has upper limits that widened with it, and "
      + "averages that look settled inside them. Read it first."
    : "The lower chart is the gap between each reading and the one before it. "
      + "It is where the sigma behind both sets of limits comes from — the mean "
      + "of that series over d2 — so a process that jumps between consecutive "
      + "readings has limits worth less than they look, whatever the upper "
      + "chart says.";
  $("#point-hint").textContent = sampled
    ? `Click a sample to see the ${n} readings behind it — or tab to one and `
      + "press Enter. A dot on the lower chart is the same sample's spread and "
      + "opens the same readings. The button opens the newest sample a rule "
      + "fired on, and the newest sample when none did."
    : "Click a reading to see the records behind it — or tab to one and press "
      + "Enter. A dot on the lower chart is the gap between two readings and "
      + "opens the later of them. The button opens the newest reading a rule "
      + "fired on, and the newest reading when none did.";
  $("#point-title").textContent = sampled
    ? "Why this sample is here" : "Why this reading is here";
  $("#point-idle").textContent = sampled
    ? `No sample is open. Click one on the chart beside this and the ${n} `
      + "readings behind it appear here, each with its distance from the "
      + "sample's own mean and which of them is furthest out — with every "
      + "record this MES holds around them: the gauge that took them and when "
      + "it was last calibrated, the same characteristic by the other gauge in "
      + "the hour either side, this station's process values over the minutes "
      + "the sample spans and the ten before, what the machine was doing, and "
      + "anything else that happened in that window. Nothing is modelled or "
      + "guessed — every line is a record, and each block says how much of its "
      + "window anybody was watching."
    : "No reading is open. Click one on the chart beside this and every record "
      + "this MES holds about it appears here: the gauge that took it and when "
      + "it was last calibrated, the same characteristic by the other gauge in "
      + "the hour either side, this station's process values over the ten "
      + "minutes before it, what the machine was doing, and anything else that "
      + "happened in that window. Nothing is modelled or guessed — every line "
      + "is a record, and each block says how much of its window anybody was "
      + "watching.";
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
  /* This reading's point on the other half: the gap from the reading before
     it. Absent for the first reading on the chart, which has nothing before
     it to be a gap from - and that is a different fact from a gap of nought,
     so the row is left out rather than printed as zero. */
  const mr = chart.moving_range;
  section.append(facts([
    ["Reading", chart.reading ? `${chart.reading} of ${chart.readings}` : null],
    ["Centre", chart.control ? chart.control.centre : null, true],
    ["Control limits", chart.control
      ? `${chart.control.lower} to ${chart.control.upper}` : null, true],
    ["Sigma (within)", chart.control ? chart.control.sigma : null, true],
    ["Moving range", mr
      ? `${mr.range}${mr.upper === null ? "" : ` of ${mr.upper} allowed`}`
        + (mr.flagged ? " — beyond the limit" : "")
      : null, true],
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

  /* A sample is one act of measurement by one person with one gauge, so its
     readings should not name another instrument. When they do, the server
     says so rather than averaging over it quietly. */
  if (g.readings_note) section.append(el("p", "muted small", g.readings_note));

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

function machineBlock(d, noun = "reading") {
  const section = pointBlock("What the machine was doing");
  const m = d.machine;
  if (!m) {
    section.append(el("p", "muted small",
      `No station is recorded against this ${noun}, so there is no machine to ask about. `
      + "A person with a gauge records none, and working one out from the order's route "
      + "would name a machine nobody stood at."));
    return section;
  }
  const state = m.state;
  const previous = m.previous;
  section.append(facts([
    [`At the ${noun}`, state
      ? `${state.state}${state.reason ? ` — ${state.reason}` : ""}` : null],
    ["In that state for", state ? kit.duration(m.seconds_in_state) : null],
    ["Just before", previous
      ? `${previous.state}${previous.reason ? ` — ${previous.reason}` : ""}` : null],
    ["Which ended", m.seconds_since_previous_ended === null ? null
      : `${kit.duration(m.seconds_since_previous_ended)} before the ${noun}`],
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

function timelineChart(host, d, station, markers) {
  if (!d.timeline) return;
  const section = pointBlock("The window, as the machine lived it");
  host.append(section);
  smallChart(section, `${station ? station.code : "station"} states`,
             "states", d.timeline, { markers, rowHeight: 20 });
}

function tagsBlock(d, station, noun = "reading") {
  const section = pointBlock("This station's process values");
  const tags = d.tags || {};
  const trends = tags.trends || [];
  if (!trends.length) {
    section.append(el("p", "muted small",
      station
        ? "This station published no process values in this window. That is not the "
          + "same as the values having been flat."
        : `No station is recorded against this ${noun}, so there are no tags to draw.`));
    return section;
  }
  /* What the window is, which is not the same question on the two panels. A
     reading is an instant and the window is ten minutes before it; a sample of
     n pieces is a stretch, and the window runs from the minutes before the
     FIRST of them to the last plus the tail. Every reading is marked. */
  const spans = d.window.spanned_seconds !== undefined
    ? `the ${kit.duration(d.window.spanned_seconds)} this sample spans, the `
      + `${d.window.before_minutes} minutes before the first reading and the `
      + `${d.window.after_minutes} after the last. All ${tags.marks || d.readings.total} `
      + "readings are marked on each."
    : `the ${d.window.before_minutes} minutes before the reading and the `
      + `${d.window.after_minutes} after. The reading is marked on each.`;
  section.append(el("p", "muted small",
    `${tags.shown} of ${tags.total} signal${tags.total === 1 ? "" : "s"} this station `
    + `published in this window, over ${spans}`));
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

/* ---------- the two blocks a sample has and a reading does not ---------- */

/* The n pieces behind one point. This is the block that makes a sampled chart
   worth clicking: a mean above its limit because every bottle was high and a
   mean above its limit because one bottle was very high are two different
   mornings, and the chart above cannot tell them apart. Each reading's
   distance from the sample's own mean, and the furthest named.

   The server names the furthest, and the sentence under the table is its own
   too — because *furthest from the mean* is arithmetic and not blame: one of
   five is always the furthest out, and on a settled process that means
   nothing whatever. */
function readingsBlock(d) {
  const r = d.readings || {};
  const s = d.sample || {};
  const section = pointBlock(`The ${r.total} readings behind this point`);
  const value = el("p", "point-value");
  value.append(String(s.mean === null || s.mean === undefined ? "—" : s.mean));
  if (d.unit) value.append(el("span", "unit", d.unit));
  section.append(value);
  const spec = s.lower_spec === null && s.upper_spec === null
    ? "no limits on the specification"
    : `${s.lower_spec === null ? "—" : s.lower_spec} to `
      + `${s.upper_spec === null ? "—" : s.upper_spec}${d.unit ? " " + d.unit : ""}`;
  section.append(facts([
    ["Mean of", `${r.total} reading${r.total === 1 ? "" : "s"}`],
    ["Range", r.range, true],
    ["Lowest / highest", r.min === null || r.min === undefined ? null
      : `${r.min} / ${r.max}`, true],
    ["Specification", spec, true],
    ["Taken at", fmt.stamp(s.ts)],
    ["By", s.checked_by],
    ["Order", s.work_order, true],
    ["Shift", s.shift ? `${s.shift}${s.shift_day ? ` (${fmt.day(s.shift_day)})` : ""}` : null],
    ["Station", s.equipment ? `${s.equipment.code} — ${s.equipment.name}` : null, true],
  ]));
  if (s.note) section.append(el("p", "muted small", s.note));
  section.append(pointTable(
    ["#", "Value", "From the mean", "Taken at", "Gauge", "By"],
    (r.readings || []).map((row) => {
      const value = el("span", row.furthest ? "mono strong" : "mono");
      value.textContent = String(row.value);
      const gap = el("span", row.furthest ? "strong" : null);
      gap.textContent = row.difference_to_mean === null ? "—"
        : `${row.difference_to_mean > 0 ? "+" : ""}${row.difference_to_mean}`
          + (row.furthest ? " — furthest out" : "");
      return [
        row.position,
        /* Out of specification is said on the row, because a sample whose mean
           is inside the limits can still hold a bottle that is not, and that
           is a finding the average hides by construction. */
        row.result === "pass" ? value : (() => {
          const wrap = el("span");
          wrap.append(value, el("span", "muted small",
            row.above_spec ? " above spec" : row.below_spec ? " below spec" : " fail"));
          return wrap;
        })(),
        gap,
        fmt.clock(row.ts),
        row.gauge || el("span", "muted small", "not recorded"),
        row.checked_by,
      ];
    }),
    "No readings are recorded under this sample."));
  if (r.note) section.append(el("p", "muted small", r.note));
  return closeWith(section, r);
}

/* Where this sample sat on each half of the chart, and what fired on it.
   Both halves, because the reader came from one of them and the two answer
   different questions: where the average of n pieces sat against limits
   computed from the mean range, and how far apart those n were. */
function sampleRuleBlock(d) {
  const section = pointBlock("What the rules said");
  const chart = d.chart || {};
  const signals = chart.signals || [];
  if (!chart.on_chart) {
    section.append(el("p", "muted small", chart.note || "This sample is not on the chart."));
  } else if (signals.length) {
    for (const signal of signals) {
      section.append(el("p", null, `Rule ${signal.rule}: ${signal.what}.`));
    }
  } else {
    section.append(el("p", "muted small",
      "No rule fired on this sample. It is on the chart and the rules were run "
      + "when it was recorded."));
  }
  const lower = chart.range_chart || {};
  section.append(facts([
    ["Sample", chart.sample ? `${chart.sample} of ${chart.samples}` : null],
    ["Mean", chart.mean, true],
    ["Centre (X̿)", chart.control ? chart.control.centre : null, true],
    ["Control limits", chart.control
      ? `${chart.control.lower} to ${chart.control.upper}` : null, true],
    ["Sigma (within)", chart.control ? chart.control.sigma_within : null, true],
    /* The sigma the limits above are drawn from, which is smaller by root n
       and is NOT what capability is computed from. Both, labelled, because a
       sampled chart that showed one of them under the other's name is how a
       process comes to be credited with a Cpk it never had. */
    ["Sigma of a mean", chart.control ? chart.control.sigma : null, true],
    ["Range", lower.upper === null || lower.upper === undefined
      ? chart.range
      : `${chart.range} of ${lower.upper} allowed`
        + (lower.flagged ? " — beyond the limit" : ""), true],
    ["Mean range (R̄)", lower.centre, true],
  ]));
  if (lower.verdict) section.append(el("p", "muted small", lower.verdict));
  const recorded = (d.recorded && d.recorded.signals) || [];
  section.append(el("p", "muted small",
    `${recorded.length} rule firing${recorded.length === 1 ? "" : "s"} recorded on this `
    + "sample when it arrived" + (recorded.length ? ":" : ".")));
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

/* ---------- the rest of the line, and folding the panel to one screen ---------- */

/* The most rows of another station's window this block prints. The answer
   bounds what it reads; this bounds what is drawn, and the count under the
   table says what it left, the way the station's own stops table already
   does. */
const LINE_ROWS_SHOWN = 4;

/* Which blocks open folded. The panel was 4,100 px on a point and 1,600 on a
   sample, and the three process-value trends were most of it: a reader
   scrolled past the gauge and the trends to reach the stops every time. So
   the readings, the rules and the line's own events are open and the rest is
   one click, with a summary line that says what is inside and how much of it
   - nothing is hidden without saying so (style rule 4).

   The state is per block and per reader, in `localStorage`: somebody who
   opens the trends on every reading should not have to open them again on the
   next one. Wrapped, because a browser with storage switched off is a browser
   this panel still has to work in. */
function foldedByDefault(key) {
  try {
    const stored = localStorage.getItem(`fsmes-spc-fold-${key}`);
    if (stored === "open") return false;
    if (stored === "closed") return true;
  } catch (e) { /* storage off: the default below stands */ }
  return null;
}

function rememberFold(key, open) {
  try {
    localStorage.setItem(`fsmes-spc-fold-${key}`, open ? "open" : "closed");
  } catch (e) { /* nothing to remember it in, which breaks nothing */ }
}

/* One built block, behind a summary that states what is inside it.

   The block's own heading becomes the summary's label and the sentence beside
   it is the count, so a folded block reads as "3 process values, 2 of 3
   drawn" rather than as a closed box. `data-fold` is the anchor a test and a
   guide point at. */
function foldable(section, key, summary, { open }) {
  const box = el("details", "point-block point-fold");
  box.dataset.fold = key;
  const stored = foldedByDefault(key);
  box.open = stored === null ? open : !stored;
  const head = el("summary");
  const title = section.querySelector("h3");
  head.append(el("span", "point-fold-title", title ? title.textContent : key));
  head.append(el("span", "point-fold-says", summary));
  if (title) title.remove();
  const inner = [...section.childNodes];
  box.append(head, ...inner);
  box.addEventListener("toggle", () => rememberFold(key, box.open));
  return box;
}

/* What the rest of the line was doing in the same window.

   The block #143 and #147 both asked for: a fill-height sample at the
   inspection bench twenty minutes after the filler changed over is a sample
   about the changeover, and the station's own block cannot say so however
   honest it is.

   Every row is a record on another machine, with that machine's name on it,
   where it sat relative to this reading, and a link to its page. There is no
   upstream and no downstream here and no cause named: the answer orders the
   stations by the routing's step number where the routing names them, says
   which routing, and leaves the reading to the reader. */
function lineBlock(d, noun = "reading") {
  const section = pointBlock("The rest of the line");
  const line = d.line || {};
  const stations = line.stations || [];
  if (!stations.length) {
    section.append(el("p", "muted small", line.note
      || `This MES holds nothing about the rest of the line around this ${noun}.`));
    return section;
  }
  section.append(el("p", "muted small",
    `${line.parent.code} — ${line.parent.name}. Every row is a record on another `
    + `station in this ${noun}'s own window, in the routing's order. Nothing here `
    + "says one caused the other."));

  for (const station of stations) {
    const box = el("div", "line-station");
    const head = el("h4");
    const link = el("a", "obj", station.code);
    link.href = `/dashboard/machine/${encodeURIComponent(station.code)}`;
    head.append(link, ` ${station.name}`);
    if (station.routing_seq !== null && station.routing_seq !== undefined) {
      head.append(el("span", "muted small", ` · step ${station.routing_seq}`));
    } else {
      /* Said rather than left blank: a station this material's routing does
         not name has no step number, which is a fact about the routing. */
      head.append(el("span", "muted small", " · not in this routing"));
    }
    box.append(head);

    /* Changeovers first, then the other stops, longest first inside each. The
       pack's own words are that "a machine in setup on this line is the whole
       line changing over", and it was the changeover neither panel could
       name - so it is not the fifth row of a table of idle blips. */
    const rows = [...(station.changeovers || []), ...(station.stops || [])];
    const shown = rows.slice(0, LINE_ROWS_SHOWN);
    box.append(pointTable(["What", "Reason", "For", "When"],
      shown.map((row) => [
        row.state === "setup" ? el("strong", null, "changeover") : row.state,
        row.reason || el("span", "muted small", "unlabelled"),
        kit.duration(row.seconds),
        placed(row, noun),
      ]),
      "Nothing recorded on this station in this window."));
    const rest = rows.length - shown.length;
    box.append(el("p", "muted small",
      `${station.not_running_total} stretch`
      + `${station.not_running_total === 1 ? "" : "es"} not running, `
      + `${kit.duration(station.seconds)} in all — ${station.labelled} named, `
      + `${station.unlabelled} unlabelled.`
      + (rest > 0 ? ` First ${shown.length} listed; ${rest} more.` : "")));
    const said = watchedLine(station);
    if (said) box.append(el("p", "point-watched", said));
    section.append(box);
  }

  /* How many of the line's stations were drawn, which recorded nothing, and
     which routing put them in this order (style rule 4). */
  section.append(el("p", "muted small", line.note || ""));
  if ((line.quiet || []).length) {
    section.append(el("p", "muted small",
      `Recorded nothing but running: ${line.quiet.map((row) => row.code).join(", ")}.`));
  }
  return closeWith(section, line);
}

/* Where one stretch on another machine sat relative to this reading, in the
   words the answer gave it. Three different sentences for three different
   facts, and the seconds are the server's: this panel does no arithmetic. */
function placed(row, noun) {
  if (row.over_the_reading) return `still in it at this ${noun}`;
  if (row.ended_seconds_before !== null && row.ended_seconds_before !== undefined) {
    return `ended ${kit.duration(row.ended_seconds_before)} before this ${noun}`;
  }
  if (row.started_seconds_after !== null && row.started_seconds_after !== undefined) {
    return `started ${kit.duration(row.started_seconds_after)} after this ${noun}`;
  }
  /* Open at the end of the window: there is no end to measure from, and
     "ended 0 min before" would be the wrong sentence (style rule 8). */
  return `${fmt.clock(row.start)}, still open`;
}

/* The one-line summaries the folded blocks carry. Each says what is inside and
   how many of it, because a closed box that states nothing is a list that
   reads complete. */
function gaugeSummary(d) {
  const g = d.gauge || {};
  const n = d.neighbours || {};
  const who = g.gauge ? g.gauge.code : "no gauge recorded";
  return `${who}, ${n.total || 0} reading${(n.total || 0) === 1 ? "" : "s"} by `
       + `${n.gauges || 0} gauge${(n.gauges || 0) === 1 ? "" : "s"} in the hour either side`;
}

function tagsSummary(d) {
  const tags = d.tags || {};
  const total = tags.total || 0;
  if (!total) return "none published in this window";
  return `${total} process value${total === 1 ? "" : "s"}, ${tags.shown} of ${total} drawn`;
}

function elseSummary(d) {
  const maint = d.maintenance || {};
  const found = d.findings || {};
  return `${maint.total || 0} maintenance order${(maint.total || 0) === 1 ? "" : "s"}, `
       + `${found.total || 0} finding${(found.total || 0) === 1 ? "" : "s"}`;
}

function lineSummary(d) {
  const line = d.line || {};
  if (!line.parent) return "no line recorded";
  const stations = line.stations || [];
  const changeovers = stations.reduce(
    (n, station) => n + (station.changeovers || []).length, 0);
  const stops = stations.reduce((n, station) => n + (station.stops || []).length, 0);
  if (!stations.length) {
    return `${line.total || 0} other station${(line.total || 0) === 1 ? "" : "s"} on `
         + `${line.parent.code}, none with a stop or a changeover in this window`;
  }
  return `${line.shown} of ${line.total} other station${line.total === 1 ? "" : "s"} on `
       + `${line.parent.code} — ${changeovers} changeover${changeovers === 1 ? "" : "s"}, `
       + `${stops} other stop${stops === 1 ? "" : "s"}`;
}

/* ---------- drawing it, and asking for it ---------- */

function drawPoint(d) {
  const body = $("#point-body");
  body.replaceChildren();
  $("#point-which").textContent =
    `— reading ${d.reading.check}, ${fmt.stamp(d.reading.ts)}`;
  body.append(readingBlock(d));
  body.append(ruleBlock(d));
  /* Folded where it already sits rather than moved to the bottom: *did the
     process move or did the gauge?* is the second question a person asks, and
     a block that answered it from somewhere else in the panel would be a
     different panel. A closed `<details>` is one line tall, so its place
     costs nothing. */
  body.append(foldable(gaugeBlock(d), "gauge", gaugeSummary(d), { open: false }));
  body.append(machineBlock(d, "reading"));
  timelineChart(body, d, d.reading.equipment,
                [{ t: d.window.at, label: "this reading" }]);
  /* And then the rest of the line, in the same window - after the station's
     own story and its Gantt, because it is the answer to "and it was not this
     station". Open when there is something in it. */
  body.append(foldable(lineBlock(d, "reading"), "line", lineSummary(d),
                       { open: (d.line && (d.line.stations || []).length) > 0 }));
  body.append(foldable(tagsBlock(d, d.reading.equipment, "reading"), "tags",
                       tagsSummary(d), { open: false }));
  body.append(foldable(elseBlock(d), "else", elseSummary(d), { open: false }));
  /* The window the whole panel is about, and how much of it anybody watched.
     Last, because it qualifies everything above it. */
  const foot = el("p", "point-watched",
    `${fmt.clock(d.window.start)}–${fmt.clock(d.window.end)}. `
    + (watchedLine(d) || ""));
  body.append(foot);
  $("#point-idle").hidden = true;
  body.hidden = false;
}

/* The same panel, about a sample. The blocks are the point panel's blocks -
   the gauge, the machine, the timeline, the trends, the window - with the two
   a sample has and a reading does not at the top: the n readings behind the
   point, and where it sat on each half of the chart. Nothing below the first
   two blocks knows which kind it is reading, which is the point: one panel,
   not a second one that would drift. */
function drawSample(d) {
  const body = $("#point-body");
  body.replaceChildren();
  $("#point-which").textContent =
    `— sample ${d.sample.sample}, ${fmt.stamp(d.sample.ts)}`;
  body.append(readingsBlock(d));
  body.append(sampleRuleBlock(d));
  body.append(foldable(gaugeBlock(d), "gauge", gaugeSummary(d), { open: false }));
  body.append(machineBlock(d, "sample"));
  /* Every reading marked on the timeline too, not just the sample's stamp: a
     changeover that ended between the second bottle and the third is the
     answer, and one marker in the middle of five would hide it. */
  timelineChart(body, d, d.sample.equipment,
                (d.readings.readings || []).map((row) => ({
                  t: row.ts, label: `reading ${row.position}` })));
  body.append(foldable(lineBlock(d, "sample"), "line", lineSummary(d),
                       { open: (d.line && (d.line.stations || []).length) > 0 }));
  body.append(foldable(tagsBlock(d, d.sample.equipment, "sample"), "tags",
                       tagsSummary(d), { open: false }));
  body.append(foldable(elseBlock(d), "else", elseSummary(d), { open: false }));
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
  /* And the other kind's attribute with it. One panel is about one thing, and
     a page carrying both would have a test - and a reader - believing the
     column beside them is about a sample when it is about a reading. */
  node.dataset.sample = "";
  openSample = null;
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

/* The same three states, about a sample. `data-sample` carries the sample's
   own id and is written LAST, after every block is on the page - so waiting on
   it is waiting on the thing asserted rather than on the box that will hold it
   (#112, 2026-09-26). */
async function openSamplePanel(sample) {
  const node = $("#point-panel");
  if (sample === undefined || sample === null) return;
  node.dataset.state = "loading";
  node.dataset.sample = "";
  node.dataset.point = "";
  openCheck = null;
  $("#point-which").textContent = "— opening…";
  const [material, characteristic] = chosen.split("|");
  try {
    const data = await api(`/quality/spc/${encodeURIComponent(material)}/${encodeURIComponent(characteristic)}/sample/${encodeURIComponent(sample)}`);
    openSample = sample;
    drawSample(data);
    node.dataset.state = "open";
    node.dataset.sample = String(sample);
    window.__fsmesPointData = data;
    if (window.__fsmesPageData) draw(window.__fsmesPageData);
  } catch (error) {
    node.dataset.state = "error";
    $("#point-which").textContent = "";
    $("#point-body").replaceChildren(el("p", "empty",
      `This sample's records could not be read: ${error.message}`));
    $("#point-body").hidden = false;
    $("#point-idle").hidden = true;
  }
}

(async function boot() {
  await FS.whoami().catch(() => {});
  openOnClick();
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
    /* Which kind of chart each one gets, in the list you pick from. The
       sampling plan decides the chart (decision 0040), and a reader who picks
       *fill height* expecting the chart they saw on *brix* is a reader who
       will read the means as readings for a moment - which is the whole of
       what this label is for. Said on every row, including the ones inspected
       one at a time: "the unlabelled ones are individuals" is a convention,
       and a convention is not a label. */
    group.append(...rows.map((s) => new Option(
      `${s.characteristic} — ${(s.sample_size || 1) > 1
        ? `${s.sample_size} at a time (X̄ and R)` : "one at a time"}`,
      `${s.material}|${s.characteristic}`)));
    return group;
  }));
  const wanted = new URL(location).searchParams.get("spec");
  chosen = specs.some((s) => `${s.material}|${s.characteristic}` === wanted) ? wanted : (specs.length ? `${specs[0].material}|${specs[0].characteristic}` : null);
  if (!chosen) return fail(new Error("No quality specification is defined yet."));
  select.value = chosen;
  select.addEventListener("change", () => {
    chosen = select.value;
    /* A new characteristic is a new chart, and nothing about the panel beside
       it is still true: sample 7 of fill height and reading 7 of brix are both
       integers, and a panel left open on one while the other is drawn is the
       page claiming a record it is not showing. */
    openCheck = null;
    openSample = null;
    const panel = $("#point-panel");
    panel.dataset.state = "idle";
    panel.dataset.point = "";
    panel.dataset.sample = "";
    $("#point-which").textContent = "";
    $("#point-body").hidden = true;
    $("#point-body").replaceChildren();
    $("#point-idle").hidden = false;
    load().then(() => live(true)).catch(fail);
  });
  const run = () => load().then(() => live(true)).catch(fail);
  await run();
  timer = setInterval(run, REFRESH_MS);
})().catch(fail);
