/* AI: every conversation this plant's AI has had, and the state of the brains
   behind them.

   Scott, 2026-09-26: "These conversations should be traced within the MES as
   well, maybe add another tab, AI beside Setup to show all things 'AI' and
   really show that this is putting AI and Agents into this system as first
   class citizens." Twice that week the only record of an assistant failure
   was a screenshot he pasted into a chat window, and the BadRequestError that
   broke his afternoon was written down nowhere at all.

   One nav entry with tabs inside it, not a chip per brain - §2a of
   docs/design/config-assistance.md, the same rule the Configuration
   workspaces follow. Three tabs: the conversations (the reason the screen
   exists), the status of every brain on the box, and the one setting this
   page owns.

   Nothing here writes to the plant except the retention number, and that one
   goes through the Configuration endpoint every other live setting goes
   through. A screen that can read the trace and act on the plant would be an
   observability screen wearing a control room's name. */

const { $, el, api } = window.FS;

/* Twelve, which is not this file's number: `services/analysis.py` already
   answered "how many rows is one screenful" once. A list longer than that
   gets its search box; the eye is faster below it. */
const SCREENFUL = 12;

let conversations = [];
let openConversation = null;
let keptDays = null;

function fail(err) {
  const banner = $("#banner");
  banner.textContent = err && err.message ? err.message : String(err);
  banner.classList.remove("hidden");
}

function clearBanner() {
  $("#banner").classList.add("hidden");
}

/* ---------- conversations ---------- */

function sinceParam() {
  const days = $("#conv-since").value;
  return days ? `?since_days=${encodeURIComponent(days)}` : "";
}

async function loadConversations() {
  const page = await api(`/ai/conversations${sinceParam()}`);
  conversations = page.conversations || [];
  keptDays = page.kept_days;
  /* Rule 4: a truncated list must never look complete. `total` is every
     conversation the trace holds for this window; `showing` is how many came
     back, and the filter below narrows what is drawn out of those. */
  $("#conv-count").textContent =
    `— showing ${page.showing} of ${page.total}`;
  $("#conv-note").textContent =
    `$${(page.spend_usd || 0).toFixed(2)} over the turns this plant still keeps`
    + (keptDays > 0 ? `, which is ${keptDays} day(s) of them.` : `, which is all of them.`)
    + " The Console is the bill; this is an estimate at list prices.";
  $("#conv-q").closest(".filter-bar").classList.toggle(
    "hidden", conversations.length <= SCREENFUL);
  drawConversations();
}

function matches(row, q) {
  if (!q) return true;
  return [row.person, row.brain, row.screen, row.opened_with, row.session, row.model]
    .join(" ").toLowerCase().includes(q);
}

/* Where the conversation was opened, and how honest that one word is.
   A conversation follows a person around the product: naming only the
   first screen on a row whose turns crossed three would read as a fact
   about the whole of it, so the row says how many there were. A turn from
   before this was recorded, and the design chat, carry none - and "—" is
   *not recorded*, not "no screen". */
function screenText(row) {
  if (!row.screen) return "—";
  return row.screens > 1 ? `${row.screen} +${row.screens - 1}` : row.screen;
}

function outcomeText(counts) {
  const parts = Object.entries(counts || {}).map(([word, n]) => `${n} ${word}`);
  return parts.length ? parts.join(", ") : "—";
}

function drawConversations() {
  const q = $("#conv-q").value.trim().toLowerCase();
  const shown = conversations.filter((row) => matches(row, q));
  const body = $("#conv-table tbody");
  body.textContent = "";
  for (const row of shown) {
    const tr = el("tr");
    tr.dataset.session = row.session;
    tr.appendChild(el("td", "mono", FS.fmt.stamp(row.started)));
    tr.appendChild(el("td", null, row.person));
    tr.appendChild(el("td", null, row.brain));
    const where = el("td", row.screen ? "mono" : "muted", screenText(row));
    if (row.screen && row.screens > 1) {
      where.title = `${row.screens} screens in this conversation`;
    }
    tr.appendChild(where);
    tr.appendChild(el("td", "opened", row.opened_with));
    tr.appendChild(el("td", "mono", String(row.turns)));
    tr.appendChild(el("td", null, outcomeText(row.proposals)));
    const errors = el("td", "mono");
    /* Rule 8: no errors is a measurement and renders as 0; a conversation
       nobody has had renders as nothing at all, which is the row's absence. */
    errors.appendChild(row.errors
      ? el("span", "pill errors", String(row.errors))
      : document.createTextNode("0"));
    tr.appendChild(errors);
    tr.appendChild(el("td", "mono", `$${row.usd.toFixed(4)}`));
    /* Affordance is structural (STYLE.md rule 6): a button, so a keyboard and
       a screen reader reach the conversation without extra code, and it says
       what it opens rather than being a row that happens to react. */
    const go = el("td");
    const open = el("button", "ghost small", "Read it");
    open.setAttribute("aria-label", `Read ${row.person}'s conversation of `
                                    + `${FS.fmt.stamp(row.started)}`);
    open.addEventListener("click", () => showConversation(row.session).catch(fail));
    go.appendChild(open);
    tr.appendChild(go);
    body.appendChild(tr);
  }
  const empty = $("#conv-empty");
  empty.classList.toggle("hidden", shown.length > 0);
  if (!shown.length) {
    empty.textContent = conversations.length
      ? "No conversation in this window matches that."
      : "Nothing recorded in this window. An assistant nobody has used records "
        + "nothing, which is not the same as one that is broken — the Status tab "
        + "says whether a brain is on.";
  }
  if (openConversation && !shown.some((r) => r.session === openConversation)) {
    $("#conv-detail").classList.add("hidden");
    openConversation = null;
  }
}

/* ---------- one conversation ---------- */

async function showConversation(session) {
  openConversation = session;
  const page = await api(`/ai/turns?session=${encodeURIComponent(session)}`);
  const head = conversations.find((c) => c.session === session);
  $("#conv-detail").classList.remove("hidden");
  $("#detail-who").textContent = head
    ? `— ${head.person}, ${head.brain} brain, ${head.model || "model not recorded"}`
    : `— ${session}`;
  $("#detail-facts").textContent =
    `${page.showing} of ${page.total} turn(s), oldest first. `
    + "Each turn is what the person was shown: their words, the assistant's, "
    + "one line per tool call, and every proposal with what became of it.";
  const box = $("#turns");
  box.textContent = "";
  for (const turn of page.turns) box.appendChild(drawTurn(turn));
  $("#conv-detail").scrollIntoView({ block: "nearest" });
}

function drawTurn(turn) {
  const node = el("div", `turn${turn.error ? " failed" : ""}`);

  const when = el("div", "when");
  when.appendChild(el("span", null, FS.fmt.stamp(turn.ts)));
  when.appendChild(el("span", null, turn.kind));
  if (turn.error) when.appendChild(el("span", "pill failed", turn.error));
  if (turn.usd) when.appendChild(el("span", null, `$${turn.usd.toFixed(4)}`));
  const tokens = turn.tokens || {};
  if (tokens.input || tokens.output) {
    when.appendChild(el("span", null,
      `${tokens.input} in / ${tokens.output} out`));
  }
  node.appendChild(when);

  if (turn.asked) node.appendChild(el("p", "asked", turn.asked));

  if (turn.tools && turn.tools.length) {
    const calls = el("div", "calls");
    for (const call of turn.tools) {
      const line = el("div", `call ${call.ok === false ? "bad" : "ok"}`);
      const args = Object.entries(call.args || {})
        .map(([k, v]) => `${k}=${v}`).join(" ");
      line.appendChild(el("code", "what", `${call.tool} ${args}`.trim()));
      line.appendChild(el("span", "said-back", call.summary || ""));
      calls.appendChild(line);
    }
    node.appendChild(calls);
  }

  for (const proposal of turn.proposals || []) {
    node.appendChild(drawProposal(proposal));
  }

  if (turn.guide) {
    node.appendChild(el("p", "walk",
      `Walkthrough “${turn.guide}” was put on their screen`
      + (turn.guide_steps ? ` (${turn.guide_steps} steps)` : "")
      + ". How far they got is not recorded — the walk runs in their browser."));
  }

  if (turn.said) node.appendChild(el("p", "said", turn.said));
  return node;
}

function drawProposal(proposal) {
  const box = el("div", "proposal");
  const head = el("div", "head");
  head.appendChild(el("code", null, proposal.tool || "?"));
  const outcome = proposal.outcome || "open";
  const cls = { confirmed: "confirmed", declined: "declined",
                failed: "failed" }[outcome] || "";
  head.appendChild(el("span", `pill ${cls}`, outcome));
  box.appendChild(head);

  const args = Object.entries(proposal.args || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (args.length) {
    const list = el("dl");
    for (const [k, v] of args) {
      list.appendChild(el("dt", null, k));
      list.appendChild(el("dd", null, String(v)));
    }
    box.appendChild(list);
  }

  if (proposal.entity_id) {
    /* The link decision 0035 needs on this page: the trace says what was
       proposed, the audit trail says what changed, and both name the same
       entity. Ops is where the audit trail is read. */
    const line = el("p", "audited");
    line.appendChild(document.createTextNode("Audited as "));
    line.appendChild(el("code", null,
      `${proposal.action || "write"} ${proposal.entity_type} ${proposal.entity_id}`));
    if (proposal.action) {
      line.appendChild(document.createTextNode(" — "));
      /* Ops is where the audit trail is read, and it takes its filters from
         the address, so this link arrives with the action already chosen
         rather than on an unfiltered list. */
      const link = el("a", "obj", `see ${proposal.action} on Ops`);
      link.href = "/dashboard/ops?actor=AGENT&action="
                  + encodeURIComponent(proposal.action);
      line.appendChild(link);
    }
    if (proposal.audit_rows) {
      line.appendChild(document.createTextNode(
        ` (this turn wrote ${proposal.audit_rows} audit rows in all)`));
    }
    box.appendChild(line);
  } else if (outcome === "confirmed") {
    box.appendChild(el("p", "audited",
      "Done, but no audit row was found for it — which is a fact about this "
      + "write, not a blank."));
  }
  return box;
}

/* ---------- status ---------- */

/* Two brains, two endpoints, and the second must not go missing with the
   first.

   `/ai` is the *local* layer on this box - Ollama, the GPU, and the jobs the
   local model has. `/assist/agent/status` is the cloud brain this plant's
   assistant actually runs on, with its spend against its cap. Until
   2026-09-27 this tab read only the first, and the cloud brain's spend was a
   note inside one of the local rows: a plant with `MES_LOCAL_AI=0` got the
   empty payload and no number at all, losing the one figure a plant
   administrator most needs from this tab to a setting that has nothing to do
   with it.

   So both are read, side by side, and neither failing takes the other with
   it: a call that does not answer reports *unknown*, which is not zero. */
async function loadStatus() {
  const [local, cloud] = await Promise.all([
    api("/ai").catch((err) => ({ unreadable: err.message || String(err) })),
    api("/assist/agent/status").catch((err) => ({ unreadable: err.message || String(err) })),
  ]);

  const head = $("#status-head");
  head.textContent = "";
  const fact = (label, value) => {
    const box = el("div", "fact");
    box.appendChild(el("span", "k", label));
    box.appendChild(el("span", "v", value));
    head.appendChild(box);
  };

  /* The cloud brain first, and whatever the local layer is doing. */
  if (cloud.unreadable) {
    fact("Assistant brain", "unknown");
  } else {
    fact("Assistant brain",
         `${cloud.model || "model not reported"} — ${cloud.available ? "on" : "off"}`);
    fact("Spend this month", `${money(cloud.spend_usd, 2)} of ${money(cloud.cap_usd)}`);
  }

  if (local.unreadable) {
    fact("Local AI", "unknown");
  } else if (!local.enabled) {
    fact("Local AI", "off on this machine");
  } else {
    if (local.ollama.reachable) {
      fact("Model server", "up");
      fact("Loaded now",
           local.ollama.loaded.map((m) => m.model).join(", ") || "nothing loaded");
    } else {
      fact("Model server", `unreachable at ${local.ollama.url}`);
    }
    if (local.gpu) {
      fact("GPU memory", `${local.gpu.vram_used_mb} / ${local.gpu.vram_total_mb} MB`);
      fact("GPU", `${local.gpu.utilization_pct}% · ${local.gpu.temperature_c}°C`);
    }
  }

  $("#status-spend").textContent = cloud.unreadable
    ? `The cloud brain's status could not be read (${cloud.unreadable}), so whether `
      + "it is on and what it has spent are unknown — which is not the same as nothing."
    : `Last used ${cloud.last_used ? FS.fmt.stamp(cloud.last_used) : "never on this plant"}. `
      + "The dollars are an estimate at list prices, the same estimate the "
      + "conversations carry; the Console is the bill. The cap is this plant's "
      + "own and does not move with the local AI setting.";

  const body = $("#status-table tbody");
  body.textContent = "";
  for (const row of [cloudRow(cloud), ...localRows(local)]) {
    const tr = el("tr");
    tr.appendChild(el("td", null, row.name));
    tr.appendChild(el("td", "muted", row.trigger));
    tr.appendChild(el("td", null, row.last || "—"));
    tr.appendChild(el("td", "muted small", row.output));
    const state = el("td");
    /* `off` is a state, not an absence: a brain switched off is reported the
       way an idle one is, with the reason beside it. */
    const cls = { ok: "confirmed", idle: "declined", stale: "declined",
                  off: "declined", down: "failed" }[row.state] || "";
    state.appendChild(el("span", `pill ${cls}`, row.state));
    tr.appendChild(state);
    tr.appendChild(el("td", "muted small", row.note || ""));
    body.appendChild(tr);
  }

  $("#status-budget").textContent = local.enabled
    ? "GPU budget, in priority order: " + local.budget.join(" → ") + "."
    : "";
}

/* Money, as a plant writes it. A cap of $10 reads "$10" and a cap of $10.50
   reads "$10.50" - rounding it to the nearest dollar for tidiness would
   overstate somebody's budget by fifty cents, and a budget is a number the
   screen has no business rounding. Spend is always to the cent. */
function money(value, places) {
  const n = Number(value || 0).toFixed(places === undefined ? 2 : places);
  return "$" + (places === undefined ? n.replace(/\.00$/, "") : n);
}

/* The cloud brain's own row, from its own endpoint. It is here whatever the
   local AI setting says, because it is not part of the local layer. */
function cloudRow(cloud) {
  if (cloud.unreadable) {
    return { name: "Floor agent (cloud)",
             trigger: "the Assistant panel, on demand",
             last: null, output: "proposes, and once confirmed performs",
             state: "unknown",
             note: `its status could not be read: ${cloud.unreadable}` };
  }
  return {
    name: "Floor agent (cloud)",
    trigger: "the Assistant panel, on demand",
    last: cloud.last_used ? FS.fmt.stamp(cloud.last_used) : null,
    output: "proposes and, once confirmed, performs; every turn of it is in "
            + "the conversations beside this tab",
    state: cloud.available ? "ok" : "off",
    /* Off says why, in `available()`'s own words - "no ANTHROPIC_API_KEY in
       this plant's environment" is an answer somebody can act on, and a blank
       is not. */
    note: cloud.available
      ? `${cloud.model} is answering; the spend above is this month's`
      : cloud.reason,
  };
}

/* The local layer's jobs when it is on; one row saying off, and why, when it
   is not. A row that vanishes is the bug this tab had. */
function localRows(local) {
  if (local.unreadable) {
    return [{ name: "Local AI layer", trigger: "on this machine", last: null,
              output: "—", state: "unknown",
              note: `its status could not be read: ${local.unreadable}` }];
  }
  if (!local.enabled) {
    return [{
      name: "Local AI layer",
      trigger: "on this machine",
      last: null,
      output: "nothing: no local model is asked",
      state: "off",
      note: "switched off on this machine (MES_LOCAL_AI=0), so run triage, the "
            + "nightly rollup, design chat, instruction drafting and the night "
            + "shift do not run here. The cloud brain above is unaffected.",
    }];
  }
  return local.consumers;
}

/* ---------- settings ---------- */

/* The rows that gate the assistant. Each is a section on Setup › Configuration
   and is linked rather than copied: this screen says where the door is, the
   way a Configuration page does. */
const AGENT_ROWS = [
  ["agent_budget", "What one conversation may spend",
   "turns per message, how long it lives, how much of a tool result the model sees"],
  ["assistant_context", "How much of the plant the assistant is told",
   "and how long the local model is given to answer"],
  ["ai_rollup_stale", "When a daily AI artifact is called late",
   "the threshold behind the Status tab's “stale”"],
];

async function loadSettings() {
  const page = await api("/dashboard/config/administration/sections");
  const section = (page.items || []).find((s) => s.key === "ai_trace_days");
  const key = section && (section.pack_keys || [])[0];
  if (key) {
    $("#trace-days").value = key.value;
    $("#trace-now").textContent =
      `Now ${key.value} day(s), set by ${key.set_by || "the pack"}`
      + (key.set_at ? ` on ${FS.fmt.stamp(key.set_at)}.` : ".");
  } else {
    $("#trace-now").textContent =
      "This plant does not list a retention row — the trace is kept for as long "
      + "as the product's own default says.";
  }
  $("#trace-readonly").classList.toggle("hidden", FS.can("users.manage"));

  const list = $("#agent-settings");
  list.textContent = "";
  for (const [sectionKey, label, what] of AGENT_ROWS) {
    const item = el("li");
    const link = el("a", null, label);
    link.href = `/dashboard/config/administration#${sectionKey}`;
    item.appendChild(link);
    item.appendChild(el("span", "what", ` — ${what}`));
    list.appendChild(item);
  }
}

async function saveTraceDays() {
  clearBanner();
  const value = $("#trace-days").value.trim();
  await api("/dashboard/config/administration/settings/ai_trace_days",
            { method: "PATCH", body: { value } });
  FS.toast(`The trace is kept for ${value} day(s).`);
  await loadSettings();
}


/* ---------- Explore: the exploration, beside the conversation ----------

   Scott, 2026-09-29: "this doesn't go nearly deep enough … it should be in AI
   … access all data including the AI traces … graphs a network node analysis …
   then the agent, after graphing and analyzing those connections, should be
   able to graph and analyze any data internally relevant to that thread."

   `docs/design/deep-analysis.md` §2 is why it is here and not on the analysis
   page, and the sentence it turns on: *what makes a chart a dashboard is not
   where it is drawn, it is whether anybody asked it a question.* Everything in
   this panel was asked for by the person reading it, in words, in this
   conversation, and it lives as long as the conversation does. Nothing is drawn
   on this tab that nobody asked for - which is why the tab still opens on this
   panel empty, with an input and no picture.

   Two rules hold the whole thing up and both are the server's, not this file's:

   1. **The agent never computes a series.** It names a tool call it made, by
      the id of its own `tool_use` block, and the server attaches the envelope
      out of what the plant actually returned. This file draws that envelope
      with `FS.kit.chart`, which writes the total and the coverage before any
      shape draws anything. So a chart here cannot carry a figure the plant did
      not compute; there is no code path by which it could.
   2. **Expanding a node is a question, not a query.** Clicking a node on the
      graph does not reach around the agent into an endpoint: it puts a sentence
      to the agent, which reads, and answers, and re-draws - and the kit
      re-states the totals on every re-draw, because a filtered picture that
      kept the old total is a list that reads complete. */

const EXPLORE_KIND = "analysis";
let exploreSession = null;
let exploreBusy = false;
let exploreBrain = null;

/* Which of a payload's fields a shape plots. This is the one thing kit's
   `options` is for - naming the field, because a chart that picked one would be
   choosing what the reader is looking at - and it is deliberately the only
   mapping in this file. No number is touched: where a series has to be built,
   it is built out of the payload's own rows, unaltered, so every `data-value`
   on the picture is still a figure that came off the plant. */
const PLOTS = {
  trace_rollup: {
    bars: (e) => ({ rows: e.groups || [], labelKey: "key", valueKey: "turns",
                    noun: "question group", rowsTotal: e.groups_total,
                    title: "Questions asked, most asked first" }),
  },
  maintenance_mttr: {
    line: (e) => ({ series: [{ label: "mean repair minutes", points: e.buckets || [] }],
                    value: "mean", title: "Repair time over time" }),
  },
  downtime_pareto: {
    bars: () => ({ title: "Downtime by reason, worst first" }),
  },
  production_trend: {
    line: (e) => ({ series: [{ label: "good", points: e.points || [] }],
                    value: "good", title: "Good over the window" }),
  },
};

/* The envelope a shape is handed, and the options beside it. The envelope is
   the plant's own payload; where a shape needs a series the payload keeps under
   another name, the rows themselves are carried across rather than copied into
   new numbers. */
function plotFor(chart) {
  const build = (PLOTS[chart.tool] || {})[chart.shape];
  const options = build ? build(chart.envelope) : {};
  const envelope = options.series
    ? { ...chart.envelope, series: options.series }
    : chart.envelope;
  const { series, ...rest } = options;
  return { envelope, options: { title: chart.title || rest.title, ...rest } };
}

function exploreLog() {
  return $("#explore-log");
}

function exploreSay(text, cls) {
  const line = el("div", `explore-msg ${cls}`, text);
  exploreLog().appendChild(line);
  line.scrollIntoView({ block: "nearest" });
  return line;
}

/* What the reply cost, in the words the rest of this screen uses: an estimate
   at list prices, with the Console as the bill. Three numbers because they
   answer three different questions - what that answer cost, what this
   exploration has spent of what one may spend, and where the month stands. */
function exploreCost(cost) {
  if (!cost) return;
  const conversation = cost.conversation_cap_usd
    ? `$${cost.conversation_usd.toFixed(4)} of $${cost.conversation_cap_usd.toFixed(2)} `
      + "this exploration may spend"
    : `$${cost.conversation_usd.toFixed(4)} this exploration`;
  $("#explore-cost").textContent =
    `That answer cost about $${cost.turn_usd.toFixed(4)}. ${conversation}; `
    + `$${cost.month_usd.toFixed(2)} of $${cost.month_cap_usd.toFixed(2)} this month. `
    + "Estimates at list prices — the Console is the bill.";
}

/* One chart, as the person sees it: the kit's own SVG, and a way to take it
   away with its footer on. A payload the shape cannot draw says so in the
   kit's own words rather than drawing something else. */
function drawChart(chart) {
  const box = el("figure", "explore-chart");
  box.dataset.tool = chart.tool;
  box.dataset.shape = chart.shape;
  const head = el("figcaption");
  head.appendChild(el("span", "chart-title", chart.title || chart.tool));
  head.appendChild(el("span", "muted small",
    ` — drawn from ${chart.tool}, which this plant computed`));
  box.appendChild(head);
  const host = el("div", "chart-host");
  box.appendChild(host);
  exploreLog().appendChild(box);

  let node = null;
  try {
    const plot = plotFor(chart);
    node = FS.kit.draw(host, chart.shape, plot.envelope, plot.options);
  } catch (err) {
    /* The kit refuses a payload it cannot draw honestly, by name. That refusal
       is the answer here: the sentence beside it already carried the numbers,
       and a second-choice shape would be this file deciding what the reader is
       looking at. */
    host.appendChild(el("p", "empty",
      `This answer could not be drawn as a ${chart.shape}: ${err.message}`));
    return box;
  }

  const tools = el("div", "chart-tools");
  for (const format of ["svg", "png"]) {
    const button = el("button", "ghost small", format.toUpperCase());
    button.dataset.export = format;
    button.setAttribute("aria-label",
      `Export “${chart.title || chart.tool}” as ${format.toUpperCase()}`);
    button.addEventListener("click", () => exportChart(node, chart, format));
    tools.appendChild(button);
  }
  box.appendChild(tools);

  /* Rule 2 of this panel: a node is a question. The kit says which node was
     asked for and draws nothing new itself; the tab puts the sentence to the
     agent, and the agent reads. Nothing here reaches around it into an
     endpoint - an answer this screen assembled would be an answer with no
     trace row and no coverage behind it. */
  node.addEventListener("fs-chart-expand", (event) => {
    const it = event.detail || {};
    const kind = String(it.kind || "node").replace(/_/g, " ");
    ask(`Expand the ${kind} “${it.label || it.id}” (${it.id}) into the records `
        + "behind it, and re-state the totals.").catch(fail);
  });
  return box;
}

/* The chart off the page, with its footer on it. `FS.kit.export` is the one
   implementation (deep-analysis §3): the same node the reader is looking at,
   its colours resolved, its coverage sentence still in the file. */
async function exportChart(node, chart, format) {
  try {
    const blob = await FS.kit.export(node, format);
    const name = `${(chart.title || chart.tool).toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "chart"}.${format}`;
    const url = URL.createObjectURL(blob);
    const link = el("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    FS.toast(`Saved ${name}, footer and all.`);
  } catch (err) {
    fail(err);
  }
}

function drawExploreTrace(rows) {
  if (!rows || !rows.length) return;
  const details = el("details", "explore-trace");
  const names = [...new Set(rows.map((r) => r.tool))];
  details.appendChild(el("summary", null,
    `${rows.length} tool call${rows.length === 1 ? "" : "s"}: ${names.join(", ")}`));
  for (const row of rows) {
    const line = el("div", `trace-row ${row.ok ? "ok" : "bad"}`);
    const args = Object.entries(row.args || {}).map(([k, v]) => `${k}=${v}`).join(" ");
    line.appendChild(el("code", null, `${row.tool} ${args}`.trim()));
    line.appendChild(el("span", null, row.summary || ""));
    details.appendChild(line);
  }
  exploreLog().appendChild(details);
}

function renderExplore(out) {
  exploreSession = out.session || exploreSession;
  drawExploreTrace(out.transcript);
  for (const chart of out.charts || []) drawChart(chart);
  if (out.say) exploreSay(out.say, "bot");
  exploreCost(out.cost);
  if (out.kind === "unavailable") {
    /* Off, or out of budget. Both are states with a reason, and the reason is
       what a person can act on; neither is an error and neither is a blank. */
    $("#explore-state").textContent = out.why
      ? `The analysis agent is not answering: ${out.why}.`
      : "The analysis agent is not answering.";
  }
  exploreLog().lastElementChild?.scrollIntoView({ block: "nearest" });
}

async function ask(question) {
  if (exploreBusy || !question) return;
  clearBanner();
  exploreSay(question, "me");
  const pending = exploreSay("working…", "thinking");
  exploreBusy = true;
  $("#explore-send").disabled = true;
  try {
    const out = await api("/assist/agent", {
      method: "POST",
      body: { message: question, session: exploreSession,
              screen: window.location.pathname, kind: EXPLORE_KIND },
    });
    pending.remove();
    renderExplore(out);
  } catch (err) {
    pending.remove();
    exploreSay("I could not reach the analysis agent just then. Nothing was "
               + "changed — it only reads.", "bot");
    fail(err);
  } finally {
    exploreBusy = false;
    $("#explore-send").disabled = false;
  }
}

/* Whether the agent is on, and why it is not. Shadow mode is the one that has
   to be said in full: a shadow plant's numbers do not leave the box, so the
   cloud brain is refused there, and an exploration that quietly answered from
   somewhere else would be the whole point of shadow mode undone. */
async function loadExplore() {
  const status = await api(`/assist/agent/status?kind=${EXPLORE_KIND}`)
    .catch((err) => ({ unreadable: err.message || String(err) }));
  exploreBrain = status;
  const line = $("#explore-state");
  if (status.unreadable) {
    line.textContent = `Whether the analysis agent is on could not be read (${status.unreadable}), `
      + "which is not the same as it being off.";
  } else if (status.available) {
    const kind = (status.kinds || {})[EXPLORE_KIND] || {};
    line.textContent = `${status.model} is answering, as the ${kind.account} account, `
      + `whose ${kind.role} role holds every read in this plant and nothing that writes.`;
  } else {
    line.textContent = `The analysis agent is off: ${status.reason} `
      + "Explore is here either way — this is a state with a reason, not a missing screen.";
  }
  $("#explore-input").disabled = false;
  $("#explore-send").disabled = false;
}

function newExploration() {
  exploreSession = null;
  exploreLog().textContent = "";
  $("#explore-cost").textContent = "";
  FS.toast("Fresh exploration. The one before it is in the trace beside this tab.");
}

/* ---------- My agent: what the analysis could say about me ----------

   Decision 0039 clause 4. Behind `plant.read` and scoped to the signed-in
   account by the route itself - there is no way to ask it about anybody else -
   so an operator who may not read the trace can still read their own. */

async function loadMine() {
  const page = await api("/ai/me");
  const who = await FS.whoami().catch(() => null);
  $("#mine-who").textContent = who ? `— ${who.code} (${who.role})` : `— ${page.person}`;

  const facts = $("#mine-facts");
  facts.textContent = "";
  const fact = (label, value) => {
    const box = el("div", "fact");
    box.appendChild(el("span", "k", label));
    box.appendChild(el("span", "v", value));
    facts.appendChild(box);
  };
  const conversations = page.conversations || {};
  const turns = page.turns || {};
  fact("Your conversations", `${conversations.showing} of ${conversations.total}`);
  fact("Your turns", `${turns.showing} of ${turns.total}`);
  fact("Times an analysis named you", String(page.named_in_total));
  $("#mine-note").textContent =
    (page.kept_days > 0
      ? `This plant keeps ${page.kept_days} day(s) of trace, so what is here is what it has; `
      : "This plant keeps every turn, so this is all of them; ")
    + "older turns are deleted as new ones are written, and a deleted turn is "
    + "gone from this page too.";

  const named = page.named_in || [];
  $("#named-count").textContent = `— ${named.length}`;
  const namedBody = $("#named-table tbody");
  namedBody.textContent = "";
  for (const row of named) {
    const tr = el("tr");
    tr.appendChild(el("td", "mono", FS.fmt.stamp(row.ts)));
    tr.appendChild(el("td", null, row.by));
    tr.appendChild(el("td", "muted small", row.everybody
      ? `every account, grouped by ${row.grouped_by || "role"}`
      : `you by name, grouped by ${row.grouped_by || "role"}`));
    namedBody.appendChild(tr);
  }
  const noneNamed = $("#named-empty");
  noneNamed.classList.toggle("hidden", named.length > 0);
  noneNamed.textContent =
    "No analysis on this plant has named you. That is a reading of the audit "
    + "trail, not an empty page: naming a person needs `people.analyse`, which "
    + "no role here holds unless somebody granted it.";

  const rows = conversations.conversations || [];
  $("#mine-count").textContent = `— showing ${conversations.showing} of ${conversations.total}`;
  const body = $("#mine-table tbody");
  body.textContent = "";
  for (const row of rows) {
    const tr = el("tr");
    tr.appendChild(el("td", "mono", FS.fmt.stamp(row.started)));
    tr.appendChild(el("td", null, row.brain));
    tr.appendChild(el("td", row.screen ? "mono" : "muted", screenText(row)));
    tr.appendChild(el("td", "opened", row.opened_with));
    tr.appendChild(el("td", "mono", String(row.turns)));
    tr.appendChild(el("td", "mono", String(row.errors || 0)));
    tr.appendChild(el("td", "mono", `$${row.usd.toFixed(4)}`));
    body.appendChild(tr);
  }
  const empty = $("#mine-empty");
  empty.classList.toggle("hidden", rows.length > 0);
  empty.textContent = "You have not talked to this plant's AI in the window it keeps.";

  const rollup = page.rollup;
  const groupsBody = $("#mine-groups tbody");
  groupsBody.textContent = "";
  if (!rollup) {
    $("#mine-groups-count").textContent = "— unknown";
    $("#mine-groups-note").textContent =
      `The rollup could not be read: ${page.rollup_unreadable}. That is a fact `
      + "about this plant's calendar, not an empty answer about you.";
    return;
  }
  const groups = rollup.groups || [];
  $("#mine-groups-count").textContent =
    `— showing ${groups.length} of ${rollup.groups_total}`;
  for (const group of groups) {
    const tr = el("tr");
    tr.appendChild(el("td", null, group.asked || group.key));
    tr.appendChild(el("td", "mono", String(group.turns)));
    tr.appendChild(el("td", "mono", String(group.sessions)));
    tr.appendChild(el("td", "mono", FS.fmt.stamp(group.first_seen)));
    tr.appendChild(el("td", "mono", FS.fmt.stamp(group.last_seen)));
    groupsBody.appendChild(tr);
  }
  $("#mine-groups-note").textContent =
    `${rollup.turns_total} turn(s) of yours in this window, in `
    + `${rollup.groups_total} group(s), ${rollup.groups_of_one} of them asked once. `
    + `${rollup.wordless_turns} turn(s) carried no words at all — pressing a button `
    + "is a turn nobody typed. Grouping is a judgment and the words are yours: "
    + `${rollup.normalisation}`;
}

/* ---------- tabs ---------- */

const LOADERS = {
  explore: loadExplore,
  conversations: loadConversations,
  status: loadStatus,
  mine: loadMine,
  settings: loadSettings,
};

function onTab(name) {
  clearBanner();
  (LOADERS[name] || LOADERS[firstTab()])().catch(fail);
}

/* Two gates on this page, not one.

   The trace, the status of the brains and the exploration are all `audit.read`:
   an exploration reads what other people typed, and the gate on the trace has
   to be the gate on the analysis of it, or the analysis would be the way round
   the gate.

   `My agent` is `plant.read`, which every role holds, and it is scoped to the
   signed-in account by the route rather than by this file. That is decision
   0039 clause 4: an operator who may not read anybody's conversations can still
   read their own, and can see every time an analysis named them. A reciprocity
   clause behind a capability no operator holds would have promised nothing. */
function myTabs() {
  return FS.can("audit.read")
    ? ["explore", "conversations", "status", "mine", "settings"]
    : (FS.can("plant.read") ? ["mine"] : []);
}

function firstTab() {
  return myTabs()[0] || "conversations";
}

(async function boot() {
  const who = await FS.whoami().catch(() => null);
  const mine = myTabs();
  const allowed = mine.length > 0;
  const whole = FS.can("audit.read");
  $("#denied").classList.toggle("hidden", whole);
  $("#ai-tabs").classList.toggle("hidden", !allowed);
  $("#ai-main").classList.toggle("hidden", !allowed);
  $("#denied-who").textContent = who ? `${who.code} (${who.role})` : "nobody";
  $("#denied-mine").classList.toggle("hidden", !allowed);
  if (!allowed) return;
  /* The tabs and the panels somebody may not open are removed rather than
     disabled: a tab that is there and answers 403 is a screen telling somebody
     to try, and a panel left in the document is a panel a script can still
     read out of. */
  for (const tab of document.querySelectorAll("#ai-tabs .tab[data-tab]")) {
    if (!mine.includes(tab.dataset.tab)) tab.remove();
  }
  for (const panel of document.querySelectorAll(".tab-panel[data-panel]")) {
    if (!mine.includes(panel.dataset.panel)) panel.remove();
  }
  FS.applyCapGates();
  if (whole) {
    $("#conv-q").addEventListener("input", drawConversations);
    $("#conv-since").addEventListener("change",
      () => loadConversations().catch(fail));
    $("#trace-save").addEventListener("click", () => saveTraceDays().catch(fail));
    $("#explore-send").addEventListener("click", () => sendExplore());
    $("#explore-new").addEventListener("click", newExploration);
    $("#explore-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter") sendExplore();
    });
  }
  FS.tabs.init(document, onTab);
})().catch(fail);

function sendExplore() {
  const box = $("#explore-input");
  const question = box.value.trim();
  if (!question) return;
  box.value = "";
  ask(question).catch(fail);
}
