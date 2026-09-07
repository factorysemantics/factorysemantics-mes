/* One line, and what is really going on along it.

   The second object page. Health tiles say what each machine is doing and
   whether it is alarming; the WIP strip says where the order's units are
   sitting; the timeline shows a stop walking down the line; the 3D view is a
   tab, not a destination. */

const { $, el, api, fmt, kit } = window.FS;

const REFRESH_MS = 4000;
let lineCode = new URL(location).searchParams.get("line");
let lines = [];
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

const hours = () => FS.window.hours();
const q = () => `line=${encodeURIComponent(lineCode)}&hours=${hours()}`;

function rememberLine(code) {
  lineCode = code;
  const url = new URL(location);
  url.searchParams.set("line", code);
  history.replaceState(null, "", url);
  $("#analysis-link").href = `/dashboard/analysis?line=${encodeURIComponent(code)}`;
  document.title = `${code} — FactorySemantics MES`;
}

/* ---------- overview ---------- */

function tile(m, alarms) {
  const card = el("div", `machine ${m.state}`);
  const head = el("div", "machine-head");
  head.append(FS.link("machine", m.code, "machine-code"), el("span", "machine-name", m.name),
              el("span", `state ${m.state}`, m.state));
  const meta = el("div", "machine-meta");
  const order = el("span");
  order.append("Order ", el("strong", null, m.current_order || "—"));
  const analog = el("span");
  analog.append(m.analog ? `${m.analog.name} ` : "Value ", el("strong", null, m.analog ? fmt.qty(m.analog.value) : "—"));
  meta.append(order, analog);
  card.append(head, meta);
  const active = alarms[m.code] || [];
  if (active.length) card.append(el("div", "alarm small", "▲ " + active.join(", ")));
  const bars = el("div", "bars");
  kit.oeeBars(bars, m.oee);
  card.append(bars);
  return card;
}

function renderWip(w) {
  const host = $("#wip");
  host.replaceChildren();
  if (!w.stations.length) {
    host.append(el("p", "muted", w.orders.length ? "Nothing has reached a station yet." : "No order is on the floor for this line."));
  }
  w.stations.forEach((s, i) => {
    if (i) host.append(el("span", "wip-arrow", "→"));
    const box = el("div", "wip-station");
    box.append(FS.link("machine", s.code));
    box.append(el("span", "muted small", s.name));
    box.append(el("span", `qty${s.wip_qty < 0 ? " negative" : ""}`, fmt.qty(s.wip_qty)));
    box.append(el("span", "muted small", s.cost_center ? `cc ${s.cost_center}` : "no cost center"));
    box.title = s.orders.map((o) => `${o.order}: ${fmt.qty(o.wip_qty)} (${o.status})`).join("\n");
    host.append(box);
  });
  $("#wip-note").textContent = w.consistent
    ? `— ${fmt.qty(w.wip_total)} across ${w.orders.length} order(s)`
    : `— ${fmt.qty(w.wip_total)}, and a station reports more finished than reached it`;
  $("#l-wip").textContent = fmt.qty(w.wip_total);
  $("#l-orders").textContent = w.orders.length;
}

async function refreshOverview() {
  const [summary, alarms, wip, oee, tree] = await Promise.all([
    api(`/dashboard/summary?line=${encodeURIComponent(lineCode)}&oee_hours=${hours()}`),
    api("/equipment/alarms"),
    api(`/line/wip?line=${encodeURIComponent(lineCode)}`),
    api(`/analysis/oee?${q()}`),
    api("/equipment/tree"),
  ]);
  const active = Object.fromEntries(alarms.map((a) => [a.equipment, a.active]));
  $("#tiles").replaceChildren(...summary.machines.map((m) => tile(m, active)));
  $("#l-machines").textContent = `${summary.plant.machines_running}/${summary.plant.machines_total} running`;
  $("#l-oee").textContent = fmt.pct(oee.line_oee) + (oee.constraint ? ` (worst ${oee.constraint})` : "");
  renderWip(wip);
  const found = (function find(nodes) {
    for (const n of nodes) {
      if (n.code === lineCode) return n;
      const deeper = find(n.children || []);
      if (deeper) return deeper;
    }
    return null;
  })(tree.roots);
  $("#l-code").textContent = lineCode;
  $("#l-name").textContent = found ? found.name : "";
  $("#l-cc").textContent = found && found.cost_center ? found.cost_center : "—";
  window.__fsmesPageData = { line: lineCode, machines: summary.machines, wip, oee };
}

async function loadTimeline() {
  const data = await api(`/analysis/timeline?${q()}&limit=60`);
  kit.timeline($("#tl"), data);
}

function loadScene() {
  const frame = $("#scene");
  const want = `/dashboard/line/3d?line=${encodeURIComponent(lineCode)}`;
  if (frame.getAttribute("src") !== want) frame.setAttribute("src", want);
  return Promise.resolve();
}

const LOADERS = { overview: refreshOverview, timeline: loadTimeline, analysis: () => Promise.resolve(), scene: loadScene };

function onTab(name) {
  clearInterval(timer);
  const run = () => (LOADERS[name] || LOADERS.overview)().then(() => live(true)).catch(fail);
  run();
  if (name === "overview") timer = setInterval(run, REFRESH_MS);
}

(async function boot() {
  await FS.whoami().catch(() => {});
  lines = await api("/analysis/lines");
  const select = $("#line");
  select.replaceChildren(...lines.map((l) => new Option(`${l.code} — ${l.name} (${l.stations})`, l.code)));
  if (!lineCode || !lines.some((l) => l.code === lineCode)) lineCode = lines.length ? lines[0].code : null;
  if (!lineCode) return fail(new Error("No line has any machines on it yet."));
  select.value = lineCode;
  rememberLine(lineCode);
  select.addEventListener("change", () => { rememberLine(select.value); onTab(FS.tabs.current()); });
  FS.window.bind($("#hours"), () => onTab(FS.tabs.current()));
  FS.tabs.init(document, onTab);
})().catch(fail);
