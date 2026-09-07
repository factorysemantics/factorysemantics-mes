/* Engineering › Triggers: what the plant does when a signal crosses a line.

   A trigger is data - tag, condition, threshold, how long it must hold,
   then one action from the catalog - with the draft → approved lifecycle
   work instructions have. This screen drafts, approves and withdraws them
   and shows every firing, because a trigger nobody can see fire is a
   trigger nobody trusts. */

const { $, el, api, fmt, toast, kit } = window.FS;

const REFRESH_MS = 10000;
let catalog = null;
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

function conditionText(t) {
  const words = { above: ">", below: "<", equals: "=", bit_set: "bit set", rises_above: "rises above", falls_below: "falls below" };
  return `${t.tag} ${words[t.condition] || t.condition} ${fmt.qty(t.threshold)}` +
    (t.sustained_seconds ? ` for ${kit.duration(t.sustained_seconds)}` : "") +
    (t.cooldown_seconds ? ` · quiet ${kit.duration(t.cooldown_seconds)}` : "");
}

const TRIGGER_PAGE = 25;
const FIRING_PAGE = 50;
let triggers = [];
let triggerOffset = 0;
let firingOffset = 0;

async function loadTriggers() {
  const [rows, fired] = await Promise.all([api("/triggers"), api("/triggers/firings?hours=24&limit=1")]);
  triggers = rows;
  drawTriggers();
  $("#kpi-fired").textContent = fired.total.toLocaleString();
  window.__fsmesPageData = { triggers: rows, fired_24h: fired.total };
}

function drawTriggers() {
  const q = $("#tr-q").value.trim().toLowerCase();
  const status = $("#tr-status").value;
  const machine = $("#tr-machine").value;
  const matching = triggers.filter((t) =>
    (!status || t.status === status)
    && (!machine || t.equipment === machine || !t.equipment)
    && (!q || `${t.code} ${t.name} ${t.tag}`.toLowerCase().includes(q)));
  const page = FS.clientPage(matching, triggerOffset, TRIGGER_PAGE);
  triggerOffset = page.offset;
  const rows = page.items;
  const body = $("#triggers-table tbody");
  body.replaceChildren();
  if (!rows.length) { const tr = el("tr"); const td = el("td", "muted", triggers.length ? "No trigger matches." : "No triggers yet. Draft one above."); td.colSpan = 8; tr.append(td); body.append(tr); }
  for (const t of rows) {
    const tr = el("tr");
    tr.append(el("td", "code", `${t.code} ${t.name}`));
    const where = el("td"); where.append(t.equipment ? FS.link("machine", t.equipment) : el("span", "muted", "any machine")); tr.append(where);
    tr.append(el("td", "small", conditionText(t)));
    tr.append(el("td", "small", t.action + (Object.keys(t.action_params || {}).length ? ` ${JSON.stringify(t.action_params)}` : "")));
    const st = el("td"); st.append(el("span", `pill ${t.status === "approved" ? "running" : t.status === "draft" ? "planned" : "down"}`, t.status)); tr.append(st);
    tr.append(el("td", "num", String(t.fire_count)));
    tr.append(el("td", "muted small", t.last_fired_at ? fmt.stamp(t.last_fired_at) : "never"));
    const actions = el("td");
    if (FS.can("triggers.approve")) {
      if (t.status === "draft") {
        const b = el("button", "small", "Approve"); b.type = "button";
        b.addEventListener("click", async () => { try { await api(`/triggers/${t.code}/approve`, { method: "POST" }); toast(`${t.code} in force`); await loadTriggers(); } catch (err) { toast(err.message, "bad"); } });
        actions.append(b);
      }
      if (t.status !== "withdrawn") {
        const w = el("button", "ghost small", "Withdraw"); w.type = "button";
        w.addEventListener("click", async () => { try { await api(`/triggers/${t.code}/withdraw`, { method: "POST" }); toast(`${t.code} withdrawn`); await loadTriggers(); } catch (err) { toast(err.message, "bad"); } });
        actions.append(" ", w);
      }
    }
    tr.append(actions);
    body.append(tr);
  }
  $("#trigger-count").textContent = FS.countText(page, triggers.length);
  FS.pager($("#tr-pager"), page, (offset) => { triggerOffset = offset; drawTriggers(); });
  // The tiles count the plant's triggers, never the page's.
  const approved = triggers.filter((t) => t.status === "approved");
  $("#kpi-approved").textContent = approved.length;
  $("#kpi-drafts").textContent = triggers.filter((t) => t.status === "draft").length;
  $("#kpi-silent").textContent = approved.filter((t) => !t.fire_count).length;
}

async function loadFirings() {
  // The server's page: a trigger on the planned-stop bit fires at every
  // changeover on every machine, and a day of that is thousands of rows.
  const params = new URLSearchParams({ hours: "24", limit: String(FIRING_PAGE), offset: String(firingOffset) });
  if ($("#f-machine").value) params.set("equipment", $("#f-machine").value);
  if ($("#f-ok").value) params.set("ok", $("#f-ok").value);
  const page = await api(`/triggers/firings?${params}`);
  firingOffset = page.offset;
  const rows = page.items;
  const body = $("#firings-table tbody");
  body.replaceChildren();
  if (!rows.length) { const tr = el("tr"); const td = el("td", "muted", page.total ? "No firing matches." : "Nothing fired in the last 24 hours."); td.colSpan = 7; tr.append(td); body.append(tr); }
  for (const f of rows) {
    const tr = el("tr", f.ok ? null : "stale");
    tr.append(el("td", "muted small", fmt.stamp(f.ts)), el("td", "code", f.trigger));
    const where = el("td"); where.append(FS.link("machine", f.equipment)); tr.append(where);
    tr.append(el("td", "code", f.tag), el("td", "num", fmt.qty(f.value)), el("td", null, f.action));
    tr.append(el("td", f.ok ? "small" : "alarm small", f.ok ? JSON.stringify(f.outcome) : `failed: ${(f.outcome || {}).error || ""}`));
    body.append(tr);
  }
  $("#firing-count").textContent = `— ${rows.length} of ${page.total.toLocaleString()} in the last 24 hours`;
  FS.pager($("#f-pager"), page, (offset) => { firingOffset = offset; loadFirings().catch(fail); });
}

async function loadForm() {
  catalog = await api("/triggers/catalog");
  const cond = $("#t-condition");
  cond.replaceChildren(...catalog.conditions.map((c) => new Option(c.replace("_", " "), c)));
  const act = $("#t-action");
  act.replaceChildren(...Object.keys(catalog.actions).map((a) => new Option(a.replace(/_/g, " "), a)));
  const help = () => { $("#action-help").textContent = catalog.actions[act.value] || ""; };
  act.addEventListener("change", help); help();
  const [states, tags] = await Promise.all([api("/equipment/states"), api("/equipment/tags").catch(() => ({ rows: [] }))]);
  for (const id of ["tr-machine", "f-machine"]) {
    $(`#${id}`).replaceChildren(new Option("Any machine", ""), ...states.map((s) => new Option(s.equipment, s.equipment)));
  }
  const machine = $("#t-machine");
  machine.replaceChildren(new Option("any machine", ""), ...states.map((s) => new Option(s.equipment, s.equipment)).sort());
  const names = [...new Set(tags.rows.map((r) => r.tag))].sort();
  $("#tag-names").replaceChildren(...names.map((n) => { const o = document.createElement("option"); o.value = n; return o; }));
}

function wire() {
  $("#trigger-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    let params = null;
    const raw = $("#t-params").value.trim();
    if (raw) { try { params = JSON.parse(raw); } catch (err) { toast("Action params must be JSON.", "bad"); return; } }
    try {
      const out = await api("/triggers", { method: "POST", body: {
        code: $("#t-code").value.trim().toUpperCase(), name: $("#t-name").value.trim(),
        equipment: $("#t-machine").value || null, tag: $("#t-tag").value.trim(),
        condition: $("#t-condition").value, threshold: Number($("#t-threshold").value),
        sustained_seconds: Number($("#t-sustained").value || 0), cooldown_seconds: Number($("#t-cooldown").value || 0),
        action: $("#t-action").value, action_params: params } });
      toast(`${out.code} drafted — approve it to put it in force`);
      $("#trigger-form").reset();
      await loadTriggers();
    } catch (err) { toast(err.message, "bad"); }
  });
}

const LOADERS = { triggers: loadTriggers, firings: loadFirings };

function onTab(name) {
  clearInterval(timer);
  const run = () => (LOADERS[name] || loadTriggers)().then(() => live(true)).catch(fail);
  run();
  timer = setInterval(run, REFRESH_MS);
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  wire();
  for (const id of ["tr-q", "tr-status", "tr-machine"]) $(`#${id}`).addEventListener("input", () => { triggerOffset = 0; drawTriggers(); });
  for (const id of ["f-machine", "f-ok"]) $(`#${id}`).addEventListener("change", () => { firingOffset = 0; loadFirings().catch(fail); });
  await loadForm().catch(fail);
  FS.tabs.init(document, onTab);
})().catch(fail);
