/* FactorySemantics MES dashboard.
   One poll of /dashboard/summary drives the machines, the feeds and the
   action forms; the work-order card reads the paged /workorders endpoint so
   its filters and pages are the server's, not a slice of a slice. Selects
   keep the operator's current choice across refreshes, so a 2-second refresh
   never steals input mid-entry.

   At a hundred machines the grid was a wall: every card, every refresh, no
   way to find one. The card is now a list like any other - filtered, paged,
   and honest about how much of the plant it is showing (STYLE.md rule 4). */

const REFRESH_MS = 2000;
const MACHINE_PAGE = 24;
const ORDER_PAGE = 10;

let user = null;
let summary = null;
let timer = null;

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
const clock = (ts) => (ts ? new Date(ts + (ts.endsWith("Z") ? "" : "Z")).toLocaleTimeString() : "");

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
  timer = null;
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
  if (!timer) timer = setInterval(refresh, REFRESH_MS);
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

/* ---------- which line a machine is on ----------
   The summary does not say; the equipment tree does. Read once at sign-in
   and every minute after - lines are not created between two refreshes. */

let lineOf = {};       // machine code -> {code, name} of its work centre
let lines = [];        // [{code, name, machines}]
let linesLoadedAt = 0;

function walk(node, centre, into) {
  if (node.level === "work_center") centre = { code: node.code, name: node.name };
  if (node.level === "work_unit" && centre) into[node.code] = centre;
  for (const child of node.children || []) walk(child, centre, into);
}

async function loadLines() {
  if (Date.now() - linesLoadedAt < 60000) return;
  try {
    const tree = await api("/equipment/tree");
    const map = {};
    for (const root of tree.roots || []) walk(root, null, map);
    lineOf = map;
    const seen = {};
    for (const centre of Object.values(map)) {
      seen[centre.code] = seen[centre.code] || { ...centre, machines: 0 };
      seen[centre.code].machines += 1;
    }
    lines = Object.values(seen).sort((a, b) => a.code.localeCompare(b.code));
    const select = $("#m-line");
    const keep = filters.line;
    select.replaceChildren(new Option("Any line", ""),
      ...lines.map((l) => new Option(`${l.code} — ${l.name} (${l.machines})`, l.code)));
    if (lines.some((l) => l.code === keep)) select.value = keep; else filters.line = "";
    linesLoadedAt = Date.now();
  } catch (err) {
    /* the grid still works unfiltered by line */
  }
}

/* ---------- refresh ---------- */

let alarms = [];
let orderPage = { items: [], total: 0, limit: ORDER_PAGE, offset: 0, has_more: false };

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
      api("/dashboard/summary"), api("/equipment/alarms"), api(orderQuery())]);
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
  $("#kpi-erp").textContent = plant.erp_pending ? "sending" : "clear";

  renderMachines(machines);
  renderOrders(orderPage);
  renderFeed($("#audit"), audit, (entry) => [
    clock(entry.ts),
    [entry.actor + (entry.on_behalf_of ? ` for ${entry.on_behalf_of}` : ""), " ", el("b", null, entry.action), ` ${entry.entity_id}`],
  ]);
  renderFeed($("#ncs"), ncs, (nc) => [clock(nc.created_at), [el("b", null, nc.code), ` ${nc.description}`]], "nc");
  renderFeed($("#alarms"), alarms.filter((a) => a.active.length),
             (a) => [clock(a.ts), [FS.link("machine", a.equipment), ` ${a.active.join(", ")}`]], "nc");

  fillSelect("machines", machines.map((m) => [m.code, `${m.code} — ${m.name}`]));
  fillSelect("orders", orders.filter((o) => ["released", "running"].includes(o.status)).map((o) => [o.code, o.code]));
}

/* ---------- machines: filtered, paged, counted ---------- */

function matchMachine(m) {
  if (filters.state && m.state !== filters.state) return false;
  if (filters.line && (lineOf[m.code] || {}).code !== filters.line) return false;
  if (filters.q) {
    const needle = filters.q.toLowerCase();
    if (!m.code.toLowerCase().includes(needle) && !(m.name || "").toLowerCase().includes(needle)) return false;
  }
  return true;
}

function renderMachines(machines) {
  const matching = machines.filter(matchMachine);
  if (filters.offset >= matching.length) filters.offset = Math.max(0, Math.floor((matching.length - 1) / MACHINE_PAGE) * MACHINE_PAGE);
  const page = {
    items: matching.slice(filters.offset, filters.offset + MACHINE_PAGE),
    total: matching.length, limit: MACHINE_PAGE, offset: filters.offset,
    has_more: filters.offset + MACHINE_PAGE < matching.length,
  };
  const filtered = matching.length !== machines.length;
  $("#m-count").textContent = filtered
    ? `— ${page.items.length} of ${matching.length} matching, ${machines.length} in the plant`
    : `— ${page.items.length} of ${machines.length} in the plant`;

  const grid = $("#machines");
  if (!page.items.length) {
    grid.replaceChildren(el("p", "empty", machines.length ? "No machine matches these filters." : "No machines yet."));
  } else {
    grid.replaceChildren(...page.items.map(machineCard));
  }
  FS.pager($("#m-pager"), page, (offset) => { filters.offset = offset; writeFilters(); renderMachines(summary.machines); });
}

function machineCard(m) {
  const card = el("div", `machine ${m.state}`);
  const head = el("div", "machine-head");
  head.append(FS.link("machine", m.code, "machine-code"), el("span", "machine-name", m.name),
              el("span", `state ${m.state}`, m.state));
  const meta = el("div", "machine-meta");
  const order = el("span");
  order.append("Order ", el("strong", null, m.current_order || "—"));
  // Machines disagree about what their process value is called, so show the
  // name the machine itself uses rather than assuming everything is a temperature.
  const analog = el("span");
  analog.append(m.analog ? `${m.analog.name} ` : "Value ",
                el("strong", null, m.analog ? m.analog.value.toFixed(1) : "—"));
  meta.append(order, analog);
  const line = lineOf[m.code];
  if (line) {
    const where = el("div", "machine-line");
    where.append("on ", FS.link("line", line.code, null, line.code));
    meta.append(where);
  }

  const bars = el("div", "bars");
  [["Availability", m.oee.availability], ["Performance", m.oee.performance],
   ["Quality", m.oee.quality], ["OEE", m.oee.oee]].forEach(([label, value], index) => {
    const row = el("div", `bar-row${index === 3 ? " total" : ""}`);
    const bar = el("div", "bar");
    const fill = el("i");
    fill.style.width = `${Math.min(100, (value || 0) * 100)}%`;
    bar.append(fill);
    row.append(el("span", null, label), bar, el("span", "num", pct(value)));
    bars.append(row);
  });

  card.append(head, meta, bars);
  return card;
}

function applyMachineFilters() {
  filters.q = $("#m-q").value.trim();
  filters.line = $("#m-line").value;
  filters.state = $("#m-state").value;
  filters.offset = 0;
  writeFilters();
  if (summary) renderMachines(summary.machines);
}

$("#m-q").addEventListener("input", applyMachineFilters);
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

async function loadSpecs() {
  const specs = await api("/quality/specs").catch(() => []);
  fillSelect(
    "specs",
    specs.map((spec) => [`${spec.material}::${spec.characteristic}`,
                         `${spec.material} ${spec.characteristic} [${spec.min_value}–${spec.max_value}]`])
  );
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
