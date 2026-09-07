/* Orders › Schedule.

   The board is each machine with time across and the maintenance it owes
   placed before the work. Promises are what the plan says about every open
   order's due date - the most useful thing a scheduler produces, and the
   thing a spreadsheet never says out loud. The calendar is what both rest
   on: a due date that counts hours the plant is dark is not a date. */

const { $, el, api, fmt, toast, kit } = window.FS;

const REFRESH_MS = 15000;
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

/* ---------- board ---------- */

async function loadBoard() {
  const hours = $("#horizon").value;
  const b = await api(`/scheduling/board?hours=${hours}`);
  $("#kpi-booked").textContent = `${(b.booked_minutes / 60).toFixed(1)} h`;
  $("#kpi-horizon").textContent = `in the next ${hours} h`;
  $("#kpi-maint").textContent = `${(b.maintenance_minutes / 60).toFixed(1)} h`;
  // The board is a timeline with two states: production and the maintenance
  // placed before it. Drawn by the kit so it looks like every other timeline.
  const machines = b.machines.map((m) => ({
    code: m.equipment,
    intervals: m.slots.map((s) => ({
      state: s.kind === "maintenance" ? "setup" : "running",
      reason: s.kind === "maintenance" ? s.what : `${s.order} op ${s.seq} ${s.what || ""}`,
      start: s.planned_start, end: s.planned_end, seconds: s.minutes * 60,
    })),
  }));
  kit.timeline($("#board"), { window: { start: b.from, end: b.to }, machines },
               { emptyText: "Nothing is scheduled in this window. Plan the open orders to fill the board." });
  window.__fsmesPageData = { board: b };
}

function wirePlan() {
  $("#plan-all").addEventListener("click", async () => {
    try {
      const out = await api("/scheduling/plan", { method: "POST", body: {} });
      toast(`Planned ${out.planned} order(s).`);
      await Promise.all([loadBoard(), loadPromises()]);
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#plan-one").addEventListener("click", async () => {
    const code = $("#plan-order").value.trim().toUpperCase();
    if (!code) return;
    try {
      const out = await api(`/scheduling/plan/${encodeURIComponent(code)}`, { method: "POST", body: {} });
      toast(`${out.order}: finishes ${fmt.stamp(out.finishes)}`);
      await Promise.all([loadBoard(), loadPromises()]);
    } catch (err) { toast(err.message, "bad"); }
  });
}

/* ---------- promises ---------- */

async function loadPromises() {
  const page = await api("/workorders?status=released&status=running&status=planned&limit=50");
  const orders = page.items || [];
  const promises = await Promise.all(orders.map((o) => api(`/scheduling/promise/${encodeURIComponent(o.code)}`).catch(() => null)));
  const body = $("#promises-table tbody");
  body.replaceChildren();
  let late = 0, unscheduled = 0;
  orders.forEach((o, i) => {
    const p = promises[i];
    const tr = el("tr");
    tr.append(el("td", "code", o.code), el("td", "code", o.material));
    const st = el("td"); st.append(el("span", `pill ${o.status}`, o.status)); tr.append(st);
    tr.append(el("td", "muted small", o.due_date ? fmt.stamp(o.due_date) : "—"));
    if (!p || !p.scheduled) {
      unscheduled += 1;
      tr.append(el("td", "muted", "not scheduled"), el("td", "muted", "—"));
    } else {
      tr.append(el("td", "small", fmt.stamp(p.finishes)));
      if (p.late) { late += 1; tr.append(el("td", "alarm", `late by ${p.late_by_hours} h`)); }
      else tr.append(el("td", null, p.due ? "on time" : "no due date to miss"));
    }
    body.append(tr);
  });
  $("#promise-count").textContent = `— ${orders.length} open order(s)`;
  $("#kpi-promised").textContent = orders.length - unscheduled;
  $("#kpi-unscheduled").textContent = unscheduled ? `${unscheduled} not scheduled` : "";
  $("#kpi-late").textContent = late;
}

/* ---------- calendar ---------- */

async function loadCalendar() {
  const c = await api("/scheduling/calendar");
  $("#calendar-note").textContent = c.note || `${c.shifts.length} shift pattern(s).`;
  const shifts = $("#shifts-table tbody");
  shifts.replaceChildren();
  for (const s of c.shifts) {
    const tr = el("tr");
    tr.append(el("td", "code", `${s.code} ${s.name}`), el("td", null, s.starts),
              el("td", null, s.ends + (s.crosses_midnight ? " (next day)" : "")),
              el("td", "muted", s.days.join(" ")), el("td", "muted", s.equipment || "whole plant"));
    shifts.append(tr);
  }
  const exc = $("#exceptions-table tbody");
  exc.replaceChildren();
  if (!c.exceptions.length) {
    const tr = el("tr"); const td = el("td", "muted", "No exceptions."); td.colSpan = 4; tr.append(td); exc.append(tr);
  }
  for (const e of c.exceptions) {
    const tr = el("tr");
    tr.append(el("td", "code", e.day), el("td", null, e.kind), el("td", null, e.reason),
              el("td", "muted", e.equipment || "whole plant"));
    exc.append(tr);
  }
}

function wireCalendar() {
  $("#shift-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/scheduling/calendar/shifts", { method: "POST", body: {
        code: $("#shift-code").value.trim().toUpperCase(), name: $("#shift-name").value.trim(),
        starts: $("#shift-starts").value, ends: $("#shift-ends").value, days: $("#shift-days").value.trim() } });
      toast("Shift added.");
      $("#shift-form").reset();
      await loadCalendar();
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#exception-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/scheduling/calendar/exceptions", { method: "POST", body: {
        day: $("#exc-day").value, kind: $("#exc-kind").value, reason: $("#exc-reason").value.trim() } });
      toast("Exception added.");
      $("#exception-form").reset();
      await loadCalendar();
    } catch (err) { toast(err.message, "bad"); }
  });
}

/* ---------- tabs ---------- */

const LOADERS = { board: loadBoard, promises: loadPromises, calendar: loadCalendar };

function onTab(name) {
  clearInterval(timer);
  const run = () => (LOADERS[name] || loadBoard)().then(() => live(true)).catch(fail);
  run();
  timer = setInterval(run, REFRESH_MS);
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  $("#horizon").addEventListener("change", () => loadBoard().catch(fail));
  wirePlan();
  wireCalendar();
  await loadPromises().catch(() => {});
  FS.tabs.init(document, onTab);
})().catch(fail);
