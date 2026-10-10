/* The Maintenance workspace.

   The Due tab is the maintenance supervisor's screen, read top to bottom:
   what this shift did, the rules that did it, who is on, what nobody has,
   and the plans that have come due. Then three more tabs for the work that
   is open, the plans themselves and what was done.

   Due-ness comes from the machine's own use, which is why the reason on
   every job reads "212 h against a 200 h plan" rather than a date. A rule
   comes from the supervisor as a sentence with blanks in it, and goes back
   to them as the sentence the dispatcher itself says. Everything here is the
   same API the machine page's Maintenance tab, `fsmes maintenance` and the
   agent's maintenance tools use. */

const { $, el, api, fmt, toast } = window.FS;

const REFRESH_MS = 8000;
let machines = [];
let completing = null;
let timer = null;

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

function fillMachines(select, keepAny) {
  const keep = select.value;
  select.replaceChildren();
  if (keepAny) select.appendChild(new Option("Any machine", ""));
  for (const m of machines) select.appendChild(new Option(m, m));
  if ([...select.options].some((o) => o.value === keep)) select.value = keep;
}

/* ---------- due ---------- */

/* How far through a plan's interval, coloured by the plant's own judgment.

   It takes the whole row rather than the fraction, because the server has
   already decided: `due` and `due_soon` are on every plan it returns, computed
   from `[process] maintenance_due_soon_fraction`. The eight tenths that used
   to be written here was the second copy of that judgment, so a plant that
   warned at 70% got a bar that turned amber at 80% and a sentence beside it
   that said *due soon* ten per cent earlier. */
function bar(p) {
  const wrap = el("div", "bar");
  const fill = el("i");
  fill.style.width = `${Math.min(100, p.fraction * 100)}%`;
  if (p.due) fill.style.background = "var(--down)";
  else if (p.due_soon) fill.style.background = "var(--idle)";
  wrap.append(fill);
  return wrap;
}

function planRow(p) {
  const li = el("li", p.due ? "running-op" : null);
  const what = el("div", "what");
  const head = el("div", "mono");
  head.append(`${p.plan} · `, FS.link("machine", p.equipment), ` · ${p.name}`);
  what.append(head);
  what.append(el("div", "muted small", `${fmt.qty(p.used)} of ${fmt.qty(p.interval)} ${p.unit}` +
    (p.never_serviced ? " · never serviced" : "") +
    (p.due ? " — due" : p.due_soon ? " — due soon" : "") +
    ` · ~${fmt.qty(p.expected_minutes)} min down` + (p.document ? ` · ${p.document}` : "")));
  what.append(bar(p));
  li.append(what);
  return li;
}

async function loadDue() {
  const d = await api("/maintenance/due");
  $("#kpi-due").textContent = d.due.length;
  $("#kpi-soon").textContent = d.due_soon.length;
  /* Say what "soon" means here. The counting is the server's, from
     `[process] maintenance_due_soon_fraction`; this only prints the number the
     count was made with, so a plant that warns at 70% reads 70% rather than
     having to know. */
  const said = await FS.screens;
  const soon = said && said.maintenance_due_soon_fraction;
  $("#kpi-soon-sub").textContent = soon
    ? `past ${Math.round(soon * 100)}% of the interval` : "";
  $("#kpi-open").textContent = d.backlog.open;
  $("#kpi-open-split").textContent = `${d.backlog.preventive} preventive · ${d.backlog.corrective} corrective`;
  $("#kpi-downtime").textContent = `${d.backlog.expected_downtime_hours} h`;
  const list = $("#due-list");
  list.replaceChildren();
  const rows = [...d.due, ...d.due_soon];
  if (!rows.length) list.append(el("li", "muted", "Nothing is due. Every plan is inside its interval."));
  for (const p of rows) list.append(planRow(p));
  window.__fsmesPageData = { due: d };
}

async function raiseDue() {
  try {
    const out = await api("/maintenance/raise", { method: "POST" });
    toast(out.count ? `Raised ${out.count} job(s).` : "Nothing new to raise - open work already covers what is due.");
    await Promise.all([loadDueTab(), loadWork()]);
  } catch (err) { toast(err.message, "bad"); }
}

/* ---------- the supervisor's screen ---------- */

/* Four panels, read top to bottom: what this shift did, the rules that did
   it, who is on, and what nobody has. Two reads cover the first and the
   last - /maintenance/shift answers both in one query per table, which is
   what makes it survive two thousand orders - and one each for the rules and
   the roster.

   No wording is invented here. A rule's sentence arrives from the server on
   every rule as `says`, and again as the preview under the blanks from
   `POST /maintenance/rules?dry_run=1`, which builds the draft, says it, and
   writes nothing. The words the blanks may be filled with arrive as
   `vocabulary` on /maintenance/rules. So the sentence on the screen, the
   sentence `fsmes maintenance rules` prints and the sentence in the audit row
   are one set of words, and this file holds no copy of them to drift. */

let vocab = null;
let crew = { people: [], total: 0, available: 0, shift: null };
let giving = null;
let previewTimer = null;

/* A person as a supervisor reads them: the name, with the code the plant
   stores beside it in small type. A screen that says "MT-04" is asking its
   reader to keep a staff list in their head. */
function who(code, name) {
  const span = el("span");
  if (!code) return el("span", "muted", "nobody");
  span.append(name || code);
  if (name) span.append(el("span", "beside", ` ${code}`));
  return span;
}

function shiftRow(o) {
  const li = el("li", o.status === "in_progress" ? "running-op" : null);
  const what = el("div", "what");
  const head = el("div", "mono");
  head.append(`${o.code} · `, FS.link("machine", o.equipment),
              ` · ${o.status.replace("_", " ")}`);
  if (!o.this_shift) head.append(el("span", "beside", " · raised before this shift"));
  what.append(head);
  what.append(el("div", null, o.summary
    + (o.skill ? ` · needs ${o.skill}` : "")
    + (o.priority === null ? "" : ` · priority ${o.priority}`)
    + (o.needs_stop ? " · needs the machine stopped" : "")));

  const hands = el("div", "muted small");
  if (o.assigned_to) {
    hands.append("with ", who(o.assigned_to, o.assigned_to_name));
    if (o.completed_at) hands.append(` · done ${fmt.clock(o.completed_at)}`);
    else if (o.started_at) hands.append(` · started ${fmt.clock(o.started_at)}`);
    else if (o.assigned_at) hands.append(` · given ${fmt.clock(o.assigned_at)}`);
  } else {
    hands.append(`nobody has it · raised ${fmt.clock(o.raised_at)}`);
  }
  what.append(hands);

  /* Which rule sent it, said as the rule says itself. A supervisor looking at
     a job in somebody's hands should not have to go and look the rule up. */
  const by = el("div", "muted small");
  if (o.by_rule && o.by_rule_says) by.append(`${o.by_rule} sent it — ${o.by_rule_says}`);
  else if (o.by_rule) by.append("given out by hand, not by a rule");
  else by.append("no rule has handed it out");
  what.append(by);

  li.append(what);
  /* *Why?* on a job somebody already has, not only on one nobody got: the
     question a supervisor asks about an order that landed is the same
     question - which rule, which people, why that one. The walk opens in the
     panel below, which is the supervisor's own, so the button is offered to
     whoever that panel is offered to. */
  if (FS.can("maintenance.plan")) {
    const why = el("button", "ghost", "Why?");
    why.type = "button";
    why.setAttribute("data-assist", "maintenance-why");
    why.addEventListener("click", () => showWhy(o.code).catch((err) => toast(err.message, "bad")));
    li.append(why);
  }
  return li;
}

async function loadShift() {
  const s = await api("/maintenance/shift");
  $("#shift-key").textContent = s.shift ? `— ${s.shift.key}` : "";
  /* The server's sentence, verbatim. The screen's job is to make it big. */
  $("#shift-sentence").textContent = s.sentence
    || (s.why_empty || "No shift pattern covers this moment, so there is no shift to read.");

  const mine = s.orders.filter((o) => o.this_shift);
  const carried = s.orders.length - mine.length;
  const list = $("#shift-list");
  list.replaceChildren();
  if (!s.orders.length) {
    list.append(el("li", "muted", s.shift
      ? "No maintenance work on this shift, and nothing open from before it."
      : "Nothing to show until the plant's shift patterns are loaded."));
  }
  for (const o of [...mine, ...s.orders.filter((o2) => !o2.this_shift)]) {
    list.append(shiftRow(o));
  }
  const counts = s.counts || {};
  $("#shift-key").textContent += mine.length
    ? ` · ${mine.length} this shift (${counts.by_rules || 0} by the rules, ${counts.by_hand || 0} by hand)`
      + (carried ? ` · ${carried} still open from before` : "")
    : carried ? ` · ${carried} still open from before this shift` : "";

  drawWaiting(s);
  return s;
}

/* ---------- waiting, and why ---------- */

function waitingRow(o) {
  const li = el("li");
  const what = el("div", "what");
  const head = el("div", "mono");
  head.append(`${o.code} · `, FS.link("machine", o.equipment));
  if (o.machine_state) head.append(el("span", "beside", ` · ${o.machine_state}`));
  what.append(head);
  what.append(el("div", null, o.summary
    + (o.skill ? ` · needs ${o.skill}` : " · no trade stated")
    + (o.priority === null ? "" : ` · priority ${o.priority}`)));
  const held = el("div", "muted small");
  if (o.assigned_to) held.append("with ", who(o.assigned_to, o.assigned_to_name));
  else held.append(`raised ${fmt.stamp(o.raised_at)}`);
  what.append(held);
  li.append(what);

  const give = el("button", "ghost", "Give to…");
  give.type = "button";
  give.setAttribute("data-assist", "maintenance-give");
  give.addEventListener("click", () => openGive(o));
  li.append(give);

  const why = el("button", "ghost", "Why?");
  why.type = "button";
  why.setAttribute("data-assist", "maintenance-why");
  why.addEventListener("click", () => showWhy(o.code).catch((err) => toast(err.message, "bad")));
  li.append(why);
  return li;
}

/* Grouped by the dispatcher's own reasons, each group with its own total and
   its own sentence about what to do. The reasons and their wording are the
   server's: WAITING_REASONS travels on the read. */
function drawWaiting(s) {
  const box = $("#waiting-groups");
  box.replaceChildren();
  $("#waiting-count").textContent = `— ${s.waiting_total || 0} waiting`;
  if (!s.waiting || !s.waiting.length) {
    box.append(el("p", "muted", s.shift
      ? "Nothing is waiting: everything open is with somebody and nothing is held up."
      : "No shift, so nothing has been dispatched."));
    return;
  }
  for (const group of s.waiting) {
    const section = el("div", "waiting-group");
    section.append(el("h3", null, `${group.label} — ${group.total}`));
    section.append(el("p", "muted small", group.why));
    const ul = el("ul", "queue");
    for (const o of group.orders) ul.append(waitingRow(o));
    section.append(ul);
    box.append(section);
  }
}

/* The walk, as the command line prints it: lines, in the order the
   dispatcher took them. Every sentence in it - each rule's verdict, each
   person's verdict, what it would do now - is the server's own wording; only
   the indenting is this file's. */
function whyLines(out) {
  const lines = [`${out.order} on ${out.equipment} — ${out.summary}`];
  lines.push(`  ${out.status}, `
    + (out.priority_is_stated ? `priority ${out.priority}`
       : `no priority set, read as ${out.priority}`)
    + `, needs ${out.skill || "no particular trade"}`
    + `; shift ${out.shift || "none — no pattern covers this moment"}`);
  if (out.held_by) {
    let held = `  ${out.held_by} has it`;
    if (out.held_by_rule) held += `, given by ${out.held_by_rule}`;
    if (out.held_since) held += ` at ${fmt.stamp(out.held_since)}`;
    lines.push(held);
  } else if (out.recorded_unassigned_reason) {
    lines.push(`  nobody has it: ${out.recorded_unassigned_reason}`);
  } else {
    lines.push("  nobody has it, and it has not been through dispatch yet");
  }
  lines.push(`  ${out.rules_tried.length} rule(s) tried, in order:`);
  for (const tried of out.rules_tried) {
    if (tried.matched && tried.because === null) lines.push(`    ${tried.rule}: matched, and found somebody`);
    else if (tried.matched) lines.push(`    ${tried.rule}: matched, but ${tried.because}`);
    else lines.push(`    ${tried.rule}: ${tried.because}`);
  }
  if (out.rule_says) lines.push(`  ${out.rule} says: ${out.rule_says}`);
  lines.push(`  ${out.considered.length} ${out.considered.length === 1 ? "person" : "people"} considered:`);
  if (!out.considered.length) lines.push("    nobody — no rule got as far as looking at the roster");
  for (const row of out.considered) {
    lines.push(`    ${row.chosen ? "→" : " "} ${row.person} (${row.name}): ${row.verdict}`);
  }
  /* What it is waiting for comes before the hypothetical, because for a job
     somebody already has it is the answer and the line under it is not. */
  if (out.waiting_clause) lines.push(`  it is waiting: ${out.waiting_clause}`);
  lines.push(`  were it handed out now it would ${out.would_now}.`);
  return lines.join("\n");
}

async function showWhy(code) {
  const out = await api(`/maintenance/dispatch/${code}/explain`);
  $("#why-order").textContent = code;
  $("#why-lines").textContent = whyLines(out);
  $("#why-box").classList.remove("hidden");
  $("#why-box").scrollIntoView({ block: "nearest" });
}

/* Who could take it: on this shift, holding the trade the job needs. A
   supervisor may still overrule the skills table - the API allows it and
   says so in the audit row - but the list offered is the honest one. */
function openGive(o) {
  giving = o.code;
  $("#give-order").textContent = o.code;
  const select = $("#give-person");
  select.replaceChildren();
  const able = crew.people.filter((p) =>
    !o.skill || p.skills.some((s) => s.skill === o.skill));
  if (!able.length) {
    select.append(new Option(
      o.skill ? `nobody on this shift holds ${o.skill}` : "nobody is on this shift", ""));
  }
  for (const p of able) {
    const load = p.on_now ? `on ${p.on_now.order}` : "free";
    select.append(new Option(
      `${p.name} (${p.person}) — ${load}, ${p.open_orders} open`
      + (p.available ? "" : ` — ${p.reason || "not available"}`), p.person));
  }
  $("#give-box").classList.remove("hidden");
  select.focus();
}

/* ---------- the rules, as sentences ---------- */

function ruleRow(rule, index, total) {
  const li = el("li", rule.active ? null : "switched-off");
  li.append(el("span", "seq", String(index + 1)));
  const what = el("div", "what");
  what.append(el("div", null, rule.says));
  const note = el("div", "muted small");
  note.append(rule.active ? "on" : "off — not tried, and the work it gave out still names it");
  note.append(` · ${rule.code}`);
  note.append(rule.handed_out
    ? ` · has handed out ${rule.handed_out} order(s)`
    : " · has handed out nothing yet");
  what.append(note);
  li.append(what);

  const up = el("button", "ghost", "↑");
  up.type = "button";
  up.title = "Try this rule earlier";
  up.disabled = index === 0;
  up.addEventListener("click", () => changeRule(rule.code, { move: "up" }));
  li.append(up);

  const down = el("button", "ghost", "↓");
  down.type = "button";
  down.title = "Try this rule later";
  down.disabled = index === total - 1;
  down.addEventListener("click", () => changeRule(rule.code, { move: "down" }));
  li.append(down);

  const onoff = el("button", "ghost", rule.active ? "Switch off" : "Switch on");
  onoff.type = "button";
  onoff.addEventListener("click", () => changeRule(rule.code, { active: !rule.active }));
  li.append(onoff);

  /* Remove only for a rule that never gave work out. One that did is
     switched off instead, because every order it sent still names it and an
     audit trail has to keep pointing somewhere. */
  if (!rule.handed_out) {
    const gone = el("button", "ghost", "Remove");
    gone.type = "button";
    gone.addEventListener("click", async () => {
      try {
        await api(`/maintenance/rules/${rule.code}`, { method: "DELETE" });
        toast(`${rule.code} removed`);
        await loadSupervisor();
      } catch (err) { toast(err.message, "bad"); }
    });
    li.append(gone);
  }
  return li;
}

async function changeRule(code, body) {
  try {
    const out = await api(`/maintenance/rules/${code}`, { method: "PATCH", body });
    toast(out.says);
    await loadSupervisor();
  } catch (err) { toast(err.message, "bad"); }
}

async function loadRules() {
  const d = await api("/maintenance/rules");
  if (!vocab) { vocab = d.vocabulary; fillBlanks(); }
  const list = $("#rules-list");
  list.replaceChildren();
  if (!d.rules.length) {
    list.append(el("li", "muted", d.house_default
      ? `No rules written. Until one is, the house default stands: ${d.house_default.says}`
      : "No rules written."));
  }
  d.rules.forEach((rule, i) => list.append(ruleRow(rule, i, d.rules.length)));
  $("#rules-count").textContent = `— ${d.rules.length} written, `
    + `${d.tried_in_order.length} tried in this order`;
}

/* The blanks, filled from the plant's own vocabulary. "Anywhere" and
   "whatever the skill" are the empty value, which is what the dispatcher
   means by a blank left out. */
function fillBlanks() {
  if (!vocab) return;
  const machine = $("#rule-equipment");
  machine.replaceChildren();
  machine.append(new Option("anywhere", ""));
  for (const e of vocab.equipment) machine.append(new Option(`${e.code} — ${e.name}`, e.code));

  const skill = $("#rule-skill");
  skill.replaceChildren();
  skill.append(new Option("whatever the skill", ""));
  for (const s of vocab.skills) skill.append(new Option(`needing ${s.code} — ${s.name}`, s.code));

  const priority = $("#rule-priority");
  priority.replaceChildren();
  priority.append(new Option("any", ""));
  for (const p of vocab.priorities) {
    priority.append(new Option(`${p.value} (${p.name}) or worse`, String(p.value)));
  }

  const how = $("#rule-strategy");
  how.replaceChildren();
  for (const s of vocab.strategies) how.append(new Option(s.says, s.value));
}

function blanks() {
  return {
    equipment: $("#rule-equipment").value || null,
    skill: $("#rule-skill").value || null,
    priority_at_least: $("#rule-priority").value ? Number($("#rule-priority").value) : null,
    strategy: $("#rule-strategy").value,
  };
}

/* The preview is the server's sentence, not this file's guess at it: the
   draft is built, said and thrown away. A client-side renderer would be a
   second copy of the wording, and the first time somebody changed `says` the
   screen would be quietly lying about what the dispatcher will do. */
async function preview() {
  try {
    const out = await api("/maintenance/rules?dry_run=1", { method: "POST", body: blanks() });
    $("#rule-preview").textContent = out.says;
  } catch (err) {
    $("#rule-preview").textContent = err.message;
  }
}

function wireRules() {
  $("#rule-new").addEventListener("click", () => {
    $("#rule-form").classList.remove("hidden");
    preview();
    $("#rule-equipment").focus();
  });
  $("#rule-cancel").addEventListener("click", () => {
    $("#rule-form").classList.add("hidden");
  });
  for (const id of ["rule-equipment", "rule-skill", "rule-priority", "rule-strategy"]) {
    $(`#${id}`).addEventListener("change", () => {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(preview, 120);
    });
  }
  $("#rule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api("/maintenance/rules", { method: "POST", body: blanks() });
      toast(out.says);
      $("#rule-form").classList.add("hidden");
      await loadSupervisor();
    } catch (err) { toast(err.message, "bad"); }
  });
}

/* ---------- who is on ---------- */

const NO_TRADE = "no trade recorded";

function personLine(p) {
  const li = el("li");
  const name = el("span", "who");
  name.append(p.name);
  li.append(name);
  li.append(el("span", "beside", p.person));
  if (p.on_now) {
    const job = el("span", "muted");
    job.append(`on ${p.on_now.order} at ${p.on_now.equipment} — ${p.on_now.summary}`);
    if (p.on_now.minutes !== null) {
      job.append(p.on_now.status === "in_progress"
        ? ` · ${fmt.qty(p.on_now.minutes)} min worked`
        : ` · waiting ${fmt.qty(p.on_now.minutes)} min`);
    }
    if (p.on_now.needs_stop) job.append(" · needs the machine stopped");
    li.append(job);
  } else {
    li.append(el("span", "muted", "free"));
  }
  /* What they are carrying: the jobs, and the minutes those jobs are expected
     to take. Unknown is not zero - a person with no open work reads "nothing
     on", not "0.0 h". */
  li.append(el("span", "muted", p.open_orders
    ? `${p.open_orders} open · ${fmt.qty(p.minutes_loaded / 60)} h loaded`
    : "nothing on"));
  if (!p.available) li.append(el("span", "muted", p.reason || "not available"));
  return li;
}

/* Grouped by trade, counts on the summary, names when it is opened. At three
   hundred people the counts are the answer and the names are the follow-up;
   a flat list of three hundred is not a screen anybody reads. */
function tradeGroup(trade, people) {
  const box = el("details", "trade");
  const head = el("summary");
  const free = people.filter((p) => p.available && !p.on_now).length;
  head.append(trade);
  head.append(el("span", "muted", `${people.length} on, ${free} free`));
  box.append(head);
  const ul = el("ul", "trade-people");
  for (const p of people) ul.append(personLine(p));
  box.append(ul);
  return box;
}

async function loadRoster() {
  crew = await api("/maintenance/roster");
  const box = $("#roster-trades");
  box.replaceChildren();
  if (!crew.shift) {
    $("#roster-count").textContent = "— no shift";
    $("#roster-shift").textContent = crew.why_empty
      || "No shift pattern covers this moment, so nobody is rostered.";
    box.append(el("p", "muted", "Nobody is on, because the plant has no shift now."));
    return;
  }
  $("#roster-count").textContent = `— ${crew.total} on, ${crew.available} available`;
  $("#roster-shift").textContent =
    `${crew.shift.code}, ${crew.shift.day} · ${fmt.clock(crew.shift.starts)}–${fmt.clock(crew.shift.ends)}`;
  if (!crew.people.length) {
    box.append(el("p", "muted", "Nobody is rostered on this shift."));
    return;
  }
  const byTrade = new Map();
  for (const p of crew.people) {
    const trades = p.skills.length ? p.skills.map((s) => s.skill) : [NO_TRADE];
    for (const trade of trades) {
      if (!byTrade.has(trade)) byTrade.set(trade, []);
      byTrade.get(trade).push(p);
    }
  }
  const trades = [...byTrade.keys()].sort((a, b) =>
    a === NO_TRADE ? 1 : b === NO_TRADE ? -1 : a.localeCompare(b));
  for (const trade of trades) box.append(tradeGroup(trade, byTrade.get(trade)));
}

function wireSupervisor() {
  wireRules();
  $("#run-dispatch").addEventListener("click", async () => {
    try {
      const out = await api("/maintenance/dispatch", { method: "POST" });
      toast(`${out.assigned} of ${out.considered} due order(s) handed out.`);
      await Promise.all([loadSupervisor(), loadWork()]);
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#give-cancel").addEventListener("click", () => {
    giving = null;
    $("#give-box").classList.add("hidden");
  });
  $("#give-confirm").addEventListener("click", async () => {
    const person = $("#give-person").value;
    if (!giving || !person) return;
    try {
      await api(`/maintenance/orders/${giving}/assign`, { method: "POST", body: { person } });
      toast(`${giving} is ${person}'s`);
      giving = null;
      $("#give-box").classList.add("hidden");
      await Promise.all([loadSupervisor(), loadWork()]);
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#why-close").addEventListener("click", () => $("#why-box").classList.add("hidden"));
}

async function loadSupervisor() {
  /* The rules read is skipped for somebody who cannot write one: their panel
     is hidden, and a read nobody can see is a read nobody should pay for. */
  await Promise.all([loadShift(), loadRoster(),
                     ...(FS.can("maintenance.plan") ? [loadRules()] : [])]);
}

/* ---------- work ---------- */

async function loadWork() {
  // The server's answer for open work, not "open" picked out of the newest
  // two hundred orders: a year in, the open ones are not among those.
  const machine = $("#work-machine").value;
  /* Three statuses, not two. `assigned` is open work - somebody has it and
     has not started - and leaving it out made the Due now tile count three
     jobs while the list below it showed two, with the chiller clean nowhere
     on the screen (seen on the lab, 2026-10-09 18:26). */
  const page = await api(`/maintenance/orders?status=due&status=assigned&status=in_progress&limit=200${machine ? `&equipment=${encodeURIComponent(machine)}` : ""}`);
  const open = page.items;
  const list = $("#work-list");
  list.replaceChildren();
  if (!open.length) list.append(el("li", "muted", "No open maintenance work."));
  for (const o of open) {
    const li = el("li", o.status === "in_progress" ? "running-op" : null);
    const what = el("div", "what");
    const head = el("div", "mono");
    head.append(`${o.code} · `, FS.link("machine", o.equipment), ` · ${o.kind}`);
    what.append(head);
    const line = el("div", "muted small");
    line.append(o.summary + (o.reason ? ` — ${o.reason}` : ""));
    /* Names, not codes. "started 6:20:26 PM by a four-character staff code"
       asked a supervisor to keep the register in their head; the code still
       travels, in small type beside the name. */
    if (o.started_at) {
      line.append(" · started ", fmt.clock(o.started_at), " by ",
                  who(o.performed_by, o.performed_by_name));
    } else if (o.assigned_to) {
      line.append(" · with ", who(o.assigned_to, o.assigned_to_name),
                  ` since ${fmt.clock(o.assigned_at)}`);
    } else {
      line.append(` · raised ${fmt.stamp(o.raised_at)}`);
    }
    if (o.needs_stop) line.append(" · needs the machine stopped");
    what.append(line);
    li.append(what);
    if (FS.can("maintenance.perform")) {
      /* An assigned job is started, not completed: the button on it has to be
         the one that is true of it. Before this, an `assigned` order that
         reached the list was offered Complete… and the start time it would
         have reported was never recorded. */
      if (o.status === "due" || o.status === "assigned") {
        const start = el("button", "ghost", "Start");
        start.type = "button";
        start.addEventListener("click", async () => {
          try { await api(`/maintenance/orders/${o.code}/start`, { method: "POST" }); toast(`${o.code} started`); await loadWork(); }
          catch (err) { toast(err.message, "bad"); }
        });
        li.append(start);
      } else {
        const done = el("button", "ghost", "Complete…");
        done.type = "button";
        done.addEventListener("click", () => {
          completing = o.code;
          $("#findings").value = "";
          $("#complete-box").classList.remove("hidden");
          $("#findings").focus();
        });
        li.append(done);
      }
    }
    list.append(li);
  }
  $("#work-count").textContent = page.has_more
    ? `— ${open.length} of ${page.total.toLocaleString()} open`
    : `— ${open.length} open`;
}

function wireWork() {
  $("#work-machine").addEventListener("change", () => loadWork().catch(fail));
  $("#complete-cancel").addEventListener("click", () => { completing = null; $("#complete-box").classList.add("hidden"); });
  $("#complete-confirm").addEventListener("click", async () => {
    if (!completing) return;
    try {
      await api(`/maintenance/orders/${completing}/complete`, { method: "POST",
        body: { findings: $("#findings").value.trim() || null } });
      toast(`${completing} complete`);
      completing = null;
      $("#complete-box").classList.add("hidden");
      await Promise.all([loadWork(), loadDueTab()]);
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#corrective").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api("/maintenance/corrective", { method: "POST", body: {
        equipment: $("#cm-machine").value, summary: $("#cm-summary").value.trim(),
        reason: $("#cm-reason").value.trim() || null } });
      toast(`${out.code} raised on ${out.equipment}`);
      $("#cm-summary").value = ""; $("#cm-reason").value = "";
      await Promise.all([loadWork(), loadDueTab()]);
    } catch (err) { toast(err.message, "bad"); }
  });
}

/* ---------- plans ---------- */

const PLAN_PAGE = 25;
let plans = [];
let planOffset = 0;

async function loadPlans() {
  plans = await api("/maintenance/plans");
  drawPlans();
}

function drawPlans() {
  // A plan per machine is the norm, so this is the plant's machine count.
  const q = $("#plan-q").value.trim().toLowerCase();
  const machine = $("#plan-filter-machine").value;
  const trigger = $("#plan-filter-trigger").value;
  const matching = plans.filter((p) =>
    (!machine || p.equipment === machine) && (!trigger || p.trigger === trigger)
    && (!q || `${p.plan} ${p.name}`.toLowerCase().includes(q)));
  const page = FS.clientPage(matching, planOffset, PLAN_PAGE);
  planOffset = page.offset;
  const body = $("#plans-table tbody");
  body.replaceChildren();
  if (!page.items.length) { const tr = el("tr"); const td = el("td", "muted", plans.length ? "No plan matches." : "No plans yet."); td.colSpan = 8; tr.append(td); body.append(tr); }
  for (const p of page.items) {
    const tr = el("tr");
    tr.append(el("td", "code", `${p.plan} ${p.name}`));
    const where = el("td"); where.append(FS.link("machine", p.equipment)); tr.append(where);
    tr.append(el("td", "muted", p.trigger.replace("_", " ")));
    const prog = el("td"); prog.append(bar(p)); tr.append(prog);
    tr.append(el("td", "num", `${fmt.qty(p.used)} ${p.unit}`));
    tr.append(el("td", "num", fmt.qty(p.interval)));
    tr.append(el("td", "muted small", p.last_done_at ? fmt.stamp(p.last_done_at) : "never"));
    tr.append(el("td", "muted small", p.document || ""));
    body.append(tr);
  }
  $("#plan-count").textContent = FS.countText(page, plans.length);
  FS.pager($("#plan-pager"), page, (offset) => { planOffset = offset; drawPlans(); });
}

function wirePlans() {
  $("#plan-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api("/maintenance/plans", { method: "POST", body: {
        code: $("#plan-code").value.trim(), name: $("#plan-name").value.trim(),
        equipment: $("#plan-machine").value, trigger: $("#plan-trigger").value,
        interval: Number($("#plan-interval").value),
        /* Left blank, this plant's own default for a new plan stands. The
           thirty that used to be here was the product's, and sending it would
           have overruled a plant that had chosen forty-five without anybody
           meaning to. */
        expected_minutes: $("#plan-minutes").value === "" ? null : Number($("#plan-minutes").value),
        document_code: $("#plan-document").value.trim() || null } });
      toast(`${out.plan} created on ${out.equipment}`);
      $("#plan-form").reset();
      await Promise.all([loadPlans(), loadDue()]);
    } catch (err) { toast(err.message, "bad"); }
  });
}

/* ---------- history ---------- */

const HISTORY_PAGE = 50;
let historyOffset = 0;

async function loadHistory() {
  // The server's page of done work, filtered there: history grows for as
  // long as the plant runs and never fits on a screen.
  const machine = $("#history-machine").value;
  const kind = $("#history-kind").value;
  const q = $("#history-q").value.trim();
  const params = new URLSearchParams({ status: "done", limit: String(HISTORY_PAGE), offset: String(historyOffset) });
  if (machine) params.set("equipment", machine);
  if (kind) params.set("kind", kind);
  if (q) params.set("q", q);
  const page = await api(`/maintenance/orders?${params}`);
  historyOffset = page.offset;
  const done = page.items;
  const body = $("#history-table tbody");
  body.replaceChildren();
  if (!done.length) { const tr = el("tr"); const td = el("td", "muted", "No completed work matches."); td.colSpan = 8; tr.append(td); body.append(tr); }
  for (const o of done) {
    const tr = el("tr");
    tr.append(el("td", "code", o.code));
    const where = el("td"); where.append(FS.link("machine", o.equipment)); tr.append(where);
    tr.append(el("td", "muted", o.kind));
    tr.append(el("td", null, o.summary));
    tr.append(el("td", "muted small", o.findings || ""));
    tr.append(el("td", "num", o.downtime_minutes === null ? "—" : fmt.qty(o.downtime_minutes)));
    tr.append(el("td", "muted small", o.performed_by || ""));
    tr.append(el("td", "muted small", fmt.stamp(o.completed_at)));
    body.append(tr);
  }
  $("#history-count").textContent = `— ${done.length} of ${page.total.toLocaleString()} completed`;
  FS.pager($("#history-pager"), page, (offset) => { historyOffset = offset; loadHistory().catch(fail); });
}

/* ---------- tabs ---------- */

/* The Due tab is the supervisor's screen: the four panels above, and the
   plans that have come due below them. They refresh together, on the one
   timer, because they are one answer to one question. */
async function loadDueTab() {
  await Promise.all([loadDue(), loadSupervisor()]);
}

const LOADERS = { due: loadDueTab, work: loadWork, plans: loadPlans, history: loadHistory };

function onTab(name) {
  clearInterval(timer);
  const run = () => (LOADERS[name] || loadDue)().then(() => live(true)).catch(fail);
  run();
  timer = setInterval(run, REFRESH_MS);
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  machines = (await api("/equipment/states")).map((s) => s.equipment).sort();
  for (const id of ["cm-machine", "plan-machine"]) fillMachines($(`#${id}`), false);
  for (const id of ["work-machine", "history-machine", "plan-filter-machine"]) fillMachines($(`#${id}`), true);
  let typing = null;
  const reloadHistory = () => { historyOffset = 0; loadHistory().catch(fail); };
  $("#history-machine").addEventListener("change", reloadHistory);
  $("#history-kind").addEventListener("change", reloadHistory);
  $("#history-q").addEventListener("input", () => { clearTimeout(typing); typing = setTimeout(reloadHistory, 250); });
  for (const id of ["plan-q", "plan-filter-machine", "plan-filter-trigger"]) {
    $(`#${id}`).addEventListener("input", () => { planOffset = 0; drawPlans(); });
  }
  $("#raise-due").addEventListener("click", raiseDue);
  wireWork();
  wirePlans();
  wireSupervisor();
  await loadDueTab();
  FS.tabs.init(document, onTab);
})().catch(fail);
