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
  /* The control chart, and the one entry here that names no field. An
     `spc_chart` payload already IS a control chart: the shape reads `points`,
     `control`, `signals`, `kind` and both specification limits off it by name,
     so there is nothing to choose and nothing this file could get wrong. The
     entry exists for the title - a picture the model titled nothing should
     still say which characteristic it is of - and because this map is the list
     of what the explore screen can draw, and a control chart is now on it. */
  spc_chart: {
    spc: (e) => ({ title: `${e.material || ""} ${e.characteristic || ""}`.trim()
                          || "Control chart" }),
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

/* ---------- the model's words, as markdown ----------

   Claude answers in markdown whether or not anybody asked it to, so until
   this the verdict arrived on the screen as `**out of control, not out of
   spec**` - asterisks and all - and a list of three findings arrived as three
   lines beginning with a hyphen. Two ways out: ask the model for plain text,
   or render the little of markdown it uses. This is the second, because the
   emphasis is doing work - it is on the verdict and on the rule that fired -
   and because a prompt that forbids markdown is a prompt that has to keep
   forbidding it.

   Minimal, and built out of nodes rather than markup: bold, italics, inline
   code, bullet and numbered lists, paragraphs, and a heading drawn as a bold
   line. No links, no images, no HTML - nothing in here ever parses markup, so
   a model that wrote `<script>` wrote nine characters of text and this screen
   shows nine characters of text. Anything this does not understand stays
   exactly the characters the model sent: unrendered is a cosmetic failure and
   mangled is not. */

/* One capture group holding every inline form, so `split` hands back the
   pieces and the markers alternately. */
const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|_[^_\n]+_|`[^`\n]+`)/;
const BULLET = /^\s*[-*\u2022]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;

function inlineInto(host, text) {
  for (const piece of String(text).split(INLINE)) {
    if (!piece) continue;
    const marker = piece.slice(0, 2);
    if (marker === "**" || marker === "__") {
      host.appendChild(el("strong", null, piece.slice(2, -2)));
    } else if (piece.startsWith("`") && piece.length > 2) {
      host.appendChild(el("code", null, piece.slice(1, -1)));
    } else if ((piece.startsWith("*") || piece.startsWith("_")) && piece.length > 2) {
      host.appendChild(el("em", null, piece.slice(1, -1)));
    } else {
      host.appendChild(document.createTextNode(piece));
    }
  }
}

function markdownInto(host, text) {
  /* The two things a line can be continuing: a list of the same sort, or a
     paragraph soft-wrapped across lines. A blank line ends both. */
  let list = null;
  let para = null;
  for (const raw of String(text).split("\n")) {
    if (!raw.trim()) { list = null; para = null; continue; }
    const bullet = BULLET.exec(raw);
    const numbered = bullet ? null : NUMBERED.exec(raw);
    if (bullet || numbered) {
      para = null;
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tagName.toLowerCase() !== tag) {
        list = el(tag, "explore-list");
        host.appendChild(list);
      }
      const item = el("li");
      inlineInto(item, (bullet || numbered)[1]);
      list.appendChild(item);
      continue;
    }
    list = null;
    const heading = HEADING.exec(raw);
    if (heading) {
      para = null;
      const head = el("p", "explore-head");
      inlineInto(head, heading[1]);
      host.appendChild(head);
      continue;
    }
    if (para) inlineInto(para, ` ${raw.trim()}`);
    else {
      para = el("p");
      inlineInto(para, raw.trim());
      host.appendChild(para);
    }
  }
}

function exploreSay(text, cls) {
  const line = el("div", `explore-msg ${cls}`);
  /* The person's own question and "working…" are this screen's own words and
     go in as they are. Only the model's reply is markdown. */
  if (cls === "bot") markdownInto(line, text);
  else line.textContent = text;
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

/* The sentence a dot on a control chart adds to the question box.

   Scott, 2026-10-08: "I was wanting to ask about a specific data point, but
   thought it was hard to type what would have been easy to click." So the
   click writes the words for him - and writes the point's ID into them, not
   only its time, because `spc_sample` and `spc_point` are asked by id and a
   model handed a clock time would have to guess which point that was.

   It is TYPED and not sent, for the same reason the chip's question is: the
   next turn costs money and the person decides to spend it. An empty box gets
   a whole question, because "- and the one at 06:52" on its own is not one. */
function alsoAsk(detail) {
  const box = $("#explore-input");
  if (!box || detail.id === undefined || detail.id === null) return;
  const noun = detail.what === "sample" ? "sample" : "reading";
  const named = `(${noun} ${detail.id})`;
  /* Pressed twice, the same dot says nothing new. */
  if (box.value.includes(named)) return;
  const when = detail.at ? FS.fmt.stamp(detail.at) : null;
  const here = when ? `the one at ${when} ${named}` : `${noun} ${detail.id}`;
  const had = box.value.trim();
  box.value = had
    ? `${had} — and ${here}?`
    : `Why is ${here} where it is?`;
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);
}

/* One chart, as the person sees it: the kit's own SVG, and a way to take it
   away with its footer on. A payload the shape cannot draw says so in the
   kit's own words rather than drawing something else.

   A control chart here is pressable, which no other shape on this tab is
   (#152). The dots of an `spc` chart dispatch `fs-spc-open`, as they do on the
   SPC screen, and what opening one MEANS here is a question: the point goes
   into the box above, ringed on the picture so the reader can see which one
   the sentence is about. Nothing is fetched and nothing is computed by the
   press - rule 2 of this panel - and the ring is the kit's own
   `options.selected`, so the chart is redrawn by the one implementation rather
   than reached into.

   `handle` is how a chart this page may draw AGAIN keeps its place: an object
   the caller owns, holding the figure that was drawn and the point currently
   ringed on it. Pass one and the new figure replaces the old one where it
   stands and inherits its ring; pass nothing and the chart is appended, which
   is what every chart in an answer does. The figure is replaced rather than
   emptied and refilled so that no listener and no closure outlives the
   picture it was about. */
function drawChart(chart, handle) {
  const box = el("figure", "explore-chart");
  box.dataset.tool = chart.tool;
  box.dataset.shape = chart.shape;
  const head = el("figcaption");
  head.appendChild(el("span", "chart-title", chart.title || chart.tool));
  head.appendChild(el("span", "muted small",
    chart.note || ` — drawn from ${chart.tool}, which this plant computed`));
  box.appendChild(head);
  const host = el("div", "chart-host");
  box.appendChild(host);
  const standing = handle && handle.figure && handle.figure.parentNode
    ? handle.figure : null;
  if (standing) {
    /* Same picture, same place in the conversation, and whatever the page had
       marked it with. */
    for (const name of standing.classList) box.classList.add(name);
    standing.replaceWith(box);
  } else {
    exploreLog().appendChild(box);
  }

  const pressable = chart.shape === "spc";
  let open = chart.selected || (handle && handle.selected) || {};
  if (handle) {
    handle.figure = box;
    handle.selected = open;
  }
  const render = () => {
    const plot = plotFor(chart);
    return FS.kit.draw(host, chart.shape, plot.envelope,
                       pressable
                         ? { ...plot.options, openable: true, selected: open }
                         : plot.options);
  };

  let node = null;
  try {
    node = render();
  } catch (err) {
    /* The kit refuses a payload it cannot draw honestly, by name. That refusal
       is the answer here: the sentence beside it already carried the numbers,
       and a second-choice shape would be this file deciding what the reader is
       looking at. */
    host.appendChild(el("p", "empty",
      `This answer could not be drawn as a ${chart.shape}: ${err.message}`));
    return box;
  }

  /* On the figure and not on the SVG: the picture is replaced every time the
     ring moves, and a listener on the node that drew it would go with it. */
  if (pressable) {
    box.addEventListener("fs-spc-open", (event) => {
      const it = event.detail || {};
      if (it.id === undefined || it.id === null) return;
      /* Two keys and not one, as on the SPC screen: a sample id and a check id
         are both small integers, and one field holding either would ring the
         dot numbered 7 on the wrong half. */
      open = it.what === "sample" ? { sample: it.id } : { check: it.id };
      if (handle) handle.selected = open;
      node = render();
      alsoAsk(it);
    });
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

/* ---------- what to ask next ----------

   Scott, 2026-10-08: "could the agent extract potential new paths of research
   and make them buttons? … its flat now." An answer that ends in prose ends;
   an answer that ends in the questions it opened keeps going, and the person
   presses rather than types.

   The questions arrive as the LAST thing the model writes: a fenced block
   whose info string is `next`, one question per line (`ANALYSIS_NEXT` in
   `services/agent.py` is the paragraph that asks for it). A fenced block and
   not a tool call, deliberately - a tool call would be another round trip of
   the whole conversation through the model, which is the cost this handoff
   came to cut, and the words are the model's either way.

   What is not in here is a decision: every line is a QUESTION put back to the
   analysis agent, which holds no tool that changes anything. A button that
   booked, adjusted or approved would be this tab growing a floor agent, and
   the system words forbid writing one.

   Unparsed is cosmetic and mangled is not, as with the markdown above: a reply
   with no block gets no buttons, and the block's own text never reaches the
   screen as a fence because it is cut out of the words before they are
   rendered. The trace beside this tab still shows what the model wrote, fence
   and all, because that is the record. */

/* Opened by a fence of three or more backticks with `next` (or `next:`) after
   it, closed by the next fence or by the end of the answer - a model that
   stopped mid-block still gets its questions read. */
const NEXT_BLOCK = /\n*```+[ \t]*next:?[ \t]*\n([\s\S]*?)(?:\n?```+|$)/i;
/* Three is what the system words ask for. A model that wrote ten gets its
   first three rather than a screen full of buttons: the cap is this file's,
   because the screen is this file's. */
const NEXT_MOST = 3;

function splitNext(text) {
  const words = String(text || "");
  const found = NEXT_BLOCK.exec(words);
  if (!found) return { say: words, next: [] };
  const next = found[1].split("\n")
    /* A model asked for lines sometimes writes a list of them. */
    .map((line) => line.replace(/^\s*(?:[-*\u2022]|\d+[.)])\s*/, "").trim())
    .filter((line) => line.length > 0)
    .slice(0, NEXT_MOST);
  return { say: words.slice(0, found.index) + words.slice(found.index + found[0].length),
           next };
}

function drawNextQuestions(questions) {
  if (!questions.length) return null;
  const box = el("div", "explore-next");
  box.dataset.next = String(questions.length);
  /* Scott, 2026-10-08, of the live pictures: "I didn't see the buttons." So
     the row carries its own heading and the buttons the weight of the Ask
     button - this is the next step, and the next step should not need
     looking for. The questions are still the agent's own words. */
  box.appendChild(el("p", "next-head", "Ask this next"));
  box.appendChild(el("p", "muted small",
                     "The agent's own questions, from what it just read — press one:"));
  const row = el("div", "next-row");
  for (const question of questions) {
    const button = el("button", "small", question);
    button.type = "button";
    button.addEventListener("click", () => {
      if (exploreBusy) return;
      /* Pressed once and then spent: it is in the conversation above as the
         question it became, and a button that can be pressed twice is a button
         that can spend the cap twice by accident. The other two stay. */
      button.disabled = true;
      ask(question).catch(fail);
    });
    row.appendChild(button);
  }
  box.appendChild(row);
  exploreLog().appendChild(box);
  return box;
}

/* The answer on the screen, in the order the model wrote it.

   `parts` is the server's account of that order - `{text}` for a round's words
   and `{chart}` naming one of `charts` by its id. It exists because a round
   that ended in a tool call used to lose its words: the model said the good
   part, read one more record, and only the last round reached the screen
   (`services/agent.py`, `turn_parts`). A reply without `parts` is read the way
   every reply was read before it: the charts, then the words. */
function partsOf(out) {
  if (out.parts && out.parts.length) return out.parts;
  return [...(out.charts || []).map((chart) => ({ chart: chart.id })),
          { text: out.say }];
}

function renderExplore(out) {
  exploreSession = out.session || exploreSession;
  drawExploreTrace(out.transcript);
  const byId = new Map();
  (out.charts || []).forEach((chart, i) => byId.set(chart.id ?? `c${i + 1}`, chart));
  let next = [];
  for (const part of partsOf(out)) {
    if (part.chart !== undefined && part.chart !== null) {
      const chart = byId.get(part.chart);
      if (chart) showChart(chart);
      continue;
    }
    /* Per part, because the fenced block of questions is the last thing the
       model writes and "last" is in whichever part it wrote last. */
    const split = splitNext(part.text || "");
    if (split.next.length) next = split.next;
    if (split.say.trim()) exploreSay(split.say, "bot");
  }
  const asked = drawNextQuestions(next);
  exploreCost(out.cost);
  if (out.kind === "unavailable") {
    /* Off, or out of budget. Both are states with a reason, and the reason is
       what a person can act on; neither is an error and neither is a blank. */
    $("#explore-state").textContent = out.why
      ? `The analysis agent is not answering: ${out.why}.`
      : "The analysis agent is not answering.";
  }
  /* The end of the answer, which is the buttons when there are any: a next
     step below the fold is a next step nobody takes. */
  (asked || exploreLog().lastElementChild)?.scrollIntoView(
    { block: asked ? "end" : "nearest" });
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
  await pinChart();
}

/* ---------- the chart somebody arrived with ----------

   The SPC screen's *Explain this chart* chip lands here carrying names: `spec`
   is the material and the characteristic, and `sample` or `check` is whichever
   point had a panel open beside it. This draws that chart at the top of the
   conversation BEFORE the first question is asked, with that point ringed, so
   the picture he was looking at is the picture he is talking about - and the
   dots on it are pressable, so the next point is a click rather than a
   sentence he has to compose.

   It reads the plant itself (#150): no payload travels in the address and
   nothing is scraped off the screen he came from, so the chart is the plant's
   own answer, as it is now, and the sample ids on it are ones `spc_sample` can
   be asked about. The agent is not called and nothing is spent - a chart drawn
   from a read the browser is already allowed to do is not a turn.

   Once. `loadExplore` runs every time somebody comes back to this tab, and a
   chart pinned again on the way back would be the same picture twice in a
   conversation that had moved on. */
let pinnedChart = false;

/* The pinned chart's handle - the figure it was drawn into, the point ringed on
   it, what picture it IS and how far back its window reaches, so the answer
   that draws the same one again can mark this one instead of stacking a second
   copy beside it (`samePicture`).
   Null until something is pinned, which is most conversations. */
let pinned = null;

/* What makes two control charts the same picture: the same tool, the same
   specification - the material and the characteristic - and the same chart
   kind. The window is matched by its RULE, not by the readings that happen to
   be in it.

   Both windows are *the last N of that characteristic, ending at the newest
   one*: the chip's chart asks this plant for its own `[quality] spc_history`
   and the agent's `spc_chart` asks for its own `limit`. So a window holding at
   least as many points as the pinned one reaches back at least as far and ends
   at the same end - it IS the pinned window, slid forward by the readings that
   arrived while the model was thinking, or grown by them. A window holding
   FEWER points is a shorter stretch somebody asked for on purpose, and that is
   a different picture, drawn where the answer wrote it.

   The reading ids at both ends used to be in this key (#155), and that is the
   bug this replaces. Seen on the lab 2026-10-08 18:52: the filler makes a
   reading every eight seconds and the model takes about twenty to answer, so
   the agent's window was always a reading later at each end than the pinned
   one (6:24:55-6:51:57 against 6:25:03-6:52:05) and the match never fired on
   any plant that was running. #155's proof had been taken on a plant that had
   stopped producing, where those ids stand still. */
function samePicture(was, chart) {
  if (!was || !chart || was.tool !== chart.tool) return false;
  const key = pictureKey(chart.envelope);
  return key !== null && key === was.key && pointCount(chart.envelope) >= was.points;
}

/* Which picture this is: the characteristic, and which of the two charts of it.
   No reading id and no point count - both move on a plant that is running. */
function pictureKey(envelope) {
  if (!pointCount(envelope)) return null;
  return [envelope.material, envelope.characteristic, envelope.kind].join("|");
}

/* How far back a window reaches, in its own points, which is the only account
   of the window either envelope carries. An envelope with no points is not a
   picture and matches nothing, including another envelope with no points. */
function pointCount(envelope) {
  return envelope && Array.isArray(envelope.points) ? envelope.points.length : 0;
}

/* One chart on the screen for one picture.

   Scott, 2026-10-08, from the live run: the manager question put the same
   control chart on the page twice - once pinned by the chip he pressed, and
   once again because the agent drew it as part of its answer. Two identical
   pictures is a reader asking what the difference between them is.

   So the pinned one is MARKED as the answer's chart rather than moved or
   duplicated: it is first in the conversation because it was there first, and
   it is re-drawn from the envelope the agent was actually handed, keeping the
   ring on the dot being asked about. A chart that is not the pinned one is
   drawn where the answer wrote it. */
function showChart(chart) {
  if (pinned && samePicture(pinned, chart)) {
    const figure = drawChart(
      { ...chart,
        note: " — the chart you came from, and the one this answer is about, "
              + "drawn from the readings the agent itself was handed, so any "
              + "that arrived since you pressed the chip are on it. The agent "
              + "drew the same picture, so it is not drawn twice. Press a dot "
              + "to ask about that point." },
      pinned);
    /* Which of the answer's charts this figure stands in for, so the screen
       says in its own markup what the note says in words. */
    figure.dataset.answerChart = chart.id || "";
    return figure;
  }
  return drawChart(chart);
}

async function pinChart() {
  if (pinnedChart) return;
  const query = new URLSearchParams(window.location.search);
  const spec = query.get("spec");
  /* The last slash: a characteristic is one of this plant's own words and a
     material code is whatever a customer's ERP calls it. */
  const cut = spec ? spec.lastIndexOf("/") : -1;
  if (cut <= 0 || cut === spec.length - 1) return;
  pinnedChart = true;
  const material = spec.slice(0, cut);
  const characteristic = spec.slice(cut + 1);
  const number = (name) => {
    const raw = query.get(name);
    const value = raw === null ? NaN : Number(raw);
    return Number.isFinite(value) ? value : undefined;
  };
  try {
    const data = await api(`/quality/spc/${encodeURIComponent(material)}/${encodeURIComponent(characteristic)}`);
    /* `points` is how far this window reaches back, kept beside the key so the
       answer's own window can be compared against it rather than against the
       ids of the readings that were in it at this moment. */
    pinned = { tool: "spc_chart", key: pictureKey(data), points: pointCount(data),
               figure: null, selected: null };
    const figure = drawChart({
      tool: "spc_chart", shape: "spc", envelope: data,
      title: `${data.material || material} ${data.characteristic || characteristic}`.trim(),
      note: " — the chart you came from, read from this plant just now. "
            + "Press a dot to ask about that point.",
      selected: { sample: number("sample"), check: number("check") },
    }, pinned);
    figure.classList.add("explore-pinned");
  } catch (err) {
    /* Said, not swallowed and not thrown: the question is already typed in the
       box and the agent can still answer it without the picture. */
    exploreSay(`The ${material} ${characteristic} chart could not be read just `
               + `then (${err.message}), so it is not drawn above. The question `
               + "below still works — the agent reads the chart itself.", "bot");
  }
}

function newExploration() {
  exploreSession = null;
  /* The log is emptied, so the figure the handle points at is gone with it. */
  pinned = null;
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
  conversations: loadConversations,
  explore: loadExplore,
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
    ? ["conversations", "explore", "status", "mine", "settings"]
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
    prefill();
  }
  FS.tabs.init(document, onTab);
})().catch(fail);

/* A question another screen handed over. The SPC tab's *Explain this chart*
   lands here as `/dashboard/ai?ask=...#explore`: the hash is the tab, which
   `FS.tabs` reads, and the query is the sentence.

   It is TYPED, not sent. The person reads it, changes it if it is not quite
   their question, and presses Ask. Sending it for them would spend a budget
   somebody else's button had decided to spend, and the question is a guess at
   what they wanted to know - the chart they were looking at, and the sample
   they had open. The guess is worth making; acting on it is not. */
function prefill() {
  const asked = new URLSearchParams(window.location.search).get("ask");
  const box = $("#explore-input");
  if (!asked || !box) return;
  box.value = asked;
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);
}

function sendExplore() {
  const box = $("#explore-input");
  const question = box.value.trim();
  if (!question) return;
  box.value = "";
  ask(question).catch(fail);
}
