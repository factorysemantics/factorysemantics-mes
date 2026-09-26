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
  return [row.person, row.brain, row.opened_with, row.session, row.model]
    .join(" ").toLowerCase().includes(q);
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

async function loadStatus() {
  const out = await api("/ai");
  const off = $("#status-off");
  if (!out.enabled) {
    $("#status-head").textContent = "";
    $("#status-table tbody").textContent = "";
    $("#status-budget").textContent = "";
    off.textContent = "The local AI layer is switched off on this machine "
      + "(MES_LOCAL_AI=0). The cloud brain, if this plant has a key, answers "
      + "the assistant panel regardless.";
    off.classList.remove("hidden");
    return;
  }
  off.classList.add("hidden");

  const head = $("#status-head");
  head.textContent = "";
  const fact = (label, value) => {
    const box = el("div", "fact");
    box.appendChild(el("span", "k", label));
    box.appendChild(el("span", "v", value));
    head.appendChild(box);
  };
  if (out.ollama.reachable) {
    fact("Model server", "up");
    fact("Loaded now",
         out.ollama.loaded.map((m) => m.model).join(", ") || "nothing loaded");
  } else {
    fact("Model server", `unreachable at ${out.ollama.url}`);
  }
  if (out.gpu) {
    fact("GPU memory", `${out.gpu.vram_used_mb} / ${out.gpu.vram_total_mb} MB`);
    fact("GPU", `${out.gpu.utilization_pct}% · ${out.gpu.temperature_c}°C`);
  }

  const body = $("#status-table tbody");
  body.textContent = "";
  for (const row of out.consumers) {
    const tr = el("tr");
    tr.appendChild(el("td", null, row.name));
    tr.appendChild(el("td", "muted", row.trigger));
    tr.appendChild(el("td", null, row.last || "—"));
    tr.appendChild(el("td", "muted small", row.output));
    const state = el("td");
    const cls = { ok: "confirmed", idle: "declined", stale: "declined",
                  down: "failed" }[row.state] || "";
    state.appendChild(el("span", `pill ${cls}`, row.state));
    tr.appendChild(state);
    tr.appendChild(el("td", "muted small", row.note || ""));
    body.appendChild(tr);
  }
  $("#status-budget").textContent =
    "GPU budget, in priority order: " + out.budget.join(" → ") + ".";
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

/* ---------- tabs ---------- */

const LOADERS = {
  conversations: loadConversations,
  status: loadStatus,
  settings: loadSettings,
};

function onTab(name) {
  clearBanner();
  (LOADERS[name] || loadConversations)().catch(fail);
}

(async function boot() {
  const who = await FS.whoami().catch(() => null);
  const allowed = FS.can("audit.read");
  $("#denied").classList.toggle("hidden", allowed);
  $("#ai-tabs").classList.toggle("hidden", !allowed);
  $("#ai-main").classList.toggle("hidden", !allowed);
  if (!allowed) {
    $("#denied-who").textContent = who ? `${who.code} (${who.role})` : "nobody";
    return;
  }
  FS.applyCapGates();
  $("#conv-q").addEventListener("input", drawConversations);
  $("#conv-since").addEventListener("change",
    () => loadConversations().catch(fail));
  $("#trace-save").addEventListener("click", () => saveTraceDays().catch(fail));
  FS.tabs.init(document, onTab);
})().catch(fail);
