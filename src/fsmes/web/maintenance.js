/* The Maintenance workspace.

   Four questions a maintenance lead asks every shift: what has come due,
   what is open and what will it cost to clear, what plans exist, and what
   was done. Due-ness comes from the machine's own use, which is why the
   reason on every job reads "212 h against a 200 h plan" rather than a
   date. Everything here is the same API the machine page's Maintenance tab
   and the agent's maintenance tools use. */

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

function bar(fraction) {
  const wrap = el("div", "bar");
  const fill = el("i");
  fill.style.width = `${Math.min(100, fraction * 100)}%`;
  if (fraction >= 1) fill.style.background = "var(--down)";
  else if (fraction >= 0.8) fill.style.background = "var(--idle)";
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
    (p.due ? " — due" : p.due_soon ? " — due soon" : "") +
    ` · ~${fmt.qty(p.expected_minutes)} min down` + (p.document ? ` · ${p.document}` : "")));
  what.append(bar(p.fraction));
  li.append(what);
  return li;
}

async function loadDue() {
  const d = await api("/maintenance/due");
  $("#kpi-due").textContent = d.due.length;
  $("#kpi-soon").textContent = d.due_soon.length;
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
    await Promise.all([loadDue(), loadWork()]);
  } catch (err) { toast(err.message, "bad"); }
}

/* ---------- work ---------- */

async function loadWork() {
  // The server's answer for open work, not "open" picked out of the newest
  // two hundred orders: a year in, the open ones are not among those.
  const machine = $("#work-machine").value;
  const page = await api(`/maintenance/orders?status=due&status=in_progress&limit=200${machine ? `&equipment=${encodeURIComponent(machine)}` : ""}`);
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
    what.append(el("div", "muted small", o.summary + (o.reason ? ` — ${o.reason}` : "") +
      (o.started_at ? ` · started ${fmt.clock(o.started_at)} by ${o.performed_by || "?"}` : ` · raised ${fmt.stamp(o.raised_at)}`)));
    li.append(what);
    if (FS.can("maintenance.perform")) {
      if (o.status === "due") {
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
      await Promise.all([loadWork(), loadDue()]);
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
      await Promise.all([loadWork(), loadDue()]);
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
    const prog = el("td"); prog.append(bar(p.fraction)); tr.append(prog);
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
        interval: Number($("#plan-interval").value), expected_minutes: Number($("#plan-minutes").value || 30),
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

const LOADERS = { due: loadDue, work: loadWork, plans: loadPlans, history: loadHistory };

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
  await loadDue();
  FS.tabs.init(document, onTab);
})().catch(fail);
