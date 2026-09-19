/* FactorySemantics MES dashboard.
   One poll of /dashboard/summary drives the machines, the feeds and the
   action forms; the work-order card reads the paged /workorders endpoint so
   its filters and pages are the server's, not a slice of a slice. Selects
   keep the operator's current choice across refreshes, so a 2-second refresh
   never steals input mid-entry.

   At a hundred machines the grid was a wall: every card, every refresh, no
   way to find one. The card is now a list like any other - filtered, paged,
   and honest about how much of the plant it is showing (STYLE.md rule 4).

   Both the filter and the page are the SERVER'S. They were the browser's
   until 2026-09-14, which meant the screen downloaded every machine in the
   plant - with its OEE and its process value - twice a second in order to
   draw twenty-four of them. On the 108-station lab plant that was the Floor
   summary's whole cost; on a thousand machines it is a wall of queries
   nobody sees. The tiles above still count the whole plant: a grid filtered
   to six machines must never read as a six-machine plant. */

const REFRESH_MS = 2000;
const PENDING_REFRESH_MS = 30000;
const MACHINE_PAGE = 24;
const ORDER_PAGE = 10;
const SPEC_CHOICES = 200;

let user = null;
let summary = null;
let timer = null;
let pendingTimer = null;

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
// Capabilities, never role names. The ladder version of this line put
// quality_inspector at index -1 and hid the one form that role exists
// to use.
const can = (cap) => FS.can(cap);
const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");
// The plant's clock, not the browser's. See common.js.
const clock = (ts) => FS.fmt.clock(ts);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (response.status === 401) {
    showLogin();
    throw new Error("signed out");
  }
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error((data && data.detail) || `${response.status} ${response.statusText}`);
  return data;
}

function toast(message, kind = "good") {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast ${kind}`;
  clearTimeout(node._t);
  node._t = setTimeout(() => node.classList.add("hidden"), 3500);
}

/* ---------- the filters, in the URL ----------
   A filtered floor is a link: "?line=FILL2LINE&state=down" opens the floor
   on FILL2's down machines, which is what a supervisor sends a fitter. */

const filters = {
  q: "", line: "", state: "", offset: 0,
  oq: "", ostatus: "released,running", ooffset: 0,
};

function readFilters() {
  const params = new URL(window.location).searchParams;
  for (const key of ["q", "line", "state", "oq"]) filters[key] = params.get(key) || "";
  if (params.has("ostatus")) filters.ostatus = params.get("ostatus");
  filters.offset = Math.max(0, parseInt(params.get("offset") || "0", 10) || 0);
  filters.ooffset = Math.max(0, parseInt(params.get("ooffset") || "0", 10) || 0);
  $("#m-q").value = filters.q;
  $("#m-state").value = filters.state;
  $("#m-line").value = filters.line;
  $("#o-q").value = filters.oq;
  $("#o-status").value = filters.ostatus;
}

function writeFilters() {
  const url = new URL(window.location);
  const set = (key, value, blank) => {
    if (value === blank || value === "" || value === 0) url.searchParams.delete(key);
    else url.searchParams.set(key, String(value));
  };
  set("q", filters.q, ""); set("line", filters.line, ""); set("state", filters.state, "");
  set("offset", filters.offset, 0);
  set("oq", filters.oq, ""); set("ooffset", filters.ooffset, 0);
  if (filters.ostatus === "released,running") url.searchParams.delete("ostatus");
  else url.searchParams.set("ostatus", filters.ostatus);
  history.replaceState(null, "", url);
}

/* ---------- session ---------- */

function showLogin() {
  clearInterval(timer);
  clearInterval(pendingTimer);
  timer = null;
  pendingTimer = null;
  user = null;
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
}

async function start() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#user-name").textContent = user.name;
  $("#user-role").textContent = user.role;
  // whoami is cached in common.js; the header made the first call.
  await FS.whoami();
  FS.applyCapGates();
  readFilters();
  await loadLines();
  await refresh();
  await refreshPending();
  if (!timer) timer = setInterval(refresh, REFRESH_MS);
  // On its own, slower clock. This answer is per-caller, so unlike
  // /dashboard/summary it cannot be shared between everyone watching, and a
  // queue somebody signs off once a week does not need a two-second poll.
  if (!pendingTimer) pendingTimer = setInterval(refreshPending, PENDING_REFRESH_MS);
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  $("#login-error").textContent = "";
  try {
    user = await api("/auth/login", {
      method: "POST",
      body: { code: form.get("code"), password: form.get("password") },
    });
    event.target.reset();
    await start();
  } catch (error) {
    $("#login-error").textContent = "Invalid operator ID or password.";
  }
});

$("#logout").addEventListener("click", async () => {
  await api("/auth/logout", { method: "POST" }).catch(() => {});
  showLogin();
});

/* ---------- which lines this plant has ----------
   Just the work centres, which is tens of rows on any plant. This used to
   pull the whole equipment tree - every node the plant has - once a minute,
   to fill one dropdown and to label the cards with their line. The machines
   now carry their own line, and this asks only for the lines. */

let lines = [];        // [{code, name}]
let linesLoadedAt = 0;

async function loadLines() {
  if (Date.now() - linesLoadedAt < 60000) return;
  try {
    const centres = await api("/masterdata/equipment?level=work_center");
    lines = centres.slice().sort((a, b) => a.code.localeCompare(b.code));
    const select = $("#m-line");
    const keep = filters.line;
    select.replaceChildren(new Option("Any line", ""),
      ...lines.map((l) => new Option(`${l.code} — ${l.name}`, l.code)));
    if (lines.some((l) => l.code === keep)) select.value = keep; else filters.line = "";
    linesLoadedAt = Date.now();
  } catch (err) {
    /* the grid still works unfiltered by line */
  }
}

/* ---------- refresh ---------- */

let alarms = [];
let orderPage = { items: [], total: 0, limit: ORDER_PAGE, offset: 0, has_more: false };

/* The floor's machine grid, as the server's page. `line` is a scope - it
   changes which plant the tiles are about - and q/state are a filter on the
   grid alone. The API draws the same distinction. */
function summaryQuery() {
  const params = new URLSearchParams({
    machine_limit: String(MACHINE_PAGE), machine_offset: String(filters.offset) });
  if (filters.line) params.set("line", filters.line);
  if (filters.q) params.set("machine_q", filters.q);
  if (filters.state) params.set("machine_state", filters.state);
  return `/dashboard/summary?${params}`;
}

function orderQuery() {
  const params = new URLSearchParams({ limit: String(ORDER_PAGE), offset: String(filters.ooffset) });
  // The API's status filter is repeatable, one value each - not a
  // comma list, which it answers with 422.
  for (const status of filters.ostatus.split(",").filter(Boolean)) params.append("status", status);
  if (filters.oq) params.set("q", filters.oq);
  return `/workorders?${params}`;
}

async function refresh() {
  try {
    [summary, alarms, orderPage] = await Promise.all([
      api(summaryQuery()), api("/equipment/alarms"), api(orderQuery())]);
    $("#live-dot").className = "dot ok";
    $("#live-text").textContent = "live";
    render();
    loadLines();
  } catch (error) {
    if (error.message === "signed out") return;
    $("#live-dot").className = "dot bad";
    $("#live-text").textContent = "connection lost";
  }
}

function render() {
  // keep the Book output order list in step with the machine chosen
  queueMicrotask(fillReportOrders);
  window.__fsmesPageData = { ...summary, orders_page: orderPage, filters: { ...filters } };
  const { plant, machines, orders, audit, non_conformances: ncs } = summary;

  $("#kpi-machines").textContent = `${plant.machines_running}/${plant.machines_total}`;
  $("#kpi-orders").textContent = plant.active_orders;
  $("#kpi-oee").textContent = pct(plant.oee);
  // Of how many. A machine drops out of this mean for a stated reason — too
  // little of the window watched, no rating, nothing counted, or counted work
  // that will not fit inside its run time — and an average that does not say
  // how many it covers turns every one of those reasons into silence.
  const covered = [`mean of ${plant.oee_machines} of ${plant.oee_machines_total}`];
  if (plant.oee_counts_outrun) {
    covered.push(`${plant.oee_counts_outrun} counting past its run time`);
  }
  $("#kpi-oee-sub").textContent = plant.machines_total ? covered.join(" · ") : "";
  $("#kpi-erp").textContent = plant.erp_pending ? "sending" : "clear";

  renderMachines(machines, summary.machines_page);
  renderOrders(orderPage);
  renderFeed($("#audit"), audit, (entry) => [
    clock(entry.ts),
    [entry.actor + (entry.on_behalf_of ? ` for ${entry.on_behalf_of}` : ""), " ", el("b", null, entry.action), ` ${entry.entity_id}`],
  ]);
  renderFeed($("#ncs"), ncs, (nc) => [clock(nc.created_at), [el("b", null, nc.code), ` ${nc.description}`]], "nc");
  renderFeed($("#alarms"), alarms.filter((a) => a.active.length),
             (a) => [clock(a.ts), [FS.link("machine", a.equipment), ` ${a.active.join(", ")}`]], "nc");

  // A datalist, not a select: a thousand machines is not a dropdown, and the
  // page in front of the operator is the part worth offering. Any code may
  // still be typed - the server is what decides whether it exists.
  fillOptions("machine-options", machines.map((m) => [m.code, m.name]));
  fillSelect("orders", orders.filter((o) => ["released", "running"].includes(o.status)).map((o) => [o.code, o.code]));
}

/* ---------- what is waiting on the person reading this ----------

   The step the product's three approval lifecycles were missing: a draft
   somebody has to know to go and look for is a draft nobody signs. The
   server answers from the caller's own capabilities, so this panel is empty
   of things they cannot act on and absent entirely for somebody who can act
   on nothing. */

async function refreshPending() {
  const panel = $("#pending-panel");
  if (!panel) return;
  let data;
  try {
    data = await api("/dashboard/pending-approvals");
  } catch (error) {
    return;  // the live dot already says the connection is gone
  }
  // On no screen that cannot act on it. Not "empty": absent.
  if (!data.kinds_total) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  $("#pending-count").textContent = data.total
    ? `${data.items.length} of ${data.total} waiting`
    : "nothing waiting";

  const body = $("#pending tbody");
  body.replaceChildren();
  if (!data.items.length) {
    const row = el("tr");
    const cell = el("td", "empty", "Nothing is waiting on you.");
    cell.colSpan = 6;
    row.append(cell);
    body.append(row);
    return;
  }
  for (const item of data.items) {
    body.append(pendingRow(item));
  }
}

function pendingRow(item) {
  const row = el("tr");
  row.append(el("td", null, item.kind.replace(/_/g, " ")));
  row.append(el("td", null, `${item.code} rev ${item.revision}`));
  row.append(el("td", null, `${item.title} — ${item.headline}`));
  // Who drafted it and, when an agent drafted for somebody, on whose behalf.
  row.append(el("td", null, item.drafted_by
    + (item.on_behalf_of ? ` for ${item.on_behalf_of}` : "")));
  // How long it has waited: the one column that changes with time, so a
  // forgotten draft reads as "11 days" rather than falling off the end.
  row.append(el("td", null, waited(item.waiting_seconds)));
  row.append(cellWith(button("Approve", async () => {
    await act(item.approve, `${item.code} is in force`);
    await refreshPending();
  })));
  return row;
}

function waited(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.floor(hours / 24)} days`;
}

/* ---------- machines: the server's page ---------- */

function renderMachines(machines, envelope) {
  // The server's envelope, in the shape every other list uses. `total` is
  // what matched the filter; `scope_total` is how many machines the tiles
  // above are counting.
  const info = envelope || { total: machines.length, scope_total: machines.length,
                             limit: MACHINE_PAGE, offset: 0, has_more: false, filtered: false };
  filters.offset = info.offset;
  const page = { items: machines, total: info.total, limit: info.limit || MACHINE_PAGE,
                 offset: info.offset, has_more: info.has_more };
  const where = filters.line ? `on ${filters.line}` : "in the plant";
  $("#m-count").textContent = FS.countText(page, info.scope_total, where);
  $("#m-scope").textContent = filters.line
    ? `Scoped to ${filters.line}. The tiles above count this line, not the whole plant.`
    : "";

  const grid = $("#machines");
  if (!page.items.length) {
    grid.replaceChildren(el("p", "empty", info.scope_total
      ? "No machine matches these filters." : "No machines yet."));
  } else {
    grid.replaceChildren(...page.items.map(machineCard));
  }
  FS.pager($("#m-pager"), page, (offset) => { filters.offset = offset; writeFilters(); refresh(); });
}

function machineCard(m) {
  // A machine the MES cannot see has no state to show. The card says so
  // instead of showing the last one it heard, which would be a claim about
  // a machine nobody is watching (decision 0030).
  const shown = FS.connection.stateClass(m);
  const card = el("div", `machine ${shown}`);
  const head = el("div", "machine-head");
  head.append(FS.link("machine", m.code, "machine-code"), el("span", "machine-name", m.name),
              el("span", `state ${shown}`, FS.connection.stateText(m)));
  const meta = el("div", "machine-meta");
  const order = el("span");
  order.append("Order ", el("strong", null, m.current_order || "—"));
  // Machines disagree about what their process value is called, so show the
  // name the machine itself uses rather than assuming everything is a temperature.
  const analog = el("span");
  analog.append(m.analog ? `${m.analog.name} ` : "Value ",
                el("strong", null, m.analog ? m.analog.value.toFixed(1) : "—"));
  meta.append(order, analog);
  const lostLink = FS.connection.badge(m);
  if (lostLink) meta.append(lostLink);
  const line = m.line;
  if (line) {
    const where = el("div", "machine-line");
    where.append("on ", FS.link("line", line.code, null, line.code));
    meta.append(where);
  }

  // The bars, and the one row that says how much of the window they were
  // measured over - drawn by the kit so the Floor tile and the machine page
  // can never say it differently. Below this plant's pack floor the kit draws
  // the ledger instead of the figures.
  const bars = el("div", "bars");
  FS.kit.oeeBars(bars, m.oee);

  card.append(head, meta, bars);
  return card;
}

function applyMachineFilters() {
  filters.q = $("#m-q").value.trim();
  filters.line = $("#m-line").value;
  filters.state = $("#m-state").value;
  filters.offset = 0;
  writeFilters();
  refresh();
}

// Typing is debounced: the filter is a request now, not a slice of something
// already downloaded, and one request per keystroke is how a search box
// becomes a load generator.
let machineTyping = null;
$("#m-q").addEventListener("input", () => {
  clearTimeout(machineTyping);
  machineTyping = setTimeout(applyMachineFilters, 250);
});
$("#m-line").addEventListener("change", applyMachineFilters);
$("#m-state").addEventListener("change", applyMachineFilters);
$("#m-clear").addEventListener("click", () => {
  $("#m-q").value = ""; $("#m-line").value = ""; $("#m-state").value = "";
  applyMachineFilters();
});

/* ---------- work orders: the server's page ---------- */

function renderOrders(page) {
  const body = $("#orders tbody");
  const orders = page.items || [];
  const label = $("#o-status").selectedOptions[0];
  $("#o-count").textContent = `— ${orders.length} of ${(page.total || 0).toLocaleString()} ${label && label.value ? label.textContent.toLowerCase() : "in any status"}`;
  if (!orders.length) {
    const row = el("tr");
    const cell = el("td", "muted", filters.oq || filters.ostatus ? "No order matches these filters." : "No orders.");
    cell.colSpan = 7;
    row.append(cell);
    body.replaceChildren(row);
  } else {
    body.replaceChildren(...orders.map(orderRow));
  }
  FS.pager($("#o-pager"), page, (offset) => { filters.ooffset = offset; writeFilters(); refresh(); });
}

function orderRow(order) {
  const row = el("tr");
  const progress = el("div", "progress");
  const bar = el("div", "bar");
  const fill = el("i");
  const done = order.quantity ? Math.min(1, (order.good_qty ?? order.good ?? 0) / order.quantity) : 0;
  fill.style.width = `${done * 100}%`;
  bar.append(fill);
  progress.append(bar, el("span", null, pct(done)));

  const progressCell = el("td");
  progressCell.append(progress);

  const actions = el("td");
  if (order.status === "planned" && can("orders.release")) {
    actions.append(button("Release", () => act(`/workorders/${order.code}/release`, `${order.code} released`)));
  } else if (order.status === "completed" && can("orders.close")) {
    actions.append(button("Close", () => act(`/workorders/${order.code}/close`, `${order.code} closed`)));
  } else {
    actions.append(el("span", "muted", "—"));
  }

  row.append(
    el("td", "code", order.code),
    el("td", "code", order.material),
    cellWith(el("span", `pill ${order.status}`, order.status)),
    progressCell,
    el("td", "num", String(order.good_qty ?? order.good ?? 0)),
    el("td", "num", String(order.scrap_qty ?? order.scrap ?? 0)),
    actions
  );
  return row;
}

function applyOrderFilters() {
  filters.oq = $("#o-q").value.trim();
  filters.ostatus = $("#o-status").value;
  filters.ooffset = 0;
  writeFilters();
  refresh();
}

let orderTyping = null;
$("#o-q").addEventListener("input", () => { clearTimeout(orderTyping); orderTyping = setTimeout(applyOrderFilters, 250); });
$("#o-status").addEventListener("change", applyOrderFilters);

function cellWith(node) {
  const cell = el("td");
  cell.append(node);
  return cell;
}

function button(label, onClick) {
  const btn = el("button", "small", label);
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    await onClick();
    btn.disabled = false;
  });
  return btn;
}

function renderFeed(list, items, shape, cls) {
  if (!items.length) {
    list.replaceChildren(el("li", "empty", "Nothing yet."));
    return;
  }
  list.replaceChildren(
    ...items.map((item) => {
      const [when, what] = shape(item);
      const li = el("li");
      const line = el("div", `what ${cls || ""}`.trim());
      (Array.isArray(what) ? what : [what]).forEach((part) => line.append(part));
      li.append(el("div", "when", when), line);
      return li;
    })
  );
}

/* Refill a datalist. Unlike a select there is nothing to disturb: the
   operator's typed value lives in the input, not in the options. */
function fillOptions(id, options) {
  const list = document.getElementById(id);
  if (!list) return;
  const signature = options.map(([value]) => value).join("|");
  if (list.dataset.signature === signature) return;
  list.replaceChildren(...options.map(([value, label]) => {
    const option = el("option", null, label || "");
    option.value = value;
    return option;
  }));
  list.dataset.signature = signature;
}

/* Refill a select without disturbing what the operator has chosen. */
function fillSelect(name, options) {
  document.querySelectorAll(`select[data-fill="${name}"]`).forEach((select) => {
    const signature = options.map(([value]) => value).join("|");
    if (select.dataset.signature === signature) return;
    const chosen = select.value;
    select.replaceChildren(
      ...options.map(([value, label]) => {
        const option = el("option", null, label);
        option.value = value;
        return option;
      })
    );
    if (options.some(([value]) => value === chosen)) select.value = chosen;
    select.dataset.signature = signature;
  });
}

async function act(path, message, body) {
  try {
    await api(path, { method: "POST", body });
    toast(message, "good");
    await refresh();
    return true;
  } catch (error) {
    toast(error.message, "bad");
    return false;
  }
}

/* ---------- shop-floor actions ---------- */

/* The order is named, not inferred. The backend would guess the first
   not-done operation on the machine, which with two orders queued books to
   whichever sorts first and says nothing - quantity against the wrong order,
   discovered at month end. The select defaults to that same first operation,
   so the honest path costs no extra clicks. */
async function fillReportOrders() {
  const machine = $("#form-report [name=equipment]").value;
  const select = $("#form-report [name=order]");
  const keep = select.value;
  select.textContent = "";
  if (!machine) return;
  try {
    const queue = await api(`/workorders/dispatch?equipment=${encodeURIComponent(machine)}`);
    const open = queue.filter((entry) => entry.status !== "done");
    for (const entry of open) {
      const option = el("option", null,
        `${entry.order} · op ${entry.seq} (${entry.operation})`);
      option.value = entry.order;
      option.dataset.seq = entry.seq;
      select.appendChild(option);
    }
    if (keep && [...select.options].some((o) => o.value === keep)) select.value = keep;
    if (!open.length) {
      select.appendChild(el("option", null, "no open order on this machine"));
      select.options[0].value = "";
    }
  } catch (err) {
    /* the machine dropdown still works; submit falls back to inference */
  }
}

$("#form-report [name=equipment]").addEventListener("change", fillReportOrders);

$("#form-report").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const chosen = $("#form-report [name=order]").selectedOptions[0];
  const body = {
    equipment: form.get("equipment"),
    good: Number(form.get("good") || 0),
    scrap: Number(form.get("scrap") || 0),
  };
  if (form.get("order")) {
    body.order = form.get("order");
    if (chosen && chosen.dataset.seq) body.seq = Number(chosen.dataset.seq);
  }
  await act("/execution/report", "Output booked.", body);
});

$("#form-consume").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const ok = await act("/execution/consume", "Material issued.", {
    order: form.get("order"),
    lot: form.get("lot"),
    quantity: Number(form.get("quantity") || 0),
  });
  if (ok) await loadLots();
});

$("#form-quality").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const [material, characteristic] = String(form.get("spec")).split("::");
  try {
    const result = await api("/quality/checks", {
      method: "POST",
      body: { material, characteristic, value: Number(form.get("value")), order: null },
    });
    toast(
      result.result === "pass" ? "Check passed." : `Out of spec — ${result.non_conformance} opened.`,
      result.result === "pass" ? "good" : "bad"
    );
    event.target.reset();
    await refresh();
  } catch (error) {
    toast(error.message, "bad");
  }
});

/* Lots and specs change rarely — fetched on sign-in, not every 2 seconds. */
async function loadLots() {
  const paged = await api("/execution/lots?limit=200").catch(() => ({ items: [] }));
  const lots = paged.items || [];
  fillSelect(
    "lots",
    lots.filter((lot) => lot.status === "available").map((lot) => [lot.code, `${lot.code} (${lot.quantity})`])
  );
}

/* The characteristics this form can record against. A plant with more than
   SPEC_CHOICES of them gets the first page and is told so - a select that
   silently stops at five hundred is the kind of quiet truncation this
   product does not do. The Quality screen is where all of them live. */
async function loadSpecs() {
  const page = await api(`/quality/specs?limit=${SPEC_CHOICES}`).catch(() => ({ items: [], total: 0 }));
  const specs = page.items || [];
  const options = specs.map((spec) => [`${spec.material}::${spec.characteristic}`,
                                       `${spec.material} ${spec.characteristic} [${spec.min_value}–${spec.max_value}]`]);
  fillSelect("specs", options);
  const note = $("#quality-scope");
  if (note) {
    note.textContent = (page.total || 0) > specs.length
      ? `${specs.length} of ${(page.total || 0).toLocaleString()} characteristics — the rest are on the Quality screen.`
      : "";
  }
}

/* ---------- boot ---------- */

(async function boot() {
  try {
    user = await api("/auth/me");
    await start();
    await Promise.all([loadLots(), loadSpecs()]);
  } catch {
    showLogin();
  }
})();
