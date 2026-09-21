/* One machine, arm's length.

   The operator's screen, built against docs/design/backlog/review-station.md:
   four things a shift, none more than three taps, and the one hard rule -
   going down demands a reason, because an unlabelled stop is the row the
   downtime pareto cannot explain. */

const { $, el, api, fmt, toast } = window.FS;

const REFRESH_MS = 3000;
const STATES = ["running", "idle", "down", "setup"];

let machine = null;
let vocabulary = null;     // the plant's approved downtime reasons, or null
let current = null;        // this machine's current state row
let queue = [];
let pendingState = null;   // a state change awaiting its reason
let completing = null;     // a maintenance order awaiting findings
let qSpecs = [];           // specs for what this machine is running now
let qSpecsTotal = 0;       // how many that material has in all
let qMaterial = null;      // the material those specs belong to

function live(ok) {
  $("#live-dot").className = "dot" + (ok ? "" : " bad");
  $("#live-text").textContent = ok ? "live" : "reconnecting…";
}

/* ---------- drawing only what changed ----------

   This screen re-reads the plant every few seconds and then stays open for
   the rest of the shift. Every list on it used to empty itself and rebuild
   identical rows on every one of those ticks, so the quality results
   disappeared and came back - and the card changed height while they did -
   twenty times a minute, with nothing behind them having changed. That is
   indistinguishable, at arm's length, from data arriving.

   The fix is the smallest one that is still honest: before a list is
   rebuilt, say in one string what is about to be drawn, and if that is word
   for word what the list is already showing, leave the DOM alone. Same rows,
   same order, same words: nothing to do. Anything else redraws wholesale,
   exactly as it did before, so no real change is ever held back by a
   comparison that tried to be clever. No diffing library, and no cache of
   the API's answers - what is compared is what was drawn. */

const drawn = new WeakMap();

/** True when `node` is already showing `description`. Records the
    description when it is not, so the caller can go on and draw it. */
function alreadyShowing(node, description) {
  if (drawn.get(node) === description) return true;
  drawn.set(node, description);
  return false;
}

/** Write text only when it would say something different. Assigning the same
    string again throws away the text node and makes a new one for nothing. */
function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

/* ---------- which machine ---------- */

function rememberMachine(code) {
  machine = code;
  try { localStorage.setItem("fsmes-station", code); } catch (err) { /* private window */ }
  const url = new URL(window.location);
  url.searchParams.set("m", code);
  history.replaceState(null, "", url);
  $("#machine-page").href = `/dashboard/machine/${encodeURIComponent(code)}`;
}

async function loadMachines() {
  const states = await api("/equipment/states");
  const select = $("#machine");
  select.textContent = "";
  for (const row of states) {
    select.appendChild(new Option(row.equipment, row.equipment));
  }
  const fromUrl = new URL(window.location).searchParams.get("m");
  let saved = null;
  try { saved = localStorage.getItem("fsmes-station"); } catch (err) { /* fine */ }
  const want = fromUrl || saved;
  if (want && [...select.options].some((o) => o.value === want)) {
    select.value = want;
  }
  rememberMachine(select.value);
  select.addEventListener("change", () => {
    rememberMachine(select.value);
    refresh();
  });
}

/* ---------- state ---------- */

function renderState(states) {
  current = states.find((row) => row.equipment === machine) || null;
  const pill = $("#machine-state");
  const state = current ? current.state : "unknown";
  const cls = `pill ${state}`;
  if (pill.className !== cls) pill.className = cls;
  setText(pill, state);
  setText($("#machine-since"), current && current.since
    ? `since ${fmt.clock(current.since)}` + (current.reason ? ` — ${current.reason}` : "")
    : "");

  // The four buttons never change; only which of them is the current state
  // does, so that is the whole of what this row is showing.
  const grid = $("#state-buttons");
  if (alreadyShowing(grid, state)) return;
  grid.textContent = "";
  for (const name of STATES) {
    const button = el("button", `state-btn ${name}` + (state === name ? " current" : ""), name);
    button.type = "button";
    button.setAttribute("aria-pressed", String(state === name));
    button.addEventListener("click", () => requestState(name));
    grid.appendChild(button);
  }
}

function requestState(name) {
  if (!machine) return;
  if (name === "down") {
    // The reason is the point. The confirm button stays dead until there is
    // one, because "down: (blank)" is the row nobody can act on.
    pendingState = name;
    $("#down-reason").value = "";
    const select = $("#down-reason-code");
    if (select) select.selectedIndex = 0;
    $("#down-reason-help").textContent = "";
    $("#reason-confirm").disabled = true;
    $("#reason-box").classList.remove("hidden");
    (picking() ? select : $("#down-reason")).focus();
    return;
  }
  setState(name, null, null);
}

/* Whether this plant names its own stops. The server owns the list; the
   screen only renders what it is given, which is why the two hardcoded
   option blocks elsewhere in this product could drift out of step with the
   enum behind them and this one cannot. */
function picking() {
  return !!(vocabulary && vocabulary.total);
}

async function loadReasons() {
  try {
    vocabulary = await api("/equipment/downtime-reasons");
  } catch (err) {
    vocabulary = null;   // an unreadable list is not an empty one
  }
  const select = $("#down-reason-code");
  if (!select) return;
  $("#reason-pick").classList.toggle("hidden", !picking());
  $("#reason-type").classList.toggle("hidden", picking());
  if (!picking()) return;

  select.replaceChildren();
  const placeholder = new Option(`Choose one of ${vocabulary.total}`, "");
  placeholder.disabled = true;
  placeholder.selected = true;
  select.appendChild(placeholder);
  for (const [code, name] of Object.entries(vocabulary.names)) {
    select.appendChild(new Option(name, code));
  }
}

async function setState(name, reason, reasonCode) {
  try {
    const body = { state: name };
    if (reason) body.reason = reason;
    if (reasonCode) body.reason_code = reasonCode;
    await api(`/equipment/${machine}/state`, { method: "POST", body });
    toast(`${machine} → ${name}`);
    $("#reason-box").classList.add("hidden");
    await refresh();
  } catch (err) {
    toast(err.message, "bad");
  }
}

function wireReason() {
  $("#down-reason").addEventListener("input", (event) => {
    $("#reason-confirm").disabled = !event.target.value.trim();
  });
  $("#down-reason-code").addEventListener("change", (event) => {
    const code = event.target.value;
    $("#reason-confirm").disabled = !code;
    // The sentence behind the chosen word, so two reasons that read alike on
    // a button can still be told apart at the machine.
    $("#down-reason-help").textContent = (vocabulary && vocabulary.reasons[code]) || "";
  });
  $("#reason-cancel").addEventListener("click", () => {
    pendingState = null;
    $("#reason-box").classList.add("hidden");
  });
  $("#reason-confirm").addEventListener("click", () => {
    if (picking()) {
      const code = $("#down-reason-code").value;
      if (!code) return;
      setState(pendingState, null, code);
      return;
    }
    const reason = $("#down-reason").value.trim();
    if (!reason) return;
    setState(pendingState, reason, null);
  });
}

/* ---------- the queue ---------- */

function renderQueue() {
  const list = $("#queue");
  const canBook = window.FS.can("production.book");
  const showing = JSON.stringify([canBook, queue.map((entry) => [
    entry.order, entry.seq, entry.operation, entry.good_qty, entry.quantity, entry.status])]);
  if (alreadyShowing(list, showing)) return;
  list.textContent = "";
  if (!queue.length) {
    list.appendChild(el("li", "muted", "Nothing queued on this machine."));
    return;
  }
  for (const entry of queue) {
    const li = el("li", entry.status === "running" ? "running-op" : null);
    const what = el("div", "what");
    what.appendChild(el("div", "mono", `${entry.order} · op ${entry.seq}`));
    what.appendChild(el("div", "muted small",
      `${entry.operation} — ${fmt.qty(entry.good_qty)} of ${fmt.qty(entry.quantity)} good`));
    li.appendChild(what);

    if (canBook) {
      if (entry.status === "pending") {
        const start = el("button", "ghost", "Start");
        start.type = "button";
        start.addEventListener("click", () => opAction(entry, "start"));
        li.appendChild(start);
      } else if (entry.status === "running") {
        const done = el("button", "ghost", "Complete");
        done.type = "button";
        done.addEventListener("click", () => opAction(entry, "complete"));
        li.appendChild(done);
      }
    }
    list.appendChild(li);
  }
}

async function opAction(entry, action) {
  try {
    await api(`/workorders/${entry.order}/operations/${entry.seq}/${action}`,
              { method: "POST" });
    toast(`${entry.order} op ${entry.seq}: ${action}`);
    await refresh();
  } catch (err) {
    toast(err.message, "bad");
  }
}

/* ---------- booking, always against a named order ---------- */

function renderBookTargets() {
  const select = $("#book-order");
  const open = queue.filter((entry) => entry.status !== "done");
  // Rebuilding a dropdown underneath somebody is worse than a flicker: it
  // shuts the list while they are reading it. Only the options matter here,
  // so only a change in the options rebuilds them.
  const showing = JSON.stringify(
    open.map((entry) => [entry.order, entry.seq, entry.operation]));
  if (!alreadyShowing(select, showing)) {
    const keep = select.value;
    select.textContent = "";
    for (const entry of open) {
      const option = new Option(`${entry.order} · op ${entry.seq} (${entry.operation})`,
                                entry.order);
      option.dataset.seq = entry.seq;
      select.appendChild(option);
    }
    if (keep && [...select.options].some((o) => o.value === keep)) select.value = keep;
  }
  $("#book-submit").disabled = !open.length;

  const running = open.find((entry) => entry.status === "running") || open[0];
  setText($("#issue-where"), running
    ? `Issues to ${running.order} at this station.`
    : "No open order to issue against.");
  $("#issue-submit").disabled = !running;
}

function wireBook() {
  $("#book").addEventListener("submit", (event) => event.preventDefault());
  $("#book-submit").addEventListener("click", async () => {
    const chosen = $("#book-order").selectedOptions[0];
    if (!chosen) return;
    const good = Number($("#book-good").value || 0);
    const scrap = Number($("#book-scrap").value || 0);
    if (good === 0 && scrap === 0) {
      toast("Nothing to book.", "bad");
      return;
    }
    try {
      await api("/execution/report", { method: "POST", body: {
        equipment: machine, order: chosen.value,
        seq: Number(chosen.dataset.seq), good, scrap,
      }});
      toast(`Booked ${fmt.qty(good)} good, ${fmt.qty(scrap)} scrap.`);
      $("#book-good").value = "0";
      $("#book-scrap").value = "0";
      await refresh();
    } catch (err) {
      toast(err.message, "bad");
    }
  });
}

/* ---------- material ---------- */

async function loadLots() {
  try {
    const page = await api("/execution/lots?status=available&limit=200");
    const select = $("#issue-lot");
    const keep = select.value;
    select.textContent = "";
    for (const lot of page.items || []) {
      select.appendChild(new Option(
        `${lot.code} — ${fmt.qty(lot.quantity)} ${lot.material}`, lot.code));
    }
    if (keep && [...select.options].some((o) => o.value === keep)) select.value = keep;
  } catch (err) { /* the panel is capability-gated; a fetch miss just empties it */ }
}

function wireIssue() {
  $("#issue").addEventListener("submit", (event) => event.preventDefault());
  $("#issue-submit").addEventListener("click", async () => {
    const running = queue.find((entry) => entry.status === "running")
                 || queue.find((entry) => entry.status !== "done");
    if (!running) return;
    try {
      await api("/execution/consume", { method: "POST", body: {
        order: running.order, lot: $("#issue-lot").value,
        quantity: Number($("#issue-qty").value || 0), seq: running.seq,
      }});
      toast("Material issued.");
      await Promise.all([loadLots(), refresh()]);
    } catch (err) {
      toast(err.message, "bad");
    }
  });
}

/* ---------- quality at the station ----------

   Conversation 9, 2026-09-02: "you should be able to record and/or see
   quality results in the floor/station page." So: the characteristics that
   have a specification for whatever this machine is running, the last few
   results with the spec that judged them, and one field to record another.

   The list is the characteristics with a spec and nothing else. A
   measurement with nothing to judge it against cannot pass or fail, and
   offering one here would invite a reading the MES then has no verdict for.
   If the machine is running nothing, the card says so rather than showing
   the whole plant's specifications. */

const Q_RECENT = 6;
// One material's characteristics. Bounded by the material rather than by the
// plant, but counted all the same - a list that stops short says so.
const QUALITY_CHARACTERISTICS = 200;

function qKey() {
  return $("#q-char").value;
}

function specFor(characteristic) {
  return qSpecs.find((s) => s.characteristic === characteristic) || null;
}

function bandText(spec) {
  if (!spec) return "";
  const unit = spec.unit ? ` ${spec.unit}` : "";
  if (spec.min_value !== null && spec.max_value !== null) {
    return `Spec ${spec.min_value} to ${spec.max_value}${unit}.`;
  }
  if (spec.min_value !== null) return `Spec: at least ${spec.min_value}${unit}.`;
  if (spec.max_value !== null) return `Spec: at most ${spec.max_value}${unit}.`;
  // A spec row with no limits judges nothing, and saying so is better than
  // drawing a band that is not there.
  return "This characteristic has no limits set, so nothing here can fail.";
}

function showSpec() {
  setText($("#q-spec"), bandText(specFor(qKey())));
}

/* Which material this station is working on: the running operation if there
   is one, otherwise the first thing queued. The same choice the Issue panel
   makes, for the same reason - an operator judges what is in front of them. */
function stationMaterial() {
  const op = queue.find((entry) => entry.status === "running") || queue[0];
  return op ? op.material || null : null;
}

async function renderQuality() {
  if (!window.FS || !FS.can("quality.record")) return;
  const material = stationMaterial();
  const label = $("#q-for");
  const select = $("#q-char");

  if (material !== qMaterial) {
    qMaterial = material;
    qSpecs = [];
    if (material) {
      try {
        // One material's characteristics: bounded by the material, not by the
        // plant. /quality/specs answers with the standard envelope, so this
        // names a page and reads `items`.
        const page = await api(
          `/quality/specs?material=${encodeURIComponent(material)}&limit=${QUALITY_CHARACTERISTICS}`);
        qSpecs = page.items || [];
        qSpecsTotal = page.total || qSpecs.length;
      } catch (err) { qSpecs = []; qSpecsTotal = 0; }
    }
    const keep = select.value;
    select.textContent = "";
    for (const spec of qSpecs) select.appendChild(new Option(spec.characteristic, spec.characteristic));
    if (keep && [...select.options].some((o) => o.value === keep)) select.value = keep;
  }

  setText(label, material ? `on ${material}` : "");
  const nothingToJudge = !material || !qSpecs.length;
  $("#q-submit").disabled = nothingToJudge;
  $("#q-value").disabled = nothingToJudge;
  let spec;
  if (!material) {
    spec = "Nothing is queued on this machine, so there is nothing to inspect.";
  } else if (!qSpecs.length) {
    spec = `No characteristic has a specification for ${material}.`;
  } else {
    spec = bandText(specFor(qKey()));
    // A list that stops short must never look complete (STYLE.md rule 4).
    if (qSpecsTotal > qSpecs.length) {
      spec += ` — ${qSpecs.length} of ${qSpecsTotal} characteristics listed.`;
    }
  }
  setText($("#q-spec"), spec);
  await renderRecent();
}

async function renderRecent() {
  const list = $("#q-recent");
  const count = $("#q-count");
  if (!qMaterial || !qSpecs.length) {
    if (alreadyShowing(list, "nothing to show yet")) return;
    setText(count, "");
    list.textContent = "";
    list.appendChild(el("li", "muted", "Nothing to show yet."));
    return;
  }
  let page;
  try {
    page = await api(`/quality/checks?material=${encodeURIComponent(qMaterial)}`
                     + `&characteristic=${encodeURIComponent(qKey())}&limit=${Q_RECENT}`);
  } catch (err) {
    // A list that cannot be read is its own state, told apart from an empty
    // one, so recovering redraws and a second failed read does not.
    if (alreadyShowing(list, "could not read")) return;
    setText(count, "");
    list.textContent = "";
    list.appendChild(el("li", "muted", "Could not read the recent results."));
    return;
  }
  const spec = specFor(qKey());
  const unit = spec && spec.unit ? ` ${spec.unit}` : "";
  // Rule 4: say what this is a slice of, never just the slice.
  const heading = `— last ${page.items.length} of ${page.total.toLocaleString()} on `
                + `${qMaterial} · ${qKey()}`;
  // Everything the rows below print, in the order they print it. A reading
  // whose value, time, name or verdict changed is a different list; the same
  // six readings again are not.
  const showing = JSON.stringify([heading, unit, page.items.map(
    (check) => [check.value, check.ts, check.checked_by, check.result])]);
  if (alreadyShowing(list, showing)) return;
  setText(count, heading);
  list.textContent = "";
  if (!page.items.length) {
    list.appendChild(el("li", "muted", "No result recorded for this characteristic yet."));
    return;
  }
  for (const check of page.items) {
    const failed = check.result === "fail";
    const li = el("li", failed ? "out-of-spec" : null);
    const what = el("div", "what");
    what.appendChild(el("div", "mono", `${fmt.qty(check.value)}${unit}`));
    what.appendChild(el("div", "muted small",
      `${fmt.clock(check.ts)} · ${check.checked_by || "not recorded"}`));
    li.appendChild(what);
    li.appendChild(el("span", failed ? "result-fail" : "result-pass",
                      failed ? "out of spec" : "in spec"));
    list.appendChild(li);
  }
}

function wireQuality() {
  $("#quality").addEventListener("submit", (event) => event.preventDefault());
  $("#q-char").addEventListener("change", () => { showSpec(); renderRecent(); });
  $("#q-submit").addEventListener("click", async () => {
    const characteristic = qKey();
    const raw = $("#q-value").value;
    if (!characteristic || raw === "") {
      toast("Enter what the gauge read.", "bad");
      return;
    }
    const op = queue.find((entry) => entry.status === "running") || queue[0];
    try {
      const out = await api("/quality/checks", { method: "POST", body: {
        material: qMaterial, characteristic, value: Number(raw),
        order: op ? op.order : null,
        // The person is standing at this machine. Recording where a reading
        // was taken is the only honest way for anything downstream to say so.
        equipment: machine,
      }});
      $("#q-value").value = "";
      const raised = $("#q-raised");
      const signal = (out.spc || [])[0];
      if (signal) {
        // In spec and still a finding: the chart judged the series, not the
        // reading. Saying only "in spec" here would be the truth and not the
        // whole of it.
        toast(`SPC rule ${signal.rule} — ${signal.nonconformance} raised.`, "bad");
        raised.textContent = `${fmt.qty(out.value)} is ${out.result === "fail" ? "out of spec" : "in spec"}, `
                           + `but the control chart fired rule ${signal.rule}: ${signal.what}. `
                           + `Non-conformance ${signal.nonconformance} was raised; a supervisor decides `
                           + `what happens to the material on the Quality screen.`;
        raised.classList.remove("hidden");
        await renderRecent();
        return;
      }
      if (out.non_conformance) {
        // The MES raising one is the system working. Say which one it is, by
        // code, so the supervisor can find it on the Quality screen.
        toast(`${fmt.qty(out.value)} is out of spec — ${out.non_conformance} raised.`, "bad");
        raised.textContent = `${fmt.qty(out.value)} was outside the specification. `
                           + `Non-conformance ${out.non_conformance} was raised; a supervisor `
                           + `decides what happens to the material on the Quality screen.`;
        raised.classList.remove("hidden");
      } else {
        toast(`Recorded ${fmt.qty(out.value)} — in spec.`);
        raised.classList.add("hidden");
      }
      await renderRecent();
    } catch (err) {
      toast(err.message, "bad");
    }
  });
}

/* ---------- maintenance on this machine ---------- */

async function renderMaintenance() {
  if (!window.FS.can("maintenance.perform")) return;
  let rows = [];
  try {
    rows = await api(`/maintenance/orders?equipment=${encodeURIComponent(machine)}&limit=10`);
  } catch (err) {
    return;
  }
  const open = rows.filter((o) => ["due", "in_progress"].includes(o.status));
  const list = $("#maint");
  const showing = JSON.stringify(open.map(
    (order) => [order.code, order.kind, order.summary, order.reason, order.status]));
  if (alreadyShowing(list, showing)) return;
  list.textContent = "";
  if (!open.length) {
    list.appendChild(el("li", "muted", "Nothing owed on this machine."));
    return;
  }
  for (const order of open) {
    const li = el("li", order.status === "in_progress" ? "running-op" : null);
    const what = el("div", "what");
    what.appendChild(el("div", "mono", `${order.code} · ${order.kind}`));
    what.appendChild(el("div", "muted small",
      order.summary + (order.reason ? ` — ${order.reason}` : "")));
    li.appendChild(what);

    if (order.status === "due") {
      const start = el("button", "ghost", "Start");
      start.type = "button";
      start.addEventListener("click", async () => {
        try {
          await api(`/maintenance/orders/${order.code}/start`, { method: "POST" });
          toast(`${order.code} started`);
          await refresh();
        } catch (err) { toast(err.message, "bad"); }
      });
      li.appendChild(start);
    } else {
      const done = el("button", "ghost", "Complete…");
      done.type = "button";
      done.addEventListener("click", () => {
        completing = order.code;
        $("#maint-findings").value = "";
        $("#maint-complete").classList.remove("hidden");
        $("#maint-findings").focus();
      });
      li.appendChild(done);
    }
    list.appendChild(li);
  }
}

function wireMaintenance() {
  $("#maint-cancel").addEventListener("click", () => {
    completing = null;
    $("#maint-complete").classList.add("hidden");
  });
  $("#maint-confirm").addEventListener("click", async () => {
    if (!completing) return;
    try {
      await api(`/maintenance/orders/${completing}/complete`, { method: "POST",
        body: { findings: $("#maint-findings").value.trim() || null } });
      toast(`${completing} complete`);
      completing = null;
      $("#maint-complete").classList.add("hidden");
      await refresh();
    } catch (err) {
      toast(err.message, "bad");
    }
  });
}

/* ---------- the loop ---------- */

async function refresh() {
  if (!machine) return;
  try {
    const [states, dispatch] = await Promise.all([
      api("/equipment/states"),
      api(`/workorders/dispatch?equipment=${encodeURIComponent(machine)}`),
    ]);
    renderState(states);
    queue = dispatch.filter((entry) => entry.status !== "done");
    renderQueue();
    renderBookTargets();
    await renderQuality();
    await renderMaintenance();
    window.__fsmesPageData = { machine, state: current, queue };
    live(true);
  } catch (err) {
    live(false);
  }
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  wireReason();
  wireBook();
  wireIssue();
  wireQuality();
  wireMaintenance();
  await loadMachines();
  await loadReasons();
  await loadLots();
  await refresh();
  setInterval(refresh, REFRESH_MS);
})();
