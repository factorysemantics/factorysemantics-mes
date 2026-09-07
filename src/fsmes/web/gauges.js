/* Quality › Gauges: the measurement system's own health.

   A control chart is only as good as the instrument behind it. The register
   says which gauges are in calibration, a calibration found out of
   tolerance takes the gauge off the floor and lists every measurement it
   invalidated, and the rule of ten says whether a gauge can judge a
   tolerance at all. No open-source MES does this; it is the thing the
   landscape review found missing everywhere. */

const { $, el, api, fmt, toast } = window.FS;

const REFRESH_MS = 15000;
let register = null;
let selected = null;

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

function statusPill(g) {
  if (g.status === "out_of_service" || g.status === "lost") return el("span", "pill down", g.status.replace("_", " "));
  if (g.overdue) return el("span", "pill down", g.never_calibrated ? "never calibrated" : "overdue");
  if (g.days_until_due !== null && g.days_until_due <= 30) return el("span", "pill idle", `due in ${g.days_until_due} d`);
  return el("span", "pill running", "in calibration");
}

const GAUGE_PAGE = 25;
let gaugeOffset = 0;

function gaugeState(g) {
  if (["out_of_service", "lost"].includes(g.status)) return "off_floor";
  if (g.overdue) return "overdue";
  if (g.days_until_due !== null && g.days_until_due <= 30) return "due_soon";
  return "ok";
}

async function loadRegister() {
  register = await api("/quality/gauges");
  drawRegister();
}

function drawRegister() {
  // The tiles and the verdict are the whole register's; the table is the
  // filtered page of it.
  const q = $("#g-q").value.trim().toLowerCase();
  const state = $("#g-state").value;
  const matching = register.gauges.filter((g) =>
    (!state || gaugeState(g) === state)
    && (!q || `${g.code} ${g.name} ${g.location || ""}`.toLowerCase().includes(q)));
  const page = FS.clientPage(matching, gaugeOffset, GAUGE_PAGE);
  gaugeOffset = page.offset;
  const body = $("#gauges-table tbody");
  body.replaceChildren();
  if (!page.items.length) {
    const tr = el("tr"); const td = el("td", "muted", register.gauges.length ? "No gauge matches." : "No gauges registered."); td.colSpan = 7; tr.append(td); body.append(tr);
  }
  for (const g of page.items) {
    const tr = el("tr", g.code === selected ? "selected" : null);
    tr.append(el("td", "code", `${g.code} ${g.name}`), el("td", "muted", g.kind), el("td", "muted", g.location || ""));
    const st = el("td"); st.append(statusPill(g)); tr.append(st);
    tr.append(el("td", "num", g.resolution === null ? "—" : String(g.resolution)));
    tr.append(el("td", "muted small", g.last_calibrated || "never"));
    tr.append(el("td", "muted small", g.due_on || "—"));
    tr.addEventListener("click", () => { selected = g.code; loadImpact().catch(fail); drawRegister(); });
    body.append(tr);
  }
  $("#register-count").textContent = FS.countText(page, register.gauges.length, "registered");
  FS.pager($("#g-pager"), page, (offset) => { gaugeOffset = offset; drawRegister(); });
  $("#kpi-gauges").textContent = register.gauges.length;
  $("#kpi-overdue").textContent = register.overdue;
  $("#kpi-verdict").textContent = register.verdict;
  $("#kpi-soon").textContent = register.gauges.filter((g) => !g.overdue && g.days_until_due !== null && g.days_until_due <= 30).length;
  $("#kpi-oos").textContent = register.gauges.filter((g) => ["out_of_service", "lost"].includes(g.status)).length;
  const select = $("#cal-gauge");
  const keep = select.value;
  select.replaceChildren(...register.gauges.map((g) => new Option(`${g.code} — ${g.name}`, g.code)));
  if ([...select.options].some((o) => o.value === keep)) select.value = keep;
  window.__fsmesPageData = register;
}

async function loadImpact() {
  if (!selected) return;
  const i = await api(`/quality/gauges/${encodeURIComponent(selected)}/impact`);
  $("#impact-title").textContent = `— ${selected}`;
  $("#impact").textContent = `${i.count} measurement(s)${i.since ? ` since ${i.since}` : ""}` +
    (i.orders.length ? ` on orders ${i.orders.join(", ")}` : "") + ` — ${i.note}`;
}

function wire() {
  $("#cal-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api(`/quality/gauges/${encodeURIComponent($("#cal-gauge").value)}/calibrate`, { method: "POST", body: {
        result: $("#cal-result").value, performed_by: $("#cal-by").value.trim(),
        performed_on: $("#cal-on").value || null, certificate: $("#cal-cert").value.trim() || null } });
      toast(`${out.gauge}: ${out.result} → ${out.status.replace("_", " ")}`);
      const box = $("#cal-result-box");
      box.classList.remove("hidden");
      $("#cal-result-text").textContent = `${out.gauge} ${out.result}: now ${out.status.replace("_", " ")}, next due ${out.next_due || "—"}.`;
      const list = $("#cal-suspect");
      list.replaceChildren();
      if (out.suspect_measurements) {
        const s = out.suspect_measurements;
        list.append(el("li", "alarm", `${s.count} suspect measurement(s)${s.orders.length ? ` on ${s.orders.join(", ")}` : ""} — ${s.note}`));
      }
      await loadRegister();
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#reg-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api("/quality/gauges", { method: "POST", body: {
        code: $("#reg-code").value.trim().toUpperCase(), name: $("#reg-name").value.trim(),
        kind: $("#reg-kind").value.trim() || "general", interval_days: Number($("#reg-interval").value || 365),
        resolution: $("#reg-resolution").value ? Number($("#reg-resolution").value) : null,
        location: $("#reg-location").value.trim() || null } });
      toast(`${out.code} registered`);
      $("#reg-form").reset();
      await loadRegister();
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#res-check").addEventListener("click", async () => {
    const tol = $("#res-tolerance").value;
    if (!selected || !tol) { $("#res-verdict").textContent = "pick a gauge and a tolerance"; return; }
    try {
      const r = await api(`/quality/gauges/${encodeURIComponent(selected)}/resolution?tolerance=${encodeURIComponent(tol)}`);
      $("#res-verdict").textContent = r.ratio !== undefined
        ? `ratio ${r.ratio}:1 — ${r.adequate ? "adequate (rule of ten)" : "not adequate: the chart would be charting the instrument"}`
        : r.verdict;
    } catch (err) { $("#res-verdict").textContent = err.message; }
  });
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  wire();
  for (const id of ["g-q", "g-state"]) $(`#${id}`).addEventListener("input", () => { gaugeOffset = 0; if (register) drawRegister(); });
  const run = () => loadRegister().then(() => live(true)).catch(fail);
  await run();
  setInterval(run, REFRESH_MS);
})().catch(fail);
