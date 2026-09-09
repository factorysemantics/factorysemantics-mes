/* Operations: what is running, what it said, and who did what.

   Read when something is wrong, so everything is one screen and nothing is
   more than a filter away. */

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const clock = (ts) => (ts ? new Date(ts + (String(ts).endsWith("Z") ? "" : "Z")).toLocaleTimeString() : "");
const ago = (epoch) => {
  const secs = Math.max(0, Math.round(Date.now() / 1000 - epoch));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
};
const bytes = (n) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1024)} kB`);

let me = null;
let component = null;

async function api(path) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" } });
  if (r.status === 401) { window.location = "/dashboard"; throw new Error("signed out"); }
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new Error((data && data.detail) || `${r.status}`);
  return data;
}

function live(ok) {
  $("#live-dot").className = "dot" + (ok ? "" : " bad");
  $("#live-text").textContent = ok ? "live" : "reconnecting…";
}

function renderPlant(plant) {
  const box = $("#plant-facts");
  box.textContent = "";
  const labels = {
    database: "Database", api: "Dashboard", opc_endpoint: "OPC UA endpoint",
    tag_map: "Tag map", replay_dir: "Line data", sim_speed: "Replay speed",
    opc_publish_ms: "Sampling interval",
  };
  for (const [key, label] of Object.entries(labels)) {
    if (plant[key] === undefined || plant[key] === null) continue;
    const fact = el("div", "fact");
    fact.appendChild(el("span", "k", label));
    let value = String(plant[key]);
    if (key === "sim_speed") value = `${value}×`;
    if (key === "opc_publish_ms") value = `${value} ms`;
    fact.appendChild(el("span", "v", value));
    box.appendChild(fact);
  }
}

function renderComponents(components) {
  const box = $("#components");
  box.textContent = "";
  const tabs = $("#log-tabs");
  tabs.textContent = "";

  for (const c of components) {
    // A component that has not written for two minutes is either idle by
    // design or stuck; either way it is worth a colour.
    const quiet = Date.now() / 1000 - c.last_wrote > 120;
    const dead = c.listening === false;
    const card = el("div", `component${dead ? " down" : quiet ? " stale" : ""}`);

    const top = el("div", "top");
    top.appendChild(el("span", `dot${dead ? " bad" : ""}`));
    top.appendChild(el("span", "name", c.component));
    if (c.listening !== undefined) {
      top.appendChild(el("span", `pill ${c.listening ? "running" : "down"}`,
        c.listening ? "listening" : "not listening"));
    }
    card.appendChild(top);
    card.appendChild(el("div", "purpose", c.purpose));
    card.appendChild(el("div", "meta",
      `wrote ${ago(c.last_wrote)} · ${bytes(c.log_bytes)}${c.where ? " · " + c.where : ""}`));
    box.appendChild(card);

    const tab = el("button", "tab" + (c.component === component ? " active" : ""), c.component);
    tab.addEventListener("click", () => { component = c.component; loadLog(); renderTabsActive(); });
    tabs.appendChild(tab);
  }
  if (!component && components.length) component = components[0].component;
  renderTabsActive();
}

function renderTabsActive() {
  for (const tab of document.querySelectorAll("#log-tabs .tab")) {
    tab.classList.toggle("active", tab.textContent === component);
  }
  $("#log-which").textContent = component ? `— ${component}` : "";
}

async function loadLog() {
  if (!component) return;
  const level = $("#log-level").value;
  const out = await api(`/ops/logs/${component}?lines=150${level ? `&level=${level}` : ""}`);
  const body = $("#log tbody");
  body.textContent = "";
  if (out.error || !out.entries.length) {
    const row = el("tr");
    const cell = el("td", "muted", out.error || out.note || "nothing at this level");
    cell.colSpan = 4;
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  for (const e of out.entries.slice().reverse()) {
    const row = el("tr");
    row.appendChild(el("td", "ts", clock(e.ts)));
    row.appendChild(el("td", `lvl ${String(e.level || "").toLowerCase()}`, e.level || ""));
    row.appendChild(el("td", null, e.event || ""));
    const detail = Object.entries(e.detail || {})
      .filter(([, v]) => v !== null && v !== "")
      .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
      .join("  ");
    row.appendChild(el("td", "detail", detail.slice(0, 400)));
    body.appendChild(row);
  }
}

async function loadActivity() {
  const actor = $("#actor-filter").value;
  const action = $("#action-filter").value.trim();
  const out = await api(`/ops/activity?limit=80`
    + (actor ? `&actor=${encodeURIComponent(actor)}` : "")
    + (action ? `&action=${encodeURIComponent(action)}` : ""));

  const select = $("#actor-filter");
  if (select.dataset.filled !== String(out.actors.length)) {
    const chosen = select.value;
    select.textContent = "";
    select.appendChild(el("option", null, "anyone"));
    for (const who of out.actors) {
      const opt = el("option", null, who);
      opt.value = who;
      select.appendChild(opt);
    }
    select.value = chosen;
    select.dataset.filled = String(out.actors.length);
  }

  const body = $("#activity tbody");
  body.textContent = "";
  for (const e of out.entries) {
    const row = el("tr");
    row.appendChild(el("td", "ts", clock(e.ts)));
    row.appendChild(el("td", "who", e.actor));
    row.appendChild(el("td", null, e.action));
    row.appendChild(el("td", "who", `${e.entity_type}/${e.entity_id}`));
    const after = e.after ? JSON.stringify(e.after) : "";
    row.appendChild(el("td", "detail", after.slice(0, 220)));
    body.appendChild(row);
  }
}

/* The AI layer changes on the scale of minutes, not seconds, and nvidia-smi
   is not free - so this panel refreshes on its own slow clock instead of
   riding the 2-second service poll. */
async function loadAi() {
  let out;
  try {
    out = await api("/ai");
  } catch (err) {
    return;                       // the section simply stays as it was
  }
  const section = $("#ai-section");
  if (!out.enabled) { section.classList.add("hidden"); return; }
  section.classList.remove("hidden");

  const head = $("#ai-head");
  head.textContent = "";
  const fact = (label, value) => {
    const box = el("div", "fact");
    box.appendChild(el("span", "kpi-label", label));
    box.appendChild(el("strong", null, value));
    head.appendChild(box);
  };
  if (out.ollama.reachable) {
    const loaded = out.ollama.loaded.map((m) => m.model).join(", ") || "nothing loaded";
    fact("Model server", "up");
    fact("Loaded now", loaded);
  } else {
    fact("Model server", `unreachable at ${out.ollama.url}`);
  }
  if (out.gpu) {
    fact("GPU memory", `${out.gpu.vram_used_mb} / ${out.gpu.vram_total_mb} MB`);
    fact("GPU", `${out.gpu.utilization_pct}% · ${out.gpu.temperature_c}°C`);
  }

  const body = $("#ai-consumers tbody");
  body.textContent = "";
  for (const c of out.consumers) {
    const row = el("tr");
    row.appendChild(el("td", null, c.name));
    row.appendChild(el("td", "muted", c.trigger));
    row.appendChild(el("td", null, c.last || "—"));
    const where = el("td", "muted small");
    where.textContent = c.output;
    row.appendChild(where);
    const state = el("td");
    const cls = { ok: "running", idle: "idle", stale: "idle",
                  down: "down", unknown: "unknown" }[c.state] || "unknown";
    state.appendChild(el("span", `pill ${cls}`, c.state));
    row.appendChild(state);
    if (c.note) row.title = c.note;
    body.appendChild(row);
  }

  $("#ai-budget").textContent =
    "GPU budget, in priority order: " + out.budget.join(" → ") + ".";
}

async function loadRetention() {
  const r = await api("/ops/retention");
  const node = document.getElementById("retention-line");
  if (!node) return;
  node.textContent = `Tag history: ${r.tag_values.toLocaleString()} samples, oldest ${r.oldest ? new Date(r.oldest + "Z").toLocaleString() : "—"}. ${r.policy}.`;
}

async function loadErp() {
  const out = await api("/erp/outbox?limit=20");
  const facts = $("#erp-facts");
  facts.textContent = "";
  const fact = (label, value) => {
    const node = document.createElement("div");
    node.className = "fact";
    const k = document.createElement("span"); k.className = "k"; k.textContent = label;
    const v = document.createElement("strong"); v.textContent = value;
    node.append(k, v);
    facts.appendChild(node);
  };
  fact("pending", out.counts.pending);
  fact("dead", out.counts.dead);
  fact("sent", out.counts.sent);
  fact("oldest pending", out.oldest_pending_seconds === null ? "—" : `${Math.round(out.oldest_pending_seconds)} s`);
  const body = $("#erp-table tbody");
  body.textContent = "";
  for (const m of out.recent) {
    const row = document.createElement("tr");
    const cells = [String(m.id), m.kind, `${m.order || ""}${m.seq ? ` op ${m.seq}` : ""}`, m.status,
                   String(m.attempts), m.error || "", new Date(m.created_at + "Z").toLocaleString()];
    for (const text of cells) { const td = document.createElement("td"); td.textContent = text; row.appendChild(td); }
    const action = document.createElement("td");
    if (m.status === "dead" && (me.capabilities || []).includes("orders.close")) {
      const btn = document.createElement("button");
      btn.className = "small";
      btn.textContent = "Retry";
      btn.addEventListener("click", async () => {
        try {
          await fetch(`/erp/outbox/${m.id}/retry`, { method: "POST", headers: { "Content-Type": "application/json" } });
          await loadErp();
        } catch (err) { /* the next refresh shows the state */ }
      });
      action.appendChild(btn);
    }
    row.appendChild(action);
    body.appendChild(row);
  }
}

async function refresh() {
  try {
    const out = await api("/ops/services");
    renderPlant(out.plant);
    renderComponents(out.components);
    window.__fsmesPageData = out;
    await Promise.all([loadLog(), loadActivity(), loadErp(), loadRetention()]);
    live(true);
  } catch (err) { live(false); }
}

$("#log-level").addEventListener("change", loadLog);
$("#actor-filter").addEventListener("change", loadActivity);
$("#action-filter").addEventListener("input", () => {
  clearTimeout(window._af);
  window._af = setTimeout(loadActivity, 350);
});

(async function boot() {
  me = await api("/auth/me");
  loadAi();
  setInterval(loadAi, 15000);
  const allowed = (me.capabilities || []).includes("audit.read");
  $("#denied-who").textContent = `${me.name} (${me.role})`;
  $("#denied").classList.toggle("hidden", allowed);
  $("#ops-main").classList.toggle("hidden", !allowed);
  if (!allowed) { live(true); return; }
  await refresh();
  setInterval(refresh, 6000);
})();
