/* One machine, everything it says.

   The first object page. Its head is where the machine sits and what it is
   doing; its tabs are the questions people ask of a machine - what is it
   reporting right now, how has a signal moved, when was it down, how well
   did it run, what does it owe maintenance, what is queued on it, and what
   it counted with nothing queued at all.

   Every number on this page came over OPC UA or was booked against this
   machine; the footer names the endpoint behind each tab. */

const { $, el, api, fmt, kit } = window.FS;

const CODE = decodeURIComponent(location.pathname.split("/").pop());
const NOW_REFRESH_MS = 3000;

let head = null;
let snapshot = null;
let trendTag = new URL(location).searchParams.get("tag");
let nowTimer = null;

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

function hours() { return FS.window.hours(); }

/* ---------- head ---------- */

function renderHead(h) {
  head = h;
  document.title = `${h.code} — FactorySemantics MES`;
  $("#m-code").textContent = h.code;
  $("#m-name").textContent = h.name;
  const pill = $("#m-state");
  pill.className = `pill ${h.state}`;
  pill.textContent = h.state;
  $("#m-since").textContent = h.since
    ? `since ${fmt.clock(h.since)}` + (h.reason ? ` — ${h.reason}` : "") : "";
  $("#m-order").textContent = h.current_order || "—";
  $("#m-op").textContent = h.current_operation ? `${h.current_operation} (op ${h.current_seq})` : "—";
  $("#m-cc").textContent = h.cost_center || "—";
  $("#m-cycle").textContent = h.ideal_cycle_seconds ? `${h.ideal_cycle_seconds} s` : "—";

  const crumbs = $("#crumbs");
  crumbs.replaceChildren();
  for (const node of h.path) {
    // A line opens its Line page; everything above it opens the Machines
    // tree scoped to that node - every level of the path is somewhere.
    const link = node.level === "work_center"
      ? FS.link("line", node.code, null, node.name || node.code)
      : FS.link("equipment", node.code, null, node.name || node.code);
    link.title = `${node.level.replace("_", " ")} ${node.code}`;
    crumbs.append(link, el("span", "sep", "›"));
  }
  crumbs.append(el("span", "mono", h.code));
  $("#operate-link").href = `/dashboard/station?m=${encodeURIComponent(h.code)}`;
}

/* ---------- now: tags and alarms ---------- */

function valueText(t) {
  if (t.value === null || t.value === undefined) return "—";
  if (typeof t.value === "number") {
    if (t.kind === "counter" || t.kind === "state" || t.kind === "alarm") return Math.round(t.value).toLocaleString();
    return fmt.qty(t.value);
  }
  return String(t.value);
}

function renderTags(snap) {
  snapshot = snap;
  const byName = Object.fromEntries(snap.tags.map((t) => [t.tag, t]));
  const rows = snap.tags.filter((t) => t.kind !== "sp");   // setpoints ride beside what they drive
  const body = $("#tags-table tbody");
  body.replaceChildren();
  for (const t of rows) {
    const tr = el("tr", t.stale ? "stale" : null);
    tr.dataset.tag = t.tag;
    const name = el("td", "code");
    name.append(t.tag);
    if (t.primary) name.append(" ", el("span", "pill", "primary"));
    if (t.note) name.title = t.note;
    tr.append(name);
    tr.append(el("td", "num mono", valueText(t)));
    tr.append(el("td", "muted", t.unit || ""));

    const sp = Object.values(byName).find((s) => s.kind === "sp" && s.drives === t.tag) || (t.follows ? byName[t.follows] : null);
    const spCell = el("td", "muted");
    if (sp) {
      spCell.className = "mono";
      spCell.textContent = `${valueText(sp)}${sp.unit ? " " + sp.unit : ""}`;
      spCell.title = `${sp.tag}: ${sp.min ?? "?"} – ${sp.max ?? "?"}${sp.writable ? " · writable" : ""}`;
      if (sp.writable) spCell.append(" ", el("span", "pill", "writable"));
    } else {
      spCell.textContent = "";
    }
    tr.append(spCell);
    tr.append(el("td", "muted", t.kind || "—"));
    const ageCell = el("td", "muted small", kit.age(t.age_seconds));
    if (t.stale) ageCell.append(" ", el("span", "pill down", "stale"));
    tr.append(ageCell);
    tr.addEventListener("click", () => {
      if (typeof t.value !== "number") return;
      trendTag = t.tag;
      location.hash = "trend";
    });
    body.append(tr);
  }
  $("#tag-count").textContent = `— ${rows.length} of ${snap.tags.length} (from ${snap.source})`;

  const alarmTag = snap.tags.find((t) => t.kind === "alarm" || t.tag === "AlarmWord");
  const list = $("#alarms");
  list.replaceChildren();
  const active = alarmTag ? alarmTag.active || [] : [];
  if (!alarmTag) {
    list.append(el("li", "muted", "This machine publishes no alarm word."));
    $("#alarm-count").textContent = "";
  } else if (!active.length) {
    list.append(el("li", "muted", `No alarm bits set (word ${valueText(alarmTag)}, ${kit.age(alarmTag.age_seconds)} ago).`));
    $("#alarm-count").textContent = "— none";
  } else {
    for (const name of active) list.append(el("li", "alarm", name));
    $("#alarm-count").textContent = `— ${active.length} active`;
  }

  // The trend picker offers every numeric tag, primary first.
  const picker = $("#trend-tag");
  const numeric = snap.tags.filter((t) => typeof t.value === "number" || t.kind === "pv" || t.kind === "sp");
  if (!trendTag || !numeric.some((t) => t.tag === trendTag)) {
    trendTag = (numeric.find((t) => t.primary) || numeric[0] || {}).tag || null;
  }
  picker.replaceChildren(...numeric.map((t) => new Option(t.tag + (t.unit ? ` (${t.unit})` : ""), t.tag, false, t.tag === trendTag)));
}

function renderAlarmHistory(back) {
  // Every change of the alarm word in the window, newest first: the question
  // a failed check asks an hour later is "what did this machine raise?".
  const list = $("#alarm-history");
  list.replaceChildren();
  const all = (back.history || []).length;
  const changes = (back.history || []).slice().reverse().slice(0, 30);
  if (!changes.length) {
    list.append(el("li", "muted", `No alarm changes in the last ${back.hours} h.`));
    $("#alarm-history-count").textContent = "";
    return;
  }
  for (const h of changes) {
    const when = new Date(h.ts + "Z").toLocaleTimeString();
    const what = h.active.length ? h.active.join(", ") : "cleared";
    list.append(el("li", h.active.length ? "alarm" : "muted", `${when} — ${what} (word ${h.word})`));
  }
  // The newest thirty are drawn; the count says how many there were.
  $("#alarm-history-count").textContent = all > changes.length
    ? `— newest ${changes.length} of ${all.toLocaleString()} changes`
    : `— ${changes.length} change${changes.length === 1 ? "" : "s"}`;
}

async function refreshNow() {
  try {
    const [h, snap, back] = await Promise.all([
      api(`/equipment/${CODE}`), api(`/equipment/${CODE}/tags`),
      api(`/equipment/${CODE}/alarms?hours=${hours()}`),
    ]);
    renderHead(h);
    renderTags(snap);
    renderAlarmHistory(back);
    window.__fsmesPageData = { head: h, tags: snap, alarms: back };
    live(true);
  } catch (err) {
    fail(err);
  }
}

/* ---------- trend ---------- */

async function loadTrend() {
  if (!trendTag) { kit.empty($("#trend"), "No numeric tag to trend."); return; }
  $("#trend-tag").value = trendTag;
  const meta = snapshot ? snapshot.tags.find((t) => t.tag === trendTag) : null;
  const data = await api(`/analysis/tag/${encodeURIComponent(CODE)}?tag=${encodeURIComponent(trendTag)}&hours=${hours()}`);
  kit.trend($("#trend"), data, {
    label: `${trendTag}${meta && meta.unit ? ` (${meta.unit})` : ""}`,
    limits: meta ? { min: meta.min, max: meta.max } : {},
  });
}

/* ---------- timeline / oee ---------- */

async function loadTimeline() {
  const data = await api(`/analysis/timeline?equipment=${encodeURIComponent(CODE)}&hours=${hours()}&limit=1`);
  kit.timeline($("#tl"), data, { rowHeight: 34 });
}

async function loadOee() {
  const oee = await api(`/equipment/${CODE}/oee?hours=${hours()}`);
  kit.oeeBars($("#oee-bars"), oee);
  $("#oee-window").textContent = `— ${oee.window_hours.toFixed(2)} h observed`;
  $("#oee-facts").textContent =
    `${fmt.qty(oee.good_qty)} good, ${fmt.qty(oee.scrap_qty)} scrap · running ${kit.duration(oee.runtime_seconds)}, down ${kit.duration(oee.downtime_seconds)}`;
}

/* ---------- maintenance ---------- */

async function loadMaintenance() {
  const [plans, orders] = await Promise.all([
    api("/maintenance/plans").catch(() => []),
    api(`/maintenance/orders?equipment=${encodeURIComponent(CODE)}&limit=20`).then((p) => p.items).catch(() => []),
  ]);
  const mine = plans.filter((p) => p.equipment === CODE);
  const planList = $("#maint-plans");
  planList.replaceChildren();
  if (!mine.length) planList.append(el("li", "muted", "No maintenance plan names this machine."));
  for (const p of mine) {
    const li = el("li", p.due ? "running-op" : null);
    const what = el("div", "what");
    what.append(el("div", "mono", `${p.code} · ${p.name || ""}`));
    what.append(el("div", "muted small", p.reason || p.status || ""));
    li.append(what);
    planList.append(li);
  }
  const orderList = $("#maint-orders");
  orderList.replaceChildren();
  if (!orders.length) orderList.append(el("li", "muted", "No maintenance work recorded on this machine."));
  for (const o of orders) {
    const li = el("li", o.status === "in_progress" ? "running-op" : null);
    const what = el("div", "what");
    what.append(el("div", "mono", `${o.code} · ${o.kind} · ${o.status}`));
    what.append(el("div", "muted small", `${o.summary || ""}${o.findings ? ` — ${o.findings}` : ""} · ${fmt.stamp(o.completed_at || o.started_at || o.raised_at)}`));
    li.append(what);
    if (FS.can("maintenance.perform") && o.status !== "done") {
      const action = o.status === "due" ? "start" : "complete";
      const btn = el("button", "ghost", action === "start" ? "Start" : "Complete");
      btn.type = "button";
      btn.addEventListener("click", async () => {
        try {
          const body = action === "complete" ? { findings: window.prompt("What was found / done?") || null } : undefined;
          await api(`/maintenance/orders/${o.code}/${action}`, { method: "POST", body });
          FS.toast(`${o.code} ${action === "start" ? "started" : "complete"}`);
          await loadMaintenance();
        } catch (err) { FS.toast(err.message, "bad"); }
      });
      li.append(btn);
    }
    orderList.append(li);
  }
}

function wireCorrective() {
  $("#corrective").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api("/maintenance/corrective", { method: "POST", body: {
        equipment: CODE, summary: $("#cm-summary").value.trim(), reason: $("#cm-reason").value.trim() || null } });
      FS.toast(`${out.code} raised`);
      $("#cm-summary").value = ""; $("#cm-reason").value = "";
      await loadMaintenance();
    } catch (err) { FS.toast(err.message, "bad"); }
  });
}

/* ---------- operate (read-only here) ---------- */

async function loadQueue() {
  const rows = await api(`/workorders/dispatch?equipment=${encodeURIComponent(CODE)}`);
  const list = $("#queue");
  list.replaceChildren();
  const open = rows.filter((r) => r.status !== "done");
  if (!open.length) list.append(el("li", "muted", "Nothing queued on this machine."));
  for (const r of open) {
    const li = el("li", r.status === "running" ? "running-op" : null);
    const what = el("div", "what");
    what.append(el("div", "mono", `${r.order} · op ${r.seq}`));
    what.append(el("div", "muted small", `${r.operation} — ${fmt.qty(r.good_qty)} of ${fmt.qty(r.quantity)} good`));
    li.append(what);
    list.append(li);
  }
}

async function loadUnassigned() {
  const body = await api(`/execution/unassigned?equipment=${encodeURIComponent(CODE)}&limit=20`);
  const list = $("#unassigned");
  list.replaceChildren();
  // The count states the whole selection, not the page: "showing 20 of 63"
  // is the sentence that stops a page reading as the whole story.
  $("#unassigned-count").textContent = body.total
    ? `${fmt.qty(body.good_total)} good · ${fmt.qty(body.scrap_total)} scrap `
      + `in ${body.total} booking${body.total === 1 ? "" : "s"}`
      + (body.has_more ? ` — showing the latest ${body.items.length}` : "")
    : "";
  if (!body.total) {
    list.append(el("li", "muted", "Nothing counted here without an order."));
    return;
  }
  for (const row of body.items) {
    const li = el("li");
    const what = el("div", "what");
    what.append(el("div", "mono", `${fmt.qty(row.good)} good · ${fmt.qty(row.scrap)} scrap`));
    // Where the number came from, on the row with the number. A count this
    // machine made and a count another system says it made are different
    // claims, and the list is unreadable if it cannot show which is which.
    const from = row.source_system ? `told by ${row.source_system}` : `counted here (${row.source})`;
    what.append(el("div", "muted small", `${fmt.clock(row.ts)} — no order · ${from}`));
    li.append(what);
    list.append(li);
  }
}

async function loadOperate() {
  await Promise.all([loadQueue(), loadUnassigned()]);
}

/* ---------- tabs ---------- */

const LOADERS = { trend: loadTrend, timeline: loadTimeline, oee: loadOee, maintenance: loadMaintenance, operate: loadOperate };

function onTab(name) {
  clearInterval(nowTimer);
  if (name === "now") {
    refreshNow();
    nowTimer = setInterval(refreshNow, NOW_REFRESH_MS);
    return;
  }
  (LOADERS[name] || (() => Promise.resolve()))().catch(fail);
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  wireCorrective();
  FS.window.bind($("#hours"), () => onTab(FS.tabs.current()));
  $("#trend-tag").addEventListener("change", (e) => { trendTag = e.target.value; loadTrend().catch(fail); });
  await refreshNow();
  FS.tabs.init(document, onTab);
})();
