/* Orders screen.
   The floor dashboard answers "what is happening now". This answers "where is
   this order and what went into it" - the question a planner asks, and the
   one a recall asks. Redesigned against the walkthrough in
   docs/design/backlog/review-orders.md: one row of filter chrome, tiles that
   admit to being selected, and the lifecycle on the order instead of in an
   agent's toolbox.

   First screen off the duplicated helpers: everything shared comes from FS. */

const { $, el, api, fmt } = window.FS;

const REFRESH_MS = 4000;
let selected = null;
let orders = [];
let total = 0;
let hasMore = false;
let offset = 0;
let filterStatus = [];
let summary = null;
let filterText = "";
let filterMaterial = "";
let dueAfter = "";
let dueBefore = "";
let typing = null;
const pageSize = 50;

function live(ok) {
  $("#live-dot").className = "dot" + (ok ? "" : " bad");
  $("#live-text").textContent = ok ? "live" : "reconnecting…";
}

/* Yield is good / (good + scrap). Unknown when nothing has been booked -
   a step that has not run is not a step that failed. */
function yieldOf(good, scrap) {
  const made = (good || 0) + (scrap || 0);
  return made > 0 ? (good || 0) / made : null;
}
function yieldCell(value) {
  if (value === null) return { text: "—", cls: "muted" };
  const pct = (value * 100).toFixed(1) + "%";
  if (value >= 0.98) return { text: pct, cls: "yield-good" };
  if (value >= 0.90) return { text: pct, cls: "yield-warn" };
  return { text: pct, cls: "yield-bad" };
}

function statusPill(status) {
  const map = { running: "running", released: "idle", planned: "unknown",
                completed: "running", closed: "unknown", cancelled: "down",
                // A hold is a planned stop, not a failure - same colour
                // family as a changeover for the same reason.
                on_hold: "setup" };
  return el("span", `pill ${map[status] || "unknown"}`, status.replace("_", " "));
}

const OPEN_STATUSES = ["planned", "released", "running", "on_hold"];

function dueCell(due, status) {
  if (!due) return el("td", "muted", "—");
  const when = new Date(due + (due.endsWith("Z") ? "" : "Z"));
  const late = OPEN_STATUSES.includes(status) && when < new Date();
  const cell = el("td", late ? "due late" : "due", when.toLocaleDateString());
  if (late) cell.title = "past due";
  return cell;
}

/* ---------- list ---------- */

function renderCount() {
  const shown = orders.length;
  $("#order-count").textContent = shown
    ? `· ${offset + 1}–${offset + shown} of ${fmt.qty(total)}`
    : "· none match";
  $("#page-where").textContent = "";
  $("#page-prev").disabled = offset === 0;
  $("#page-next").disabled = !hasMore;
}

function renderList() {
  const body = $("#order-list tbody");
  body.textContent = "";

  for (const o of orders) {
    const row = el("tr");
    if (o.code === selected) row.classList.add("selected");
    row.appendChild(el("td", "mono", o.code));
    row.appendChild(el("td", null, o.material));

    const st = el("td");
    st.appendChild(statusPill(o.status));
    row.appendChild(st);
    row.appendChild(dueCell(o.due_date, o.status));

    const done = Math.min(1, (o.good_qty || 0) / (o.quantity || 1));
    const prog = el("td");
    const bar = el("div", "progress");
    const fill = el("div", "bar");
    fill.style.width = (done * 100).toFixed(0) + "%";
    bar.appendChild(fill);
    prog.appendChild(bar);
    prog.appendChild(el("span", "muted small", (done * 100).toFixed(0) + "%"));
    row.appendChild(prog);

    row.appendChild(el("td", "num", fmt.qty(o.quantity)));
    row.appendChild(el("td", "num", fmt.qty(o.good_qty)));
    row.appendChild(el("td", "num", fmt.qty(o.scrap_qty)));

    row.addEventListener("click", () => { selected = o.code; renderList(); loadDetail(); });
    body.appendChild(row);
  }

  renderTiles();
  renderCount();
}

/* The tiles count the whole plant, not this page. They are also the status
   control, so each one admits when it is the active filter - the old ones
   gave no sign at all, which made clicking them feel like nothing. */
const TILE_VALUES = { "card-open": "released,running", "card-running": "running",
                      "card-done": "completed,closed" };

function renderTiles() {
  const by = summary && summary.by_status;
  const n = (...statuses) =>
    by ? statuses.reduce((a, s) => a + (by[s] || 0), 0).toLocaleString() : "—";

  $("#kpi-open").textContent = n("released", "running");
  $("#kpi-running").textContent = n("running");
  $("#kpi-done").textContent = n("completed", "closed");

  const current = filterStatus.join(",");
  for (const [id, value] of Object.entries(TILE_VALUES)) {
    $(`#${id}`).setAttribute("aria-pressed", String(value === current && !!current));
  }

  const cell = yieldCell(summary ? summary.yield : null);
  $("#kpi-yield").textContent = cell.text;
  $("#kpi-yield").className = `kpi-value ${cell.cls}`;
}

/* ---------- filters ---------- */

function setStatus(value) {
  $("#f-status").value = value;
  filterStatus = value ? value.split(",") : [];
  applyFilters();
}

function anyFilter() {
  return !!(filterStatus.length || filterText || filterMaterial || dueAfter || dueBefore);
}

function decorateFilterChrome() {
  $("#f-clear").classList.toggle("hidden", !anyFilter());

  const due = $("#due-toggle");
  if (dueAfter || dueBefore) {
    const shorten = (d) => (d ? new Date(d).toLocaleDateString(undefined,
      { month: "short", day: "numeric" }) : "…");
    due.textContent = `Due ${shorten(dueAfter)}–${shorten(dueBefore)}`;
    due.classList.add("filtering");
  } else {
    due.textContent = "Due ▾";
    due.classList.remove("filtering");
  }

  const st = $("#status-toggle");
  if (filterStatus.length) {
    const option = $(`#f-status option[value="${filterStatus.join(",")}"]`);
    st.textContent = option ? option.textContent : filterStatus.join(",");
    st.classList.add("filtering");
  } else {
    st.textContent = "Status ▾";
    st.classList.remove("filtering");
  }
}

function applyFilters() {
  offset = 0;
  selected = null;
  decorateFilterChrome();
  refresh();
}

function popover(toggleSel, popSel) {
  const toggle = $(toggleSel);
  const pop = $(popSel);
  toggle.addEventListener("click", () => {
    const open = pop.classList.toggle("hidden");
    toggle.setAttribute("aria-expanded", String(!open));
  });
  document.addEventListener("click", (event) => {
    if (!pop.contains(event.target) && event.target !== toggle) {
      pop.classList.add("hidden");
      toggle.setAttribute("aria-expanded", "false");
    }
  });
}

function wireFilters() {
  popover("#due-toggle", "#due-pop");
  popover("#status-toggle", "#status-pop");

  $("#f-status").addEventListener("change", (e) => setStatus(e.target.value));
  for (const [id, value] of Object.entries(TILE_VALUES)) {
    $(`#${id}`).addEventListener("click", () => {
      // Clicking the active tile clears it - a filter you can turn on with
      // one tap should turn off with the same tap.
      setStatus(filterStatus.join(",") === value ? "" : value);
    });
  }
  $("#f-material").addEventListener("change", (e) => {
    filterMaterial = e.target.value; applyFilters();
  });
  $("#f-due-after").addEventListener("change", (e) => {
    dueAfter = e.target.value; applyFilters();
  });
  $("#f-due-before").addEventListener("change", (e) => {
    dueBefore = e.target.value; applyFilters();
  });
  $("#f-text").addEventListener("input", (e) => {
    filterText = e.target.value.trim();
    clearTimeout(typing);
    typing = setTimeout(applyFilters, 300);
  });
  $("#f-clear").addEventListener("click", () => {
    filterStatus = [];
    filterText = filterMaterial = dueAfter = dueBefore = "";
    for (const sel of ["#f-status", "#f-material", "#f-text", "#f-due-after", "#f-due-before"]) {
      $(sel).value = "";
    }
    applyFilters();
  });
  $("#page-prev").addEventListener("click", () => {
    offset = Math.max(0, offset - pageSize); refresh();
  });
  $("#page-next").addEventListener("click", () => {
    if (hasMore) { offset += pageSize; refresh(); }
  });
}

/* ---------- creating an order ---------- */

function suggestCode() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `WO-${String(now.getFullYear()).slice(2)}${pad(now.getMonth() + 1)}`
       + `${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
}

function wireNewOrder() {
  const form = $("#new-order-form");
  $("#new-order").addEventListener("click", () => {
    $("#no-code").value = suggestCode();
    form.classList.toggle("hidden");
  });
  $("#no-cancel").addEventListener("click", () => form.classList.add("hidden"));
  $("#no-create").addEventListener("click", async () => {
    const code = $("#no-code").value.trim();
    const material = $("#no-material").value;
    const quantity = Number($("#no-qty").value);
    if (!code || !material || !(quantity > 0)) {
      FS.toast("An order needs a code, a material and a quantity.", "bad");
      return;
    }
    try {
      const body = { code, material, quantity };
      if ($("#no-due").value) body.due_date = `${$("#no-due").value}T23:59:59`;
      await api("/workorders", { method: "POST", body });
      if ($("#no-release").checked && FS.can("orders.release")) {
        await api(`/workorders/${code}/release`, { method: "POST" });
      }
      FS.toast(`${code} created`);
      form.classList.add("hidden");
      selected = code;
      await refresh();
    } catch (err) {
      FS.toast(err.message, "bad");
    }
  });
}

/* ---------- lifecycle actions ---------- */

function actionButton(label, cls, onClick) {
  const button = el("button", cls || "ghost", label);
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await onClick();
      await refresh();
    } catch (err) {
      FS.toast(err.message, "bad");
      button.disabled = false;
    }
  });
  return button;
}

function renderActions(order) {
  const box = $("#order-actions");
  box.textContent = "";
  const post = (path) => () => api(path, { method: "POST" }).then(() =>
    FS.toast(`${order.code}: done`));

  if (order.status === "planned" && FS.can("orders.release")) {
    box.appendChild(actionButton("Release", null,
      post(`/workorders/${order.code}/release`)));
  }
  if (["released", "running"].includes(order.status) && FS.can("orders.close")) {
    const hold = el("button", "ghost", "Hold…");
    hold.type = "button";
    hold.addEventListener("click", () => {
      $("#hold-reason").value = "";
      $("#hold-pop").classList.remove("hidden");
      $("#hold-reason").focus();
    });
    box.appendChild(hold);
  }
  if (order.status === "on_hold" && FS.can("orders.close")) {
    box.appendChild(actionButton("Resume", null,
      post(`/workorders/${order.code}/resume`)));
  }
  if (order.status === "completed" && FS.can("orders.close")) {
    box.appendChild(actionButton("Close", null,
      post(`/workorders/${order.code}/close`)));
  }
  if (OPEN_STATUSES.includes(order.status) && FS.can("orders.close")) {
    box.appendChild(actionButton("Cancel", "ghost danger", () => {
      if (!window.confirm(`Cancel ${order.code}? This is not a hold - it does not come back.`)) {
        return Promise.resolve();
      }
      return post(`/workorders/${order.code}/cancel`)();
    }));
  }
}

function wireHold() {
  $("#hold-cancel").addEventListener("click", () =>
    $("#hold-pop").classList.add("hidden"));
  $("#hold-confirm").addEventListener("click", async () => {
    const reason = $("#hold-reason").value.trim();
    if (!reason) {
      FS.toast("A hold needs a reason - the person resuming it has to know why.", "bad");
      return;
    }
    try {
      await api(`/workorders/${selected}/hold`, { method: "POST", body: { reason } });
      FS.toast(`${selected} held`);
      $("#hold-pop").classList.add("hidden");
      await refresh();
    } catch (err) {
      FS.toast(err.message, "bad");
    }
  });
}

/* ---------- detail ---------- */

async function loadDetail() {
  if (!selected) return;
  const [order, genealogy, promise, staging] = await Promise.all([
    api(`/workorders/${selected}`),
    api(`/execution/genealogy/${selected}`).catch(() => ({ consumed: [], produced: [] })),
    api(`/scheduling/promise/${selected}`).catch(() => null),
    api(`/execution/staging/${selected}`).catch(() => null),
  ]);

  $("#detail-empty").classList.add("hidden");
  $("#detail").classList.remove("hidden");
  $("#detail-code").textContent = order.code;
  $("#d-material").textContent = order.material;
  $("#d-qty").textContent = fmt.qty(order.quantity);
  $("#d-status").textContent = order.status.replace("_", " ");
  const y = yieldCell(yieldOf(order.good_qty, order.scrap_qty));
  $("#d-yield").textContent = y.text;
  $("#d-yield").className = y.cls;

  renderPromise(order, promise);
  // A completed order has a certificate; say so where the order is.
  const promiseNode = $("#d-promise");
  if (promiseNode && ["completed", "closed"].includes(order.status)) {
    promiseNode.append(" · ", FS.link("certificate", order.code, null, "certificate of analysis"));
  }
  renderActions(order);

  const ops = $("#ops tbody");
  ops.textContent = "";
  for (const op of order.operations || []) {
    const row = el("tr");
    row.appendChild(el("td", "num", op.seq));
    row.appendChild(el("td", null, op.name));
    const where = el("td", "mono");
    if (op.equipment) where.appendChild(FS.link("machine", op.equipment));
    row.appendChild(where);
    const st = el("td");
    st.appendChild(statusPill(op.status));
    row.appendChild(st);
    row.appendChild(el("td", "num", fmt.qty(op.good_qty)));
    row.appendChild(el("td", "num", fmt.qty(op.scrap_qty)));
    const cell = yieldCell(yieldOf(op.good_qty, op.scrap_qty));
    row.appendChild(el("td", `num ${cell.cls}`, cell.text));
    ops.appendChild(row);
  }

  renderStaging(order, staging);

  fillFeed("#consumed", genealogy.consumed, (c) => {
    const base = `${c.lot || c.code || "lot"} — ${fmt.qty(c.quantity)} ${c.material || ""}`
      + (c.equipment ? ` at ${c.equipment}` : "");
    return c.produced_by_order ? `${base}  ← made by ${c.produced_by_order}` : base;
  });
  fillFeed("#produced", genealogy.produced, (p) =>
    `${p.lot || p.code || "lot"} — ${fmt.qty(p.quantity)} ${p.material || ""}`);
}

function renderPromise(order, promise) {
  const node = $("#d-promise");
  if (!promise || !OPEN_STATUSES.includes(order.status)) {
    node.textContent = "—";
    node.className = "muted";
    return;
  }
  if (!promise.scheduled) {
    node.textContent = "not scheduled";
    node.className = "muted";
    return;
  }
  const finish = new Date(promise.finishes + (String(promise.finishes).endsWith("Z") ? "" : "Z"));
  const when = finish.toLocaleString(undefined,
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  if (promise.late) {
    node.textContent = `${when} — ${promise.late_by_hours}h late`;
    node.className = "yield-bad";
  } else {
    node.textContent = when;
    node.className = "yield-good";
  }
}

function renderStaging(order, staging) {
  const body = $("#staging tbody");
  const verdict = $("#staging-verdict");
  body.textContent = "";
  if (!staging || !OPEN_STATUSES.includes(order.status) || !staging.stations) {
    verdict.textContent = OPEN_STATUSES.includes(order.status)
      ? "" : "Finished orders have nothing left to stage.";
    return;
  }
  for (const station of staging.stations) {
    const where = station.seq
      ? `${station.seq} ${station.operation || ""} @ ${station.equipment || "?"}`
      : "unassigned";
    for (const c of station.components || []) {
      const row = el("tr");
      row.appendChild(el("td", "station", where));
      row.appendChild(el("td", null, c.component));
      row.appendChild(el("td", "num", fmt.qty(c.needed_for_remaining)));
      row.appendChild(el("td", "num", fmt.qty(c.issued)));
      row.appendChild(el("td", "num", fmt.qty(c.available_in_stock)));
      row.appendChild(el("td", c.will_run_out ? "short" : "muted",
        c.will_run_out ? `SHORT ${fmt.qty(c.short_by)}` : "ok"));
      body.appendChild(row);
    }
  }
  verdict.textContent = staging.verdict || "";
}

function fillFeed(sel, items, shape) {
  const list = $(sel);
  list.textContent = "";
  if (!items || !items.length) {
    list.appendChild(el("li", "muted", "nothing recorded"));
    return;
  }
  for (const item of items) {
    const li = el("li");
    li.appendChild(el("span", null, shape(item)));
    if (item.ts) li.appendChild(el("span", "muted small", fmt.stamp(item.ts)));
    list.appendChild(li);
  }
}

/* Materials feed both the filter and the create form. Master data changes
   when somebody adds a product, not every four seconds. */
async function loadMaterials() {
  try {
    const materials = await api("/masterdata/materials");
    for (const target of ["#f-material", "#no-material"]) {
      const select = $(target);
      if (!select) continue;
      for (const m of materials) {
        const option = el("option", null, m.name ? `${m.code} — ${m.name}` : m.code);
        option.value = m.code;
        select.appendChild(option);
      }
    }
  } catch (err) {
    /* typing a code into the search box still works */
  }
}

async function refresh() {
  try {
    const query = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
    for (const one of filterStatus) query.append("status", one);
    if (filterText) query.set("q", filterText);
    if (filterMaterial) query.set("material", filterMaterial);
    if (dueAfter) query.set("due_after", dueAfter);
    if (dueBefore) query.set("due_before", `${dueBefore}T23:59:59`);
    const [paged, totals] = await Promise.all([
      api(`/workorders?${query}`),
      api("/workorders/summary").catch(() => null),
    ]);
    summary = totals;
    orders = paged.items;
    total = paged.total;
    hasMore = paged.has_more;
    if (!selected && orders.length) {
      const active = orders.find((o) => ["running", "released"].includes(o.status));
      selected = (active || orders[0]).code;
    }
    renderList();
    window.__fsmesPageData = {
      orders, total,
      filters: { filterStatus, filterText, filterMaterial, dueAfter, dueBefore, offset },
    };
    if (selected) await loadDetail();
    live(true);
  } catch (err) {
    live(false);
  }
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  wireFilters();
  wireNewOrder();
  wireHold();
  await loadMaterials();
  await refresh();
  setInterval(refresh, REFRESH_MS);
})();
