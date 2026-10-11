/* One machine, everything it says.

   The first object page. Its head is where the machine sits and what it is
   doing; its tabs are the questions people ask of a machine - what is it
   reporting right now, how has a signal moved, when was it down, how well
   did it run, what does it owe maintenance, what is queued on it, and what
   it counted with nothing queued at all.

   Every number on this page came over OPC UA or was booked against this
   machine; the footer names the endpoint behind each tab. */

const { $, el, api, fmt, kit } = window.FS;

const CODE = decodeURIComponent(location.pathname.split("/").pop());
const NOW_REFRESH_MS = 3000;

let head = null;
let snapshot = null;
let trendTag = new URL(location).searchParams.get("tag");
let nowTimer = null;
// The timeline envelope the bar is drawn from, the block the panel beside it
// is open on, and this plant's own list of downtime reasons - read once.
let timelineData = null;
let openStop = null;
let stopVocabulary = null;

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

function hours() { return FS.window.hours(); }

/* ---------- head ---------- */

function renderHead(h) {
  head = h;
  document.title = `${h.code} — FactorySemantics MES`;
  $("#m-code").textContent = h.code;
  $("#m-name").textContent = h.name;
  const pill = $("#m-state");
  const lost = FS.connection.lost(h);
  pill.className = `pill ${FS.connection.stateClass(h)}`;
  pill.textContent = FS.connection.stateText(h);
  // When the link is gone the "since" line is about the link, not about a
  // state the MES stopped being able to see.
  $("#m-since").textContent = lost
    ? `no connection since ${lost.since ? fmt.clock(lost.since) : "an unknown time"}`
      + (lost.reason ? ` — ${lost.reason}` : "")
    : (h.since ? `since ${fmt.clock(h.since)}` + (h.reason ? ` — ${h.reason}` : "") : "");
  $("#m-order").textContent = h.current_order || "—";
  $("#m-op").textContent = h.current_operation ? `${h.current_operation} (op ${h.current_seq})` : "—";
  $("#m-cc").textContent = h.cost_center || "—";
  $("#m-cycle").textContent = h.ideal_cycle_seconds ? `${h.ideal_cycle_seconds} s` : "—";

  // Every level of the path is somewhere, and FS.crumbs is the one place
  // that knows which screen shows which level.
  FS.crumbs($("#crumbs"), h.path, h.code);
  $("#operate-link").href = `/dashboard/station?m=${encodeURIComponent(h.code)}`;
}

/* ---------- now: tags and alarms ---------- */

function valueText(t) {
  if (t.value === null || t.value === undefined) return "—";
  if (typeof t.value === "number") {
    if (t.kind === "counter" || t.kind === "state" || t.kind === "alarm") return Math.round(t.value).toLocaleString();
    return fmt.qty(t.value);
  }
  return String(t.value);
}

function renderTags(snap) {
  snapshot = snap;
  const byName = Object.fromEntries(snap.tags.map((t) => [t.tag, t]));
  const rows = snap.tags.filter((t) => t.kind !== "sp");   // setpoints ride beside what they drive
  const body = $("#tags-table tbody");
  body.replaceChildren();
  for (const t of rows) {
    const tr = el("tr", t.stale ? "stale" : null);
    tr.dataset.tag = t.tag;
    const name = el("td", "code");
    name.append(t.tag);
    if (t.primary) name.append(" ", el("span", "pill", "primary"));
    if (t.note) name.title = t.note;
    tr.append(name);
    tr.append(el("td", "num mono", valueText(t)));
    tr.append(el("td", "muted", t.unit || ""));

    const sp = Object.values(byName).find((s) => s.kind === "sp" && s.drives === t.tag) || (t.follows ? byName[t.follows] : null);
    const spCell = el("td", "muted");
    if (sp) {
      spCell.className = "mono";
      spCell.textContent = `${valueText(sp)}${sp.unit ? " " + sp.unit : ""}`;
      spCell.title = `${sp.tag}: ${sp.min ?? "?"} – ${sp.max ?? "?"}${sp.writable ? " · writable" : ""}`;
      if (sp.writable) spCell.append(" ", el("span", "pill", "writable"));
    } else {
      spCell.textContent = "";
    }
    tr.append(spCell);
    tr.append(el("td", "muted", t.kind || "—"));
    const ageCell = el("td", "muted small", kit.age(t.age_seconds));
    if (t.stale) ageCell.append(" ", el("span", "pill down", "stale"));
    tr.append(ageCell);
    tr.addEventListener("click", () => {
      if (typeof t.value !== "number") return;
      trendTag = t.tag;
      location.hash = "trend";
    });
    body.append(tr);
  }
  $("#tag-count").textContent = `— ${rows.length} of ${snap.tags.length} (from ${snap.source})`;

  const alarmTag = snap.tags.find((t) => t.kind === "alarm" || t.tag === "AlarmWord");
  const list = $("#alarms");
  list.replaceChildren();
  const active = alarmTag ? alarmTag.active || [] : [];
  if (!alarmTag) {
    list.append(el("li", "muted", "This machine publishes no alarm word."));
    $("#alarm-count").textContent = "";
  } else if (!active.length) {
    list.append(el("li", "muted", `No alarm bits set (word ${valueText(alarmTag)}, ${kit.age(alarmTag.age_seconds)} ago).`));
    $("#alarm-count").textContent = "— none";
  } else {
    for (const name of active) list.append(el("li", "alarm", name));
    $("#alarm-count").textContent = `— ${active.length} active`;
  }

  // The trend picker offers every numeric tag, primary first.
  const picker = $("#trend-tag");
  const numeric = snap.tags.filter((t) => typeof t.value === "number" || t.kind === "pv" || t.kind === "sp");
  if (!trendTag || !numeric.some((t) => t.tag === trendTag)) {
    trendTag = (numeric.find((t) => t.primary) || numeric[0] || {}).tag || null;
  }
  picker.replaceChildren(...numeric.map((t) => new Option(t.tag + (t.unit ? ` (${t.unit})` : ""), t.tag, false, t.tag === trendTag)));
}

function renderAlarmHistory(back) {
  // Every change of the alarm word in the window, newest first: the question
  // a failed check asks an hour later is "what did this machine raise?".
  const list = $("#alarm-history");
  list.replaceChildren();
  const all = (back.history || []).length;
  const changes = (back.history || []).slice().reverse().slice(0, 30);
  if (!changes.length) {
    list.append(el("li", "muted", `No alarm changes in the last ${back.hours} h.`));
    $("#alarm-history-count").textContent = "";
    return;
  }
  for (const h of changes) {
    const when = new Date(h.ts + "Z").toLocaleTimeString();
    const what = h.active.length ? h.active.join(", ") : "cleared";
    list.append(el("li", h.active.length ? "alarm" : "muted", `${when} — ${what} (word ${h.word})`));
  }
  // The newest thirty are drawn; the count says how many there were.
  $("#alarm-history-count").textContent = all > changes.length
    ? `— newest ${changes.length} of ${all.toLocaleString()} changes`
    : `— ${changes.length} change${changes.length === 1 ? "" : "s"}`;
}

async function refreshNow() {
  try {
    const [h, snap, back] = await Promise.all([
      api(`/equipment/${CODE}`), api(`/equipment/${CODE}/tags`),
      api(`/equipment/${CODE}/alarms?hours=${hours()}`),
    ]);
    renderHead(h);
    renderTags(snap);
    renderAlarmHistory(back);
    window.__fsmesPageData = { head: h, tags: snap, alarms: back };
    live(true);
  } catch (err) {
    fail(err);
  }
}

/* ---------- trend ---------- */

async function loadTrend() {
  if (!trendTag) { kit.empty($("#trend"), "No numeric tag to trend."); return; }
  $("#trend-tag").value = trendTag;
  const meta = snapshot ? snapshot.tags.find((t) => t.tag === trendTag) : null;
  const data = await api(`/analysis/tag/${encodeURIComponent(CODE)}?tag=${encodeURIComponent(trendTag)}&hours=${hours()}`);
  kit.trend($("#trend"), data, {
    label: `${trendTag}${meta && meta.unit ? ` (${meta.unit})` : ""}`,
    limits: meta ? { min: meta.min, max: meta.max } : {},
  });
}

/* ---------- timeline / oee ---------- */

/* The window this screen is drawn over, as a query. One control for the whole
   page: where the trend tab's start-and-end window exists it is the window,
   and this reads it rather than growing a second chip that could disagree
   with the first. Where it does not, the hours chip is the window, as before. */
function windowQuery() {
  return window.FS.span ? FS.span.query() : `hours=${hours()}`;
}

async function loadTimeline() {
  const data = await api(`/analysis/timeline?equipment=${encodeURIComponent(CODE)}&${windowQuery()}&limit=1`);
  timelineData = data;
  drawTimeline();
  /* The window the bar is drawn over, beside its heading - the realised one,
     because a plant that has only been watched two hours cannot be shown
     eight however many were asked for. */
  const w = data.window || {};
  $("#tl-window").textContent = w.start
    ? `— ${fmt.clock(w.start)} to ${fmt.clock(w.end)}`
      + (w.clamped ? `, ${fmt.qty(w.hours)} h of the ${fmt.qty(w.requested_hours)} asked for `
                     + "— this MES was not watching for the rest" : "")
    : "";
}

/* The bar, from the envelope already in hand. Separate from the fetch because
   opening a block redraws it to ring the block that is open, and re-reading
   the whole timeline to move an outline would be a second round trip and a
   second chance for the two to disagree. */
function drawTimeline() {
  if (!timelineData) return;
  kit.timeline($("#tl"), timelineData, {
    rowHeight: 34,
    /* Opt-in, here and nowhere else: four other screens draw this same shape
       and none of them has a panel to open. */
    openable: true,
    selected: openStop,
  });
}

/* ---------- one block of the bar, and the records behind it ----------

   Scott clicks a block of the state timeline and this says what it was, why,
   what job it belonged to, whether it was planned work, what the machine was
   doing either side of it and what quality saw after it. Every line is a
   record this MES already holds: nothing here is modelled, and the two things
   that are worked out rather than recorded - which order was on, and who named
   the stop - say so on the screen in those words.

   The same shape as the panel beside the control chart (spc.js), for the same
   reason: one read, drawn beside the thing that was clicked, with `data-block`
   written last as the contract a test waits on. */

/* Wired once, on the host rather than on the blocks. The bar is replaced
   whenever the window changes or a block is opened - the outline is drawn by
   the redraw - and a listener on a <rect> would go with it. */
function openOnClick() {
  const host = $("#tl");
  if (!host) return;
  host.addEventListener("fs-states-open", (event) => {
    const detail = event.detail || {};
    // A block is addressed by its window: it is clipped to what was drawn and
    // may be several records folded together, so there is no one id to send.
    if (!detail.start || !detail.end) return;
    openBlock(detail).catch(fail);
  });
}

async function openBlock(detail) {
  const node = $("#block-panel");
  if (!node) return;
  node.dataset.state = "loading";
  /* Cleared while the fetch is in flight: the attribute means "this block is
     drawn", and leaving the last one on it would have a test - and a reader on
     a slow connection - believing the panel beside them is about the block
     they just clicked. */
  node.dataset.block = "";
  $("#block-which").textContent = "— opening…";
  try {
    const data = await api(`/analysis/timeline/block/${encodeURIComponent(CODE)}?start=${encodeURIComponent(detail.start)}&end=${encodeURIComponent(detail.end)}`);
    openStop = { code: detail.code || CODE, start: detail.start, end: detail.end };
    drawBlock(data);
    node.dataset.state = "open";
    /* Written last, after everything above is on the page, and it carries the
       window the read was made with. */
    node.dataset.block = `${detail.start}/${detail.end}`;
    window.__fsmesBlockData = data;
    // The bar redraws so the block the reader opened is outlined. Nothing else
    // on the page moves: the panel's column was always there.
    drawTimeline();
  } catch (error) {
    openStop = null;
    node.dataset.state = "error";
    $("#block-which").textContent = "";
    $("#block-body").replaceChildren(el("p", "empty",
      `This block's records could not be read: ${error.message}`));
    $("#block-body").hidden = false;
    $("#block-idle").hidden = true;
    drawTimeline();
  }
}

function drawBlock(d) {
  const body = $("#block-body");
  body.replaceChildren();
  $("#block-which").textContent =
    `— ${fmt.clock(d.window.start)} to ${fmt.clock(d.window.end)}`;
  body.append(whatBlock(d));
  body.append(whyBlock(d));
  body.append(ordersBlock(d));
  body.append(maintenanceBlock(d));
  body.append(eitherSideBlock(d));
  body.append(foldable(tagsBlock(d), "tags", tagsSummary(d),
                       { open: (d.tags.trends || []).length > 0 }));
  body.append(foldable(qualityBlock(d), "quality", qualitySummary(d),
                       { open: (d.quality.readings || []).length > 0 }));
  body.append(foldable(watchedBlock(d), "watched", watchedSummary(d), { open: false }));
  $("#block-idle").hidden = true;
  body.hidden = false;
}

/* ---------- the panel's own small parts ----------

   The same parts the control chart's panel has. Written again here rather
   than shared because they are a page script's renderers and this page does
   not load that one; when a third panel of this shape appears they belong in
   the kit, which is where the charts they draw already live. */

function stopBlock(title) {
  const section = el("section", "stop-block");
  section.append(el("h3", null, title));
  return section;
}

function watchedLine(block) {
  if (!block) return null;
  if (block.coverage === "absent") {
    return block.coverage_note || "No coverage figure: this is a list of records.";
  }
  if (!("coverage" in block)) return null;
  if (block.coverage === null || block.coverage === undefined) {
    return block.coverage_note || "How much of this window was watched is unknown.";
  }
  return `Watched ${fmt.pct(block.coverage)} of it`
       + (block.coverage_note ? ` — ${block.coverage_note}` : "");
}

function closeWith(section, block) {
  const said = watchedLine(block);
  if (said) section.append(el("p", "stop-watched", said));
  return section;
}

/* Label and value in two columns. A value nobody recorded prints "—" and its
   label stays: a row that vanished would read as a question nobody asked. */
function facts(rows) {
  const list = el("dl", "stop-facts");
  for (const [label, value, mono] of rows) {
    list.append(el("dt", null, label));
    const dd = el("dd", mono ? "mono" : null);
    if (value instanceof Node) dd.append(value);
    else dd.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
    list.append(dd);
  }
  return list;
}

function stopTable(headings, rows, empty) {
  if (!rows.length) return el("p", "muted small", empty);
  const wrap = el("div", "table-wrap");
  const table = el("table");
  const head = el("thead");
  const hr = el("tr");
  for (const h of headings) hr.append(el("th", null, h));
  head.append(hr);
  table.append(head);
  const tbody = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    for (const cell of row) {
      const td = el("td");
      if (cell instanceof Node) td.append(cell);
      else td.textContent = cell === null || cell === undefined ? "—" : String(cell);
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(tbody);
  wrap.append(table);
  return wrap;
}

/* A row that belongs to another screen carries the link to it. The href is
   built here, on the screen, because the services lay out records and compute
   no URLs (decision 0023). */
function pageLink(text, href, mono = true) {
  const a = el("a", mono ? "mono" : null, text);
  a.href = href;
  return a;
}

function foldedByDefault(key) {
  try {
    const stored = localStorage.getItem(`fsmes-stop-fold-${key}`);
    if (stored === "open") return false;
    if (stored === "closed") return true;
  } catch (e) { /* storage off: the default below stands */ }
  return null;
}

function foldable(section, key, summary, { open }) {
  const box = el("details", "stop-block stop-fold");
  box.dataset.fold = key;
  const stored = foldedByDefault(key);
  box.open = stored === null ? open : !stored;
  const head = el("summary");
  const title = section.querySelector("h3");
  head.append(el("span", "stop-fold-title", title ? title.textContent : key));
  head.append(el("span", "stop-fold-says", summary));
  if (title) title.remove();
  box.append(head, ...[...section.childNodes]);
  box.addEventListener("toggle", () => {
    try {
      localStorage.setItem(`fsmes-stop-fold-${key}`, box.open ? "open" : "closed");
    } catch (e) { /* nothing to remember it in, which breaks nothing */ }
  });
  return box;
}

/* One small chart inside the panel. The kit refuses an envelope it cannot
   draw honestly, by name; that refusal is printed rather than swallowed. */
function smallChart(host, title, kind, envelope, options) {
  const box = el("div", "stop-chart");
  if (title) box.append(el("h4", null, title));
  const inner = el("div", "chart-host");
  box.append(inner);
  host.append(box);
  try {
    kit.draw(inner, kind, envelope, { width: inner.clientWidth || 380, ...options });
  } catch (err) {
    inner.append(el("p", "empty", `This could not be drawn as a ${kind}: ${err.message}`));
  }
  return box;
}

/* ---------- the blocks ---------- */

const STATE_WORDS = {
  running: "Running", idle: "Idle", down: "Down", setup: "Setup",
  blocked: "Blocked", starved: "Starved", disconnected: "No connection",
};

/* What it was and how long: the sentence the handoff asks for, in that order
   - "Setup, 4 minutes, 4:40 to 4:44 a.m." */
function whatBlock(d) {
  const section = stopBlock("What this block was");
  const b = d.block;
  const words = STATE_WORDS[b.state] || b.state || "Not recorded";
  section.append(el("p", "stop-headline", `${words}, ${kit.duration(b.seconds)}`));
  section.append(el("p", "stop-says",
    `${fmt.clock(b.started_at)} to ${fmt.clock(b.ended_at)}`
    + (b.open ? " — and still open: this stretch has not ended" : "")));
  const states = Object.entries(b.seconds_by_state || {});
  section.append(facts([
    ["Shift", b.shift],
    /* What the reader clicked may be several records: `_merge_short` folds
       intervals too brief to draw into the block before them, so the panel
       says how many it stands for rather than letting one word cover nine. */
    ["Records behind it", b.records_total === 1
      ? "one — one state record, as drawn"
      : `${b.records_total} state records folded into the block that was drawn`],
    ["What is in it", states.length > 1
      ? states.map(([name, seconds]) => `${name} ${kit.duration(seconds)}`).join(", ")
      : null],
    ["Began before the window", b.runs_back_before_window
      ? "yes — this is the tail of something longer than what was drawn" : "no"],
    ["Runs past the window", b.runs_on_after_window ? "yes" : "no"],
  ]));
  if (b.note) section.append(el("p", "muted small", b.note));
  return section;
}

/* Why — the reason the floor gave it, who gave it and when, or plainly that
   nobody has, with the control to say it where the capability allows. */
function whyBlock(d) {
  const section = stopBlock("Why");
  const why = d.why;
  section.append(el("p", "stop-says", why.says));
  section.append(facts([
    ["Reason", why.reason],
    ["Code", why.reason_code, true],
    ["This plant's word for it", why.reason_label
      ? pageLink(why.reason_label, "/dashboard/reasons", false) : null],
    ["Told by", why.reason_source
      ? `${why.reason_source} — a system, not a person` : null],
    ["Said of", why.records_total > 1
      ? `${why.records_with_a_reason} of this block's ${why.records_total} records`
      : null],
  ]));
  const by = why.labelled_by || [];
  section.append(el("h4", null, by.length ? "Who said so" : "Nobody is recorded as saying so"));
  section.append(stopTable(["Who", "When", "What they did", "Matched by"],
    by.map((row) => [
      row.on_behalf_of ? `${row.actor} for ${row.on_behalf_of}` : row.actor,
      fmt.stamp(row.at),
      row.action === "equipment.stop_labelled"
        ? `named it ${row.reason || row.reason_code || "—"}`
          + (row.intervals > 1 ? ` — over a window holding ${row.intervals} stops` : "")
        : `gave the reason as the state changed`,
      row.matched_by,
    ]),
    "No audit row matches this stretch, so who named it is not recorded."));
  section.append(el("p", "muted small", `Derived: ${why.derived}.`));
  section.append(labelling(d));
  return section;
}

/* The door to the labelling the product already has - the same write the
   station screen uses, over the block's own window. Where the capability does
   not allow it, the refusal in its place names who may. */
function labelling(d) {
  const box = el("div", "stop-labelling");
  const can = d.why.labelling;
  if (!can.would_name) {
    if (can.note) box.append(el("p", "muted small", can.note));
    return box;
  }
  if (can.may === false) {
    box.append(el("p", "muted small",
      `Naming this stop needs ${can.capability}`
      + (can.what ? ` — ${can.what}` : "") + `. ${can.who_may}.`));
    return box;
  }
  if (!FS.can("equipment.state")) return box;
  const form = el("form", "stop-label-form");
  form.id = "stop-label";
  const select = el("select");
  select.id = "stop-reason-code";
  select.setAttribute("aria-label", "The plant's word for this stop");
  const placeholder = new Option("Loading this plant's reasons…", "");
  placeholder.disabled = true;
  placeholder.selected = true;
  select.appendChild(placeholder);
  const button = el("button", "ghost small", "Name this stop");
  button.type = "submit";
  button.disabled = true;
  form.append(select, button);
  box.append(form);
  box.append(el("p", "muted small",
    can.would_name === 1
      ? "Naming it names this one record, and never overwrites a reason somebody "
        + "already gave."
      : `Naming it names all ${can.would_name} unlabelled records in this block, and `
        + "never overwrites a reason somebody already gave."));
  fillReasons(select, button).catch(() => {
    placeholder.textContent = "This plant's reasons could not be read";
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!select.value) return;
    button.disabled = true;
    try {
      const out = await api(`/equipment/${encodeURIComponent(CODE)}/stops/label`, {
        method: "POST",
        body: { reason_code: select.value, start: d.window.start, end: d.window.end },
      });
      FS.toast(out.labelled
        ? `${out.labelled} stop${out.labelled === 1 ? "" : "s"} named ${out.reason || out.reason_code}`
          + (out.still_unlabelled_in_the_window
             ? ` — ${out.still_unlabelled_in_the_window} still unnamed in that window` : "")
        : out.note || "Nothing unlabelled was observed in that window");
      await loadTimeline();
      await openBlock({ code: CODE, start: d.window.start, end: d.window.end });
    } catch (err) {
      button.disabled = false;
      FS.toast(err.message, "bad");
    }
  });
  return box;
}

/* This plant's own list, from the server. The screen renders what it is
   given: a hardcoded list here would drift out of step with the vocabulary
   the plant approved. */
async function fillReasons(select, button) {
  if (!stopVocabulary) stopVocabulary = await api("/equipment/downtime-reasons");
  select.replaceChildren();
  if (!stopVocabulary.total) {
    select.appendChild(new Option("This plant has approved no reasons yet", ""));
    return;
  }
  const placeholder = new Option(`Choose one of ${stopVocabulary.total}`, "");
  placeholder.disabled = true;
  placeholder.selected = true;
  select.appendChild(placeholder);
  for (const [code, name] of Object.entries(stopVocabulary.names)) {
    select.appendChild(new Option(name, code));
  }
  select.addEventListener("change", () => { button.disabled = !select.value; });
}

/* What it belonged to: the order before and the order after. Derived, and the
   block says so - nothing in this MES joins a stop to an order. */
function ordersBlock(d) {
  const section = stopBlock("The job that was on");
  const o = d.orders;
  section.append(facts([
    ["Before it", side(o.before)],
    ["After it", side(o.after)],
    ["Open across it", o.during_total
      ? `${o.during_total} operation record${o.during_total === 1 ? "" : "s"}`
      : null],
  ]));
  if (o.before && o.after && o.before.order !== o.after.order) {
    section.append(el("p", "stop-says",
      `${o.before.order} before, ${o.after.order} after — this block is the hand-over `
      + "between two orders"));
  }
  for (const which of [o.before, o.after]) {
    if (which && which.operation_names_another_order) {
      section.append(el("p", "muted small",
        `The two kinds of record disagree here: this machine booked against `
        + `${which.order} and an operation record names `
        + `${which.operation_names_another_order}. Both are reported; neither is `
        + "picked for you."));
    }
  }
  section.append(stopTable(["Order", "Step", "Operation", "Status", "Started"],
    (o.during || []).map((row) => [
      pageLink(row.order, `/dashboard/orders?q=${encodeURIComponent(row.order)}`),
      row.seq, row.name, row.status, fmt.stamp(row.started_at),
    ]),
    "No operation record was open on this machine across this block."));
  if (o.booked_during_total) {
    section.append(el("p", "muted small",
      `${o.booked_during_total} quantity booking${o.booked_during_total === 1 ? "" : "s"} `
      + "landed inside the block itself."));
  }
  section.append(el("p", "muted small", `Derived: ${o.derived}.`));
  return closeWith(section, o);
}

function side(which) {
  if (!which) return null;
  const box = el("span");
  box.append(pageLink(which.order, `/dashboard/orders?q=${encodeURIComponent(which.order)}`));
  box.append(el("span", "muted small", ` ${which.material || ""} — ${which.from}`));
  return box;
}

/* Planned work, or a breakdown. The question this panel exists to answer for
   a down block: #158 put both the order's state and its reason in the data,
   and a planned stop has to read as planned. */
function maintenanceBlock(d) {
  const section = stopBlock("Planned work, or a breakdown");
  const m = d.maintenance;
  section.append(el("p", "stop-says", m.says));
  if (m.note) section.append(el("p", "muted small", m.note));
  section.append(stopTable(["Order", "Kind", "State", "Needs a stop", "Given to", "Raised"],
    (m.orders || []).map((row) => [
      pageLink(row.code, "/dashboard/maintenance"),
      row.kind, row.status,
      row.needs_stop ? `yes${row.window ? ` — ${row.window.replace(/_/g, " ")}` : ""}` : "no",
      row.assigned_to
        ? `${row.assigned_to}${row.assigned_by ? ` by ${row.assigned_by}` : ""}`
        : null,
      fmt.stamp(row.raised_at),
    ]),
    "No maintenance order on this machine covers this block."));
  if ((m.never_started || []).length) {
    section.append(el("p", "muted small",
      `Raised and never started: ${m.never_started.join(", ")}. An order nobody `
      + "started does not explain a stop — it would explain every stop."));
  }
  return closeWith(section, m);
}

/* What the machine was doing either side, in words. The bar shows the shape;
   this says it, with the seconds between. */
function eitherSideBlock(d) {
  const section = stopBlock("What the machine was doing either side");
  const e = d.either_side;
  section.append(facts([
    ["Just before", interval(e.before, "before")],
    ["Just after", interval(e.after, "after")],
  ]));
  section.append(el("p", "muted small", e.note.charAt(0).toUpperCase() + e.note.slice(1) + "."));
  return closeWith(section, e);
}

function interval(row, which) {
  if (!row) return null;
  const words = STATE_WORDS[row.state] || row.state;
  const gap = row.seconds_from_the_block;
  return `${words}${row.reason ? ` — ${row.reason}` : ""}`
    + (row.seconds === null ? ", still open" : `, ${kit.duration(row.seconds)}`)
    + (gap ? `, ending ${kit.duration(gap)} ${which === "before" ? "before" : "after"} the block`
           : "");
}

/* The machine's own signals before and after, as small charts, with a line
   where the block starts and another where it ends - the same shape and the
   same bucketing the control chart's panel draws, through the same service
   call, so the two screens cannot draw one tag two ways. */
function tagsBlock(d) {
  const section = stopBlock("This machine's own signals, either side");
  const tags = d.tags || {};
  const trends = tags.trends || [];
  if (!trends.length) {
    section.append(el("p", "muted small",
      "This machine published no process values in the minutes either side. That is "
      + "not the same as the values having been flat."));
    return section;
  }
  section.append(el("p", "muted small",
    `${tags.shown} of ${tags.total} signal${tags.total === 1 ? "" : "s"} this machine `
    + `published, over the block and the ${d.window.before_minutes} minutes before it `
    + `and ${d.window.after_minutes} after. The block's start and end are marked on each.`));
  for (const trend of trends) {
    const box = smallChart(section, trend.tag || "value", "line", trend, {
      value: "mean",
      band: { low: "min", high: "max" },
      // Not zero-based: a cycle time around 1.2 s on a zero axis is a flat
      // line, and the whole question is the step either side of the block.
      zero: false,
      height: 120,
      markers: trend.markers,
      y: { label: trend.tag },
    });
    if (trend.samples === 0) {
      const last = trend.last_before_window;
      box.append(el("p", "stop-watched", last
        ? `Nothing in the window drawn — it last reported ${last.value} at `
          + `${fmt.stamp(last.ts)}, ${kit.duration(last.seconds_before_window)} before `
          + "the window opened. A value that has not changed publishes nothing."
        : "Nothing in the window drawn, and this MES holds no earlier sample of it."));
    }
  }
  return closeWith(section, tags);
}

function tagsSummary(d) {
  const tags = d.tags || {};
  if (!(tags.trends || []).length) return "nothing published either side";
  return `${tags.shown} of ${tags.total} drawn`;
}

/* What quality saw after it, with the chart's own verdict - "the setup was
   followed by two low means" in one glance instead of four screens. */
function qualityBlock(d) {
  const section = stopBlock("What quality saw after it");
  const q = d.quality;
  section.append(stopTable(["Reading", "Characteristic", "Verdict", "After", "Chart"],
    (q.readings || []).map((row) => [
      `${row.value}${row.unit ? ` ${row.unit}` : ""}`
      + (row.below_spec ? " — under spec" : row.above_spec ? " — over spec" : ""),
      pageLink(`${row.material} ${row.characteristic}`,
               `/dashboard/spc?spec=${encodeURIComponent(row.material)}|${encodeURIComponent(row.characteristic)}`,
               false),
      row.result,
      kit.duration(row.seconds_after_the_block),
      row.signals.length
        ? row.signals.map((s) => `rule ${s.rule}: ${s.what}`).join("; ")
        : el("span", "muted small", "no rule fired"),
    ]),
    `Nothing was measured on this machine in the ${fmt.qty(q.minutes)} minutes after `
    + "this block."));
  if (q.total > q.shown) {
    section.append(el("p", "muted small",
      `${q.shown} of ${q.total} readings listed.`));
  }
  if (q.total) {
    section.append(el("p", "muted small",
      `${q.total} reading${q.total === 1 ? "" : "s"} — ${q.failed} outside `
      + `specification, ${q.with_a_signal} with a control-chart rule on it.`));
  }
  section.append(el("p", "muted small", `Scope: ${q.scope}.`));
  return closeWith(section, q);
}

function qualitySummary(d) {
  const q = d.quality || {};
  if (!q.total) return "nothing measured after it";
  return `${q.total} reading${q.total === 1 ? "" : "s"}, ${q.failed} out of spec`;
}

/* How much of the block's own stretch anybody was watching, and where the
   unwatched seconds went. Every chart in this product says this. */
function watchedBlock(d) {
  const section = stopBlock("How much of it anybody was watching");
  section.append(el("p", "stop-says", watchedLine(d) || ""));
  section.append(kit.ledgerSummary(d));
  return section;
}

function watchedSummary(d) {
  if (d.coverage === null || d.coverage === undefined) return "not known";
  return `${fmt.pct(d.coverage)} watched`;
}

async function loadOee() {
  const oee = await api(`/equipment/${CODE}/oee?hours=${hours()}`);
  kit.oeeBars($("#oee-bars"), oee);
  // Two different statements, and the header makes both: how much history
  // this MES holds for the machine, and how much of the window that was
  // *asked for* anybody watched. A figure read without the second is a
  // figure read as though it covered the shift.
  $("#oee-window").textContent =
    `— ${oee.window_hours.toFixed(2)} h of history · ${fmt.pct(oee.coverage)} of the window watched`;
  $("#oee-facts").textContent =
    `${fmt.qty(oee.good_qty)} good, ${fmt.qty(oee.scrap_qty)} scrap · running ${kit.duration(oee.runtime_seconds)}, down ${kit.duration(oee.downtime_seconds)}`;
  // The ledger under the figures, always: where the unwatched time went is
  // not a detail a reader should have to go to another screen for.
  $("#oee-ledger").replaceChildren(kit.ledgerSummary(oee));
  $("#oee-explain-code").textContent = CODE;
}

/* ---------- maintenance ---------- */

async function loadMaintenance() {
  const [plans, orders] = await Promise.all([
    api("/maintenance/plans").catch(() => []),
    api(`/maintenance/orders?equipment=${encodeURIComponent(CODE)}&limit=20`).then((p) => p.items).catch(() => []),
  ]);
  const mine = plans.filter((p) => p.equipment === CODE);
  const planList = $("#maint-plans");
  planList.replaceChildren();
  if (!mine.length) planList.append(el("li", "muted", "No maintenance plan names this machine."));
  for (const p of mine) {
    const li = el("li", p.due ? "running-op" : null);
    const what = el("div", "what");
    what.append(el("div", "mono", `${p.code} · ${p.name || ""}`));
    what.append(el("div", "muted small", p.reason || p.status || ""));
    li.append(what);
    planList.append(li);
  }
  const orderList = $("#maint-orders");
  orderList.replaceChildren();
  if (!orders.length) orderList.append(el("li", "muted", "No maintenance work recorded on this machine."));
  for (const o of orders) {
    const li = el("li", o.status === "in_progress" ? "running-op" : null);
    const what = el("div", "what");
    what.append(el("div", "mono", `${o.code} · ${o.kind} · ${o.status}`));
    what.append(el("div", "muted small", `${o.summary || ""}${o.findings ? ` — ${o.findings}` : ""} · ${fmt.stamp(o.completed_at || o.started_at || o.raised_at)}`));
    li.append(what);
    if (FS.can("maintenance.perform") && o.status !== "done") {
      const action = o.status === "due" ? "start" : "complete";
      const btn = el("button", "ghost", action === "start" ? "Start" : "Complete");
      btn.type = "button";
      btn.addEventListener("click", async () => {
        try {
          const body = action === "complete" ? { findings: window.prompt("What was found / done?") || null } : undefined;
          await api(`/maintenance/orders/${o.code}/${action}`, { method: "POST", body });
          FS.toast(`${o.code} ${action === "start" ? "started" : "complete"}`);
          await loadMaintenance();
        } catch (err) { FS.toast(err.message, "bad"); }
      });
      li.append(btn);
    }
    orderList.append(li);
  }
}

function wireCorrective() {
  $("#corrective").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api("/maintenance/corrective", { method: "POST", body: {
        equipment: CODE, summary: $("#cm-summary").value.trim(), reason: $("#cm-reason").value.trim() || null } });
      FS.toast(`${out.code} raised`);
      $("#cm-summary").value = ""; $("#cm-reason").value = "";
      await loadMaintenance();
    } catch (err) { FS.toast(err.message, "bad"); }
  });
}

/* ---------- operate (read-only here) ---------- */

async function loadQueue() {
  const rows = await api(`/workorders/dispatch?equipment=${encodeURIComponent(CODE)}`);
  const list = $("#queue");
  list.replaceChildren();
  const open = rows.filter((r) => r.status !== "done");
  if (!open.length) list.append(el("li", "muted", "Nothing queued on this machine."));
  for (const r of open) {
    const li = el("li", r.status === "running" ? "running-op" : null);
    const what = el("div", "what");
    what.append(el("div", "mono", `${r.order} · op ${r.seq}`));
    what.append(el("div", "muted small", `${r.operation} — ${fmt.qty(r.good_qty)} of ${fmt.qty(r.quantity)} good`));
    li.append(what);
    list.append(li);
  }
}

async function loadUnassigned() {
  const body = await api(`/execution/unassigned?equipment=${encodeURIComponent(CODE)}&limit=20`);
  const list = $("#unassigned");
  list.replaceChildren();
  // The count states the whole selection, not the page: "showing 20 of 63"
  // is the sentence that stops a page reading as the whole story.
  $("#unassigned-count").textContent = body.total
    ? `${fmt.qty(body.good_total)} good · ${fmt.qty(body.scrap_total)} scrap `
      + `in ${body.total} booking${body.total === 1 ? "" : "s"}`
      + (body.has_more ? ` — showing the latest ${body.items.length}` : "")
    : "";
  if (!body.total) {
    list.append(el("li", "muted", "Nothing counted here without an order."));
    return;
  }
  for (const row of body.items) {
    const li = el("li");
    const what = el("div", "what");
    what.append(el("div", "mono", `${fmt.qty(row.good)} good · ${fmt.qty(row.scrap)} scrap`));
    // Where the number came from, on the row with the number. A count this
    // machine made and a count another system says it made are different
    // claims, and the list is unreadable if it cannot show which is which.
    const from = row.source_system ? `told by ${row.source_system}` : `counted here (${row.source})`;
    what.append(el("div", "muted small", `${fmt.clock(row.ts)} — no order · ${from}`));
    li.append(what);
    list.append(li);
  }
}

async function loadOperate() {
  await Promise.all([loadQueue(), loadUnassigned()]);
}

/* ---------- tabs ---------- */

const LOADERS = { trend: loadTrend, timeline: loadTimeline, oee: loadOee, maintenance: loadMaintenance, operate: loadOperate };

function onTab(name) {
  clearInterval(nowTimer);
  if (name === "now") {
    refreshNow();
    nowTimer = setInterval(refreshNow, NOW_REFRESH_MS);
    return;
  }
  (LOADERS[name] || (() => Promise.resolve()))().catch(fail);
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  wireCorrective();
  openOnClick();
  // Awaited before the first refresh: this plant's own window is what the
  // first load should be drawn over, not the product's eight hours.
  await FS.window.bind($("#hours"), () => onTab(FS.tabs.current()));
  $("#trend-tag").addEventListener("change", (e) => { trendTag = e.target.value; loadTrend().catch(fail); });
  await refreshNow();
  FS.tabs.init(document, onTab);
})();
