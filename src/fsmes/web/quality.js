/* Quality screen.
   Inspection history against the specification that judged it. The chart is
   hand-drawn SVG because principle 5 says a plant PC renders this for years
   with no node toolchain - a charting library would be the one dependency
   that eventually stops installing.

   At 127 specifications the strip of tabs was a wall and the history was a
   slice of the last 200 checks across the whole plant. The chart is now
   chosen by material then characteristic and reads its own series from the
   server; the history, the specifications and the non-conformances are
   filtered and paged, and each says how much of the whole it is showing
   (STYLE.md rule 4). */

const REFRESH_MS = 5000;
const HISTORY_PAGE = 50;
const SPEC_PAGE = 25;
const NC_PAGE = 25;

let specs = [];
let series = [];       // the checks behind the chart: one material/characteristic
let ncPage = { items: [], total: 0, limit: 50, offset: 0, has_more: false };  // the server's page of non-conformances
let active = null;     // "material/characteristic"
let historyPage = { items: [], total: 0, limit: HISTORY_PAGE, offset: 0, has_more: false };
const filters = { material: "", hMaterial: "", hChar: "", hResult: "", hOffset: 0,
                  sQ: "", sMaterial: "", sOffset: 0, nStatus: "open", nQ: "", nOffset: 0 };

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const clock = (ts) => (ts ? new Date(ts + (ts.endsWith("Z") ? "" : "Z")).toLocaleTimeString() : "");
const stamp = (ts) => (ts ? new Date(ts + (ts.endsWith("Z") ? "" : "Z")).toLocaleString() : "");
const key = (o) => `${o.material}/${o.characteristic}`;

async function api(path, options = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (r.status === 401) { window.location = "/dashboard"; throw new Error("signed out"); }
  const data = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) throw new Error((data && data.detail) || `${r.status} ${r.statusText}`);
  return data;
}

function live(ok) {
  $("#live-dot").className = "dot" + (ok ? "" : " bad");
  $("#live-text").textContent = ok ? "live" : "reconnecting…";
}

function toast(message, kind = "good") {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast ${kind}`;
  clearTimeout(node._t);
  node._t = setTimeout(() => node.classList.add("hidden"), 3500);
}

/* A link can open the screen on one characteristic:
   /dashboard/quality?material=FG-FILL1&characteristic=fill_weight */
function readUrl() {
  const p = new URL(window.location).searchParams;
  if (p.get("material")) filters.material = p.get("material");
  if (p.get("material") && p.get("characteristic")) active = `${p.get("material")}/${p.get("characteristic")}`;
}

function writeUrl() {
  const url = new URL(window.location);
  const spec = specs.find((s) => key(s) === active);
  if (spec) { url.searchParams.set("material", spec.material); url.searchParams.set("characteristic", spec.characteristic); }
  else { url.searchParams.delete("material"); url.searchParams.delete("characteristic"); }
  history.replaceState(null, "", url);
}

const materials = () => [...new Set(specs.map((s) => s.material))].sort();

function fillMaterialSelect(select, keep, first) {
  const chosen = keep;
  select.replaceChildren(new Option(first, ""), ...materials().map((m) => new Option(m, m)));
  if (materials().includes(chosen)) select.value = chosen;
}

/* ---------- the chart ---------- */

const PAD = { l: 52, r: 14, t: 14, b: 26 };
const W = 900, H = 260;

function drawChart() {
  const svg = $("#chart");
  svg.textContent = "";

  const spec = specs.find((s) => key(s) === active);
  const points = series.slice().sort((a, b) => (a.ts < b.ts ? -1 : 1));

  $("#chart-label").textContent = spec
    ? `— ${spec.characteristic} on ${spec.material} (${spec.unit || ""}) · last ${points.length} checks` : "";

  const empty = !spec || points.length === 0;
  $("#chart-empty").classList.toggle("hidden", !empty);
  $("#chart-wrap").classList.toggle("hidden", empty);
  $("#chart-empty").textContent = spec
    ? "No checks recorded yet for this characteristic." : "Choose a material, then a characteristic.";
  if (empty) return;

  // Scale to the data and the limits together, so a point outside the band is
  // visibly outside rather than clipped to the edge.
  const values = points.map((c) => c.value);
  const lo = Math.min(...values, spec.min_value ?? Infinity);
  const hi = Math.max(...values, spec.max_value ?? -Infinity);
  const pad = (hi - lo) * 0.15 || 1;
  const yMin = lo - pad, yMax = hi + pad;

  const x = (i) => PAD.l + (i / Math.max(points.length - 1, 1)) * (W - PAD.l - PAD.r);
  const y = (v) => PAD.t + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.t - PAD.b);

  if (spec.min_value !== null && spec.max_value !== null) {
    svg.appendChild(svgEl("rect", {
      class: "spec-band", x: PAD.l, y: y(spec.max_value),
      width: W - PAD.l - PAD.r, height: Math.max(1, y(spec.min_value) - y(spec.max_value)),
    }));
  }
  for (const limit of [spec.min_value, spec.max_value]) {
    if (limit === null || limit === undefined) continue;
    svg.appendChild(svgEl("line", {
      class: "spec-limit", x1: PAD.l, x2: W - PAD.r, y1: y(limit), y2: y(limit),
    }));
    const label = svgEl("text", { class: "axis-text", x: 6, y: y(limit) + 4 });
    label.textContent = limit;
    svg.appendChild(label);
  }
  if (spec.min_value !== null && spec.max_value !== null) {
    const mid = (spec.min_value + spec.max_value) / 2;
    svg.appendChild(svgEl("line", {
      class: "spec-mid", x1: PAD.l, x2: W - PAD.r, y1: y(mid), y2: y(mid),
    }));
  }

  svg.appendChild(svgEl("path", {
    class: "series",
    d: points.map((c, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(c.value).toFixed(1)}`).join(" "),
  }));

  points.forEach((c, i) => {
    const failed = c.result === "fail";
    const dot = svgEl("circle", {
      class: failed ? "point-fail" : "point-pass",
      cx: x(i), cy: y(c.value), r: failed ? 4 : 2.5,
    });
    const title = svgEl("title");
    title.textContent = `${c.value} — ${c.result} — ${stamp(c.ts)}`;
    dot.appendChild(title);
    svg.appendChild(dot);
  });

  const first = svgEl("text", { class: "axis-text", x: PAD.l, y: H - 8 });
  first.textContent = clock(points[0].ts);
  svg.appendChild(first);
  const last = svgEl("text", { class: "axis-text", x: W - PAD.r, y: H - 8, "text-anchor": "end" });
  last.textContent = clock(points[points.length - 1].ts);
  svg.appendChild(last);
}

async function loadSeries() {
  const spec = specs.find((s) => key(s) === active);
  if (!spec) { series = []; return; }
  const page = await api(`/quality/checks?material=${encodeURIComponent(spec.material)}`
    + `&characteristic=${encodeURIComponent(spec.characteristic)}&limit=200`);
  series = page.items || [];
}

async function showInstruction() {
  const box = document.querySelector("#wi-inline");
  if (!box) return;
  const spec = specs.find((s) => key(s) === active);
  if (!spec) { box.classList.add("hidden"); return; }
  try {
    const found = await api(
      `/documents/for?material=${encodeURIComponent(spec.material)}`
      + `&characteristic=${encodeURIComponent(spec.characteristic)}`);
    if (!found.length) { box.classList.add("hidden"); return; }
    const doc = found[0];
    box.textContent = "";
    const h = el("h4", null, doc.title);
    box.appendChild(h);
    // The first line of the body is the Purpose, which is the sentence an
    // operator needs at a glance. The rest is a click away.
    const first = (doc.body || "").split("\n").find((l) => l.trim());
    if (first) box.appendChild(el("div", "muted", first.replace(/^Purpose:\s*/i, "")));
    const link = el("a", null, `Read ${doc.code} (rev ${doc.revision}) →`);
    link.href = "/dashboard/instructions";
    box.appendChild(el("div").appendChild(link).parentNode);
    box.classList.remove("hidden");
  } catch (err) {
    box.classList.add("hidden");
  }
}

/* The tabs are one material's characteristics - a handful, not the plant's
   hundred. The material comes from the select above them. */
function drawTabs() {
  const tabs = $("#spec-tabs");
  tabs.textContent = "";
  const mine = specs.filter((s) => s.material === filters.material);
  $("#q-scope").textContent = filters.material
    ? `${mine.length} characteristic${mine.length === 1 ? "" : "s"} on ${filters.material}, of ${specs.length} in the plant`
    : `${materials().length} materials, ${specs.length} characteristics in the plant`;
  for (const spec of mine) {
    const k = key(spec);
    const tab = el("button", "tab" + (k === active ? " active" : ""), spec.characteristic);
    tab.type = "button";
    tab.addEventListener("click", async () => {
      active = k; writeUrl(); drawTabs();
      await loadSeries().catch(() => { series = []; });
      drawChart(); showInstruction();
    });
    tabs.appendChild(tab);
  }
}

/* ---------- specifications: filtered and paged in the browser ----------
   The endpoint answers with the whole list (127 rows, 13 KB - fine); the
   screen still says how many it is showing. */

function drawSpecs() {
  const q = filters.sQ.toLowerCase();
  const matching = specs.filter((s) =>
    (!filters.sMaterial || s.material === filters.sMaterial)
    && (!q || s.material.toLowerCase().includes(q) || s.characteristic.toLowerCase().includes(q)));
  if (filters.sOffset >= matching.length) filters.sOffset = 0;
  const page = {
    items: matching.slice(filters.sOffset, filters.sOffset + SPEC_PAGE),
    total: matching.length, limit: SPEC_PAGE, offset: filters.sOffset,
    has_more: filters.sOffset + SPEC_PAGE < matching.length,
  };
  $("#s-count").textContent = matching.length === specs.length
    ? `— ${page.items.length} of ${specs.length}`
    : `— ${page.items.length} of ${matching.length} matching, ${specs.length} in the plant`;
  const body = $("#specs tbody");
  body.textContent = "";
  for (const s of page.items) {
    const row = el("tr");
    const open = el("a", "obj", s.characteristic);
    open.href = `/dashboard/quality?material=${encodeURIComponent(s.material)}&characteristic=${encodeURIComponent(s.characteristic)}`;
    open.addEventListener("click", async (event) => {
      event.preventDefault();
      filters.material = s.material; $("#q-material").value = s.material;
      active = key(s); writeUrl(); drawTabs();
      await loadSeries().catch(() => { series = []; });
      drawChart(); showInstruction();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    row.appendChild(el("td", null, s.material));
    const cell = el("td"); cell.appendChild(open); row.appendChild(cell);
    row.appendChild(el("td", "num", s.min_value ?? "—"));
    row.appendChild(el("td", "num", s.max_value ?? "—"));
    row.appendChild(el("td", null, s.unit || ""));
    body.appendChild(row);
  }
  if (!page.items.length) {
    const row = el("tr"); const cell = el("td", "muted", "No specification matches."); cell.colSpan = 5;
    row.appendChild(cell); body.appendChild(row);
  }
  FS.pager($("#s-pager"), page, (offset) => { filters.sOffset = offset; drawSpecs(); });
}

/* ---------- inspection history: the server's page ---------- */

function historyQuery() {
  const p = new URLSearchParams({ limit: String(HISTORY_PAGE), offset: String(filters.hOffset) });
  if (filters.hMaterial) p.set("material", filters.hMaterial);
  if (filters.hChar) p.set("characteristic", filters.hChar);
  if (filters.hResult) p.set("result", filters.hResult);
  return `/quality/checks?${p}`;
}

function drawChecks() {
  const body = $("#checks tbody");
  body.textContent = "";
  const page = historyPage;
  const narrowed = filters.hMaterial || filters.hChar || filters.hResult;
  $("#h-count").textContent = `— ${page.items.length} of ${(page.total || 0).toLocaleString()}${narrowed ? " matching" : ""}`;
  for (const c of page.items) {
    const row = el("tr");
    row.appendChild(el("td", "muted", stamp(c.ts)));
    row.appendChild(el("td", null, c.material));
    row.appendChild(el("td", null, c.characteristic));
    row.appendChild(el("td", "num", c.value));
    row.appendChild(el("td", c.result === "fail" ? "result-fail" : "result-pass", c.result));
    row.appendChild(el("td", "mono", c.checked_by || ""));
    body.appendChild(row);
  }
  if (!page.items.length) {
    const row = el("tr"); const cell = el("td", "muted", "No checks match."); cell.colSpan = 6;
    row.appendChild(cell); body.appendChild(row);
  }
  FS.pager($("#h-pager"), page, (offset) => { filters.hOffset = offset; refresh(); });
}

function fillHistoryChars() {
  const select = $("#h-char");
  const keep = filters.hChar;
  const chars = [...new Set(specs.filter((s) => !filters.hMaterial || s.material === filters.hMaterial)
                                  .map((s) => s.characteristic))].sort();
  select.replaceChildren(new Option("Any characteristic", ""), ...chars.map((c) => new Option(c, c)));
  if (chars.includes(keep)) select.value = keep; else filters.hChar = "";
}

/* ---------- non-conformances: by status, searchable, paged ---------- */

function drawNcs() {
  // The server's page, filtered there: a day of a busy plant is two
  // thousand non-conformances, and a month is a list no screen should fetch.
  const list = $("#ncs");
  list.textContent = "";
  const q = filters.nQ.toLowerCase();
  const page = ncPage;
  filters.nOffset = page.offset;
  const what = filters.nStatus ? filters.nStatus : "in any status";
  $("#n-count").textContent = `— ${page.items.length} of ${page.total.toLocaleString()} ${what}${q ? " matching" : ""}`;
  if (!page.items.length) {
    list.appendChild(el("li", "muted", filters.nStatus === "open" && !q
      ? "none open — the line is inside spec" : "none match"));
  }
  for (const nc of page.items) {
    const li = el("li");
    const body = el("div");
    body.appendChild(el("strong", null, nc.code));
    body.appendChild(el("span", `pill ${nc.status}`, " " + nc.status));
    body.appendChild(el("span", null, " " + (nc.description || "")));
    const raised = nc.created_at || nc.opened_at;
    const detail = el("div", "muted small");
    detail.append([nc.severity, raised && stamp(raised)].filter(Boolean).join(" · "));
    if (nc.order) {
      const order = el("a", "obj", nc.order);
      order.href = `/dashboard/orders?q=${encodeURIComponent(nc.order)}`;
      detail.append(" · order ", order);
    }
    if (nc.closed_at) detail.append(` · closed ${stamp(nc.closed_at)}`);
    body.appendChild(detail);
    li.appendChild(body);

    // Gated: showing a button that will 403 is worse than not showing
    // it. This reverses the earlier let-the-403-do-the-talking decision.
    if (nc.status !== "open" || !window.FS || !FS.can("quality.close_nc")) {
      list.appendChild(li);
      continue;
    }
    const close = el("button", "ghost", "Close");
    close.type = "button";
    close.addEventListener("click", async () => {
      close.disabled = true;
      try {
        await api(`/quality/nonconformances/${nc.code}/close`, { method: "POST" });
        toast(`${nc.code} closed`);
        await refresh();
      } catch (err) {
        // Closing is a supervisor action; an operator being refused is the
        // system working, so say which it was.
        toast(err.message, "bad");
        close.disabled = false;
      }
    });
    li.appendChild(close);
    list.appendChild(li);
  }
  FS.pager($("#n-pager"), page, (offset) => { filters.nOffset = offset; refresh(); });
}

/* ---------- refresh ---------- */

async function refresh() {
  try {
    const ncParams = new URLSearchParams({ limit: String(NC_PAGE), offset: String(filters.nOffset) });
    if (filters.nStatus) ncParams.set("status", filters.nStatus);
    if (filters.nQ) ncParams.set("q", filters.nQ);
    const ncQuery = `/quality/nonconformances?${ncParams}`;
    const [s, h, n, all, failed, openCount] = await Promise.all([
      api("/quality/specs"),
      api(historyQuery()),
      api(ncQuery),
      api("/quality/checks?limit=1"),
      api("/quality/checks?limit=1&result=fail"),
      api("/quality/nonconformances?status=open&limit=1"),
    ]);
    specs = s || [];
    historyPage = h;
    ncPage = n || ncPage;
    if (!filters.material && specs.length) filters.material = (specs.find((x) => key(x) === active) || specs[0]).material;
    if (!active) { const first = specs.find((x) => x.material === filters.material); if (first) active = key(first); }
    if (active && !specs.some((x) => key(x) === active)) active = null;

    fillMaterialSelect($("#q-material"), filters.material, "Choose a material");
    fillMaterialSelect($("#h-material"), filters.hMaterial, "Any material");
    fillMaterialSelect($("#s-material"), filters.sMaterial, "Any material");
    fillHistoryChars();

    const total = all.total || 0;
    $("#kpi-checks").textContent = total.toLocaleString();
    $("#kpi-pass").textContent = total ? (((total - (failed.total || 0)) / total) * 100).toFixed(1) + "%" : "—";
    $("#kpi-ncs").textContent = openCount && openCount.total !== undefined ? openCount.total.toLocaleString() : "—";
    $("#kpi-specs").textContent = specs.length;

    await loadSeries().catch(() => { series = []; });
    drawTabs(); drawChart(); drawSpecs(); drawChecks(); drawNcs();
    window.__fsmesPageData = { specs: specs.length, active, series: series.slice(0, 40),
                               history: { total: historyPage.total, shown: historyPage.items.length },
                               nonconformances: ncPage.items.slice(0, 40), filters: { ...filters } };
    showInstruction();
    live(true);
  } catch (err) {
    live(false);
  }
}

/* ---------- controls ---------- */

$("#q-material").addEventListener("change", async () => {
  filters.material = $("#q-material").value;
  const first = specs.find((x) => x.material === filters.material);
  active = first ? key(first) : null;
  writeUrl(); drawTabs();
  await loadSeries().catch(() => { series = []; });
  drawChart(); showInstruction();
});

$("#s-q").addEventListener("input", () => { filters.sQ = $("#s-q").value.trim(); filters.sOffset = 0; drawSpecs(); });
$("#s-material").addEventListener("change", () => { filters.sMaterial = $("#s-material").value; filters.sOffset = 0; drawSpecs(); });

$("#h-material").addEventListener("change", () => {
  filters.hMaterial = $("#h-material").value; filters.hOffset = 0; fillHistoryChars(); refresh();
});
$("#h-char").addEventListener("change", () => { filters.hChar = $("#h-char").value; filters.hOffset = 0; refresh(); });
$("#h-result").addEventListener("change", () => { filters.hResult = $("#h-result").value; filters.hOffset = 0; refresh(); });
$("#h-clear").addEventListener("click", () => {
  filters.hMaterial = ""; filters.hChar = ""; filters.hResult = ""; filters.hOffset = 0;
  $("#h-material").value = ""; $("#h-result").value = ""; fillHistoryChars(); refresh();
});

$("#n-status").addEventListener("change", () => { filters.nStatus = $("#n-status").value; filters.nOffset = 0; refresh(); });
let ncTyping = null;
$("#n-q").addEventListener("input", () => {
  clearTimeout(ncTyping);
  ncTyping = setTimeout(() => { filters.nQ = $("#n-q").value.trim(); filters.nOffset = 0; refresh(); }, 250);
});

(async function boot() {
  // Know who is asking before the first draw, or capability-gated buttons
  // appear one refresh late.
  await FS.whoami().catch(() => {});
  readUrl();
  await refresh();
  setInterval(refresh, REFRESH_MS);
})();
