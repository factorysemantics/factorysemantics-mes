/* One machine, arm's length.

   The operator's screen, built against docs/design/backlog/review-station.md:
   four things a shift, none more than three taps, and the one hard rule -
   going down demands a reason, because an unlabelled stop is the row the
   downtime pareto cannot explain. */

const { $, el, api, fmt, toast } = window.FS;

const REFRESH_MS = 3000;
const STATES = ["running", "idle", "down", "setup"];

let machine = null;
let current = null;        // this machine's current state row
let queue = [];
let pendingState = null;   // a state change awaiting its reason
let completing = null;     // a maintenance order awaiting findings

function live(ok) {
  $("#live-dot").className = "dot" + (ok ? "" : " bad");
  $("#live-text").textContent = ok ? "live" : "reconnecting…";
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
  pill.className = `pill ${state}`;
  pill.textContent = state;
  $("#machine-since").textContent = current && current.since
    ? `since ${fmt.clock(current.since)}` + (current.reason ? ` — ${current.reason}` : "")
    : "";

  const grid = $("#state-buttons");
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
    $("#reason-confirm").disabled = true;
    $("#reason-box").classList.remove("hidden");
    $("#down-reason").focus();
    return;
  }
  setState(name, null);
}

async function setState(name, reason) {
  try {
    const body = { state: name };
    if (reason) body.reason = reason;
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
  $("#reason-cancel").addEventListener("click", () => {
    pendingState = null;
    $("#reason-box").classList.add("hidden");
  });
  $("#reason-confirm").addEventListener("click", () => {
    const reason = $("#down-reason").value.trim();
    if (!reason) return;
    setState(pendingState, reason);
  });
}

/* ---------- the queue ---------- */

function renderQueue() {
  const list = $("#queue");
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

    if (window.FS.can("production.book")) {
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
  const keep = select.value;
  select.textContent = "";
  const open = queue.filter((entry) => entry.status !== "done");
  for (const entry of open) {
    const option = new Option(`${entry.order} · op ${entry.seq} (${entry.operation})`,
                              entry.order);
    option.dataset.seq = entry.seq;
    select.appendChild(option);
  }
  if (keep && [...select.options].some((o) => o.value === keep)) select.value = keep;
  $("#book-submit").disabled = !open.length;

  const running = open.find((entry) => entry.status === "running") || open[0];
  $("#issue-where").textContent = running
    ? `Issues to ${running.order} at this station.`
    : "No open order to issue against.";
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
  wireMaintenance();
  await loadMachines();
  await loadLots();
  await refresh();
  setInterval(refresh, REFRESH_MS);
})();
