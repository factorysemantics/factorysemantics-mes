/* Engineering › Tags: every tag on every machine, in one table.

   The machine page answers "what is this machine saying"; this screen
   answers "what is the plant saying, and is anything wrong with the
   saying" - which machines have gone quiet, which tags stopped arriving,
   which alarm bits are set, and where the writable setpoints and their
   bounds are. The manifest that powers it was generated to be browsed and,
   until now, nothing browsed it. */

const { $, el, api, fmt, kit } = window.FS;

const REFRESH_MS = 5000;
const ROW_PAGE = 100;
let data = null;
let rowOffset = 0;

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

function valueText(r) {
  if (r.value === null || r.value === undefined) return "—";
  if (typeof r.value !== "number") return String(r.value);
  if (["counter", "state", "alarm"].includes(r.kind)) return Math.round(r.value).toLocaleString();
  return fmt.qty(r.value);
}

function filters() {
  return {
    q: $("#q").value.trim().toLowerCase(),
    machine: $("#machine").value,
    kind: $("#kind").value,
    stale: $("#only-stale").checked,
    writable: $("#only-writable").checked,
  };
}

function renderHealth(machines) {
  const host = $("#health");
  host.replaceChildren();
  for (const m of machines) {
    const quiet = m.quiet_for_seconds !== null && m.quiet_for_seconds > data.stale_after_seconds;
    const tile = el("div", `machine ${quiet ? "down" : "running"} health-tile`);
    const head = el("div", "machine-head");
    head.append(FS.link("machine", m.code, "machine-code"), el("span", "machine-name", m.name));
    tile.append(head);
    const meta = el("div", "machine-meta");
    const spoke = el("span");
    spoke.append("Last spoke ", el("strong", null, kit.age(m.quiet_for_seconds) + (m.quiet_for_seconds === null ? "" : " ago")));
    const counts = el("span");
    counts.append(`${m.tags} tags`, el("strong", null, m.stale ? ` · ${m.stale} stale` : ""));
    meta.append(spoke, counts);
    tile.append(meta);
    if (m.alarms.length) tile.append(el("div", "alarm small", "▲ " + m.alarms.join(", ")));
    host.append(tile);
  }
}

function renderRows() {
  const f = filters();
  const matching = data.rows.filter((r) =>
    (!f.machine || r.equipment === f.machine)
    && (!f.kind || r.kind === f.kind)
    && (!f.stale || r.stale)
    && (!f.writable || r.writable)
    && (!f.q || `${r.equipment} ${r.tag}`.toLowerCase().includes(f.q)));
  // A thousand-tag fabric redrawn every five seconds is a page at a time,
  // not a thousand rows; the count says how many the filter left.
  const page = FS.clientPage(matching, rowOffset, ROW_PAGE);
  rowOffset = page.offset;
  const rows = page.items;
  const body = $("#tags-table tbody");
  body.replaceChildren();
  for (const r of rows) {
    const tr = el("tr", r.stale ? "stale" : null);
    const where = el("td");
    where.append(FS.link("machine", r.equipment));
    tr.append(where);
    const name = el("td", "code");
    name.append(r.tag);
    if (r.primary) name.append(" ", el("span", "pill", "primary"));
    if (r.writable) name.append(" ", el("span", "pill", "writable"));
    if (r.active && r.active.length) name.append(" ", el("span", "alarm small", r.active.join(", ")));
    tr.append(name);
    tr.append(el("td", "num mono", valueText(r)));
    tr.append(el("td", "muted", r.unit || ""));
    tr.append(el("td", "muted", r.kind || "—"));
    const bounds = el("td", "muted small");
    if (r.min !== null && r.min !== undefined) bounds.textContent = `${r.min} – ${r.max}${r.drives ? ` → ${r.drives}` : ""}`;
    else if (r.nominal !== null && r.nominal !== undefined) bounds.textContent = `nominal ${r.nominal}`;
    tr.append(bounds);
    const age = el("td", "muted small", kit.age(r.age_seconds));
    if (r.stale) age.append(" ", el("span", "pill down", "stale"));
    tr.append(age);
    if (typeof r.value === "number") {
      tr.addEventListener("click", () => {
        location.href = `/dashboard/machine/${encodeURIComponent(r.equipment)}?tag=${encodeURIComponent(r.tag)}#trend`;
      });
    }
    body.append(tr);
  }
  $("#row-count").textContent = FS.countText(page, data.rows.length, "on the fabric");
  FS.pager($("#tag-pager"), page, (offset) => { rowOffset = offset; renderRows(); });
}

async function refresh() {
  try {
    data = await api("/equipment/tags");
    const select = $("#machine");
    const keep = select.value;
    select.replaceChildren(new Option("Any machine", ""), ...data.machines.map((m) => new Option(m.code, m.code)));
    if ([...select.options].some((o) => o.value === keep)) select.value = keep;
    $("#kpi-tags").textContent = data.rows.length.toLocaleString();
    $("#kpi-quiet").textContent = data.machines.filter((m) => m.quiet_for_seconds !== null && m.quiet_for_seconds > data.stale_after_seconds).length;
    $("#kpi-stale").textContent = data.rows.filter((r) => r.stale).length;
    $("#kpi-alarms").textContent = data.machines.filter((m) => m.alarms.length).length;
    renderHealth(data.machines);
    renderRows();
    window.__fsmesPageData = data;
    live(true);
  } catch (err) {
    fail(err);
  }
}

(async function boot() {
  await FS.whoami().catch(() => {});
  for (const id of ["q", "machine", "kind", "only-stale", "only-writable"]) {
    $(`#${id}`).addEventListener("input", () => { rowOffset = 0; if (data) renderRows(); });
  }
  await refresh();
  setInterval(refresh, REFRESH_MS);
})();
