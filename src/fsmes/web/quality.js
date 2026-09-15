/* Quality screen.
   Inspection history against the specification that judged it. The chart is
   hand-drawn SVG because principle 5 says a plant PC renders this for years
   with no node toolchain - a charting library would be the one dependency
   that eventually stops installing.

   Every list here is the server's page, filtered on the server: the
   measurements, the specifications, the inspection history and the
   non-conformances. The specifications card and the tab strip used to be
   drawn out of one fetch of every specification the plant has, which is the
   habit Scott hit on the 108-station plant ("way too many tags") and the one
   that does not survive a catalogue ten times the size. The filter selects
   are built from /quality/specs/facets - the distinct materials and
   characteristics with their counts - so filling a dropdown no longer means
   reading the whole table.

   Every filter is in the address bar, so a filtered screen is a link. */

const REFRESH_MS = 5000;
const HISTORY_PAGE = 50;
const SPEC_PAGE = 25;
const NC_PAGE = 25;
// One material's characteristics, for the tab strip and the chart's limits.
// A material with more than this many is a real possibility and the strip
// says so rather than quietly stopping.
const CHAR_PAGE = 200;
// What the non-conformances card opens on. "Still open" is three states, not
// one: a record taken under review must not drop out of the list somebody is
// working. It is also the value the address bar leaves out.
const NC_DEFAULT_SCOPE = "open,under_review,dispositioned";
// How many points the chart draws. Newest first from the server, then drawn
// oldest to newest.
const SERIES_POINTS = 200;

let facets = { materials: [], materials_total: 0, characteristics: [],
               characteristics_total: 0, specs_total: 0 };
let charFacets = facets;   // the characteristics the history filter offers
let chars = { items: [], total: 0 };   // the chosen material's specifications
let series = [];       // the checks behind the chart: one material/characteristic
let specPage = { items: [], total: 0, limit: SPEC_PAGE, offset: 0, has_more: false };
let historyPage = { items: [], total: 0, limit: HISTORY_PAGE, offset: 0, has_more: false };
let ncPage = { items: [], total: 0, limit: NC_PAGE, offset: 0, has_more: false };

const filters = {
  // the chart
  material: "", characteristic: "", charQ: "",
  // inspection history
  hMaterial: "", hChar: "", hResult: "", hFrom: "", hTo: "", hOffset: 0,
  // specifications
  sQ: "", sMaterial: "", sOffset: 0,
  // non-conformances
  nStatus: NC_DEFAULT_SCOPE, nQ: "", nOffset: 0,
};

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
// The plant's clock, not the browser's: FS.fmt knows which zone this plant
// works in. See common.js.
const clock = (ts) => FS.fmt.clock(ts);
const stamp = (ts) => (ts ? FS.fmt.stamp(ts) : "");

/* The specification the chart is drawing, out of the chosen material's
   characteristics. Never out of "every spec in the plant" - that list is
   not on this screen any more. */
function currentSpec() {
  if (!filters.material || !filters.characteristic) return null;
  return chars.items.find((s) => s.characteristic === filters.characteristic) || null;
}

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

/* ---------- the filters, in the address bar ----------
   Every card's filter, not just the chart's: a supervisor who has narrowed
   the history to last night's failures on one characteristic can send that
   screen to the person who has to answer for it. The design chat reads the
   same state, so a critique arrives with the slice it was made about. */

const URL_KEYS = [
  ["material", "material"], ["characteristic", "characteristic"], ["char_q", "charQ"],
  ["h_material", "hMaterial"], ["h_char", "hChar"], ["h_result", "hResult"],
  ["h_from", "hFrom"], ["h_to", "hTo"],
  ["s_q", "sQ"], ["s_material", "sMaterial"],
  ["n_q", "nQ"],
];

function readUrl() {
  const p = new URL(window.location).searchParams;
  for (const [key, field] of URL_KEYS) if (p.get(key)) filters[field] = p.get(key);
  if (p.has("n_status")) filters.nStatus = p.get("n_status");
  filters.hOffset = Math.max(0, parseInt(p.get("h_offset") || "0", 10) || 0);
  filters.sOffset = Math.max(0, parseInt(p.get("s_offset") || "0", 10) || 0);
  filters.nOffset = Math.max(0, parseInt(p.get("n_offset") || "0", 10) || 0);
  $("#q-char").value = filters.charQ;
  $("#s-q").value = filters.sQ;
  $("#n-q").value = filters.nQ;
  $("#n-status").value = filters.nStatus;
  $("#h-result").value = filters.hResult;
  $("#h-from").value = filters.hFrom;
  $("#h-to").value = filters.hTo;
}

function writeUrl() {
  const url = new URL(window.location);
  const set = (key, value) => {
    if (value === "" || value === 0 || value === undefined || value === null) url.searchParams.delete(key);
    else url.searchParams.set(key, String(value));
  };
  for (const [key, field] of URL_KEYS) set(key, filters[field]);
  set("h_offset", filters.hOffset);
  set("s_offset", filters.sOffset);
  set("n_offset", filters.nOffset);
  if (filters.nStatus === NC_DEFAULT_SCOPE) url.searchParams.delete("n_status");
  else url.searchParams.set("n_status", filters.nStatus);
  history.replaceState(null, "", url);
}

/* A select of materials, built from the facets rather than from every
   specification in the plant. */
function fillMaterialSelect(select, keep, first) {
  const codes = facets.materials.map((m) => m.code);
  select.replaceChildren(new Option(first, ""),
    ...facets.materials.map((m) => new Option(`${m.code} (${m.specs})`, m.code)));
  select.value = codes.includes(keep) ? keep : "";
}

/* ---------- the chart ---------- */

const PAD = { l: 52, r: 14, t: 14, b: 26 };
const W = 900, H = 260;

function drawChart() {
  const svg = $("#chart");
  svg.textContent = "";

  const spec = currentSpec();
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
  const spec = currentSpec();
  if (!spec) { series = []; return; }
  const page = await api(`/quality/checks?material=${encodeURIComponent(spec.material)}`
    + `&characteristic=${encodeURIComponent(spec.characteristic)}&limit=${SERIES_POINTS}`);
  series = page.items || [];
}

async function showInstruction() {
  const box = document.querySelector("#wi-inline");
  if (!box) return;
  const spec = currentSpec();
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

/* ---------- measurements: one material's characteristics ----------
   The tab strip is the chosen material's characteristics, asked for by
   name - never the plant's. It says how many of the plant's it is showing,
   and what it has left out if a single material has more than a page. */

async function loadChars() {
  if (!filters.material) { chars = { items: [], total: 0 }; return; }
  // The search narrows the strip on the SERVER. "Way too many tags" was one
  // material's hundred characteristics laid end to end; typing "fill" is how
  // you get to the one you came for.
  const p = new URLSearchParams({ material: filters.material, limit: String(CHAR_PAGE) });
  if (filters.charQ) p.set("q", filters.charQ);
  chars = await api(`/quality/specs?${p}`);
  if (filters.characteristic && !chars.items.some((s) => s.characteristic === filters.characteristic)) {
    filters.characteristic = "";
  }
  if (!filters.characteristic && chars.items.length) filters.characteristic = chars.items[0].characteristic;
}

function drawTabs() {
  const tabs = $("#spec-tabs");
  tabs.textContent = "";
  const shown = chars.items.length;
  $("#q-scope").textContent = filters.material
    ? `${shown} of ${chars.total.toLocaleString()} characteristic${chars.total === 1 ? "" : "s"} `
      + `${filters.charQ ? "matching " : ""}on ${filters.material}, `
      + `of ${facets.specs_total.toLocaleString()} in the plant`
    : `${facets.materials_total.toLocaleString()} materials, `
      + `${facets.specs_total.toLocaleString()} characteristics in the plant`;
  for (const spec of chars.items) {
    const tab = el("button", "tab" + (spec.characteristic === filters.characteristic ? " active" : ""),
                   spec.characteristic);
    tab.type = "button";
    tab.addEventListener("click", async () => {
      filters.characteristic = spec.characteristic;
      writeUrl(); drawTabs();
      await loadSeries().catch(() => { series = []; });
      drawChart(); showInstruction();
    });
    tabs.appendChild(tab);
  }
}

/* ---------- specifications: the server's page ---------- */

function specQuery() {
  const p = new URLSearchParams({ limit: String(SPEC_PAGE), offset: String(filters.sOffset) });
  if (filters.sMaterial) p.set("material", filters.sMaterial);
  if (filters.sQ) p.set("q", filters.sQ);
  return `/quality/specs?${p}`;
}

function drawSpecs() {
  const page = specPage;
  filters.sOffset = page.offset;
  const narrowed = Boolean(filters.sQ || filters.sMaterial);
  $("#s-count").textContent = narrowed
    ? `— ${page.items.length} of ${page.total.toLocaleString()} matching, `
      + `${facets.specs_total.toLocaleString()} in the plant`
    : `— ${page.items.length} of ${page.total.toLocaleString()}`;
  const body = $("#specs tbody");
  body.textContent = "";
  for (const s of page.items) {
    const row = el("tr");
    const open = el("a", "obj", s.characteristic);
    open.href = `/dashboard/quality?material=${encodeURIComponent(s.material)}&characteristic=${encodeURIComponent(s.characteristic)}`;
    open.addEventListener("click", async (event) => {
      event.preventDefault();
      filters.material = s.material; filters.characteristic = s.characteristic;
      $("#q-material").value = s.material;
      writeUrl();
      await loadChars().catch(() => { chars = { items: [], total: 0 }; });
      drawTabs();
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
  FS.pager($("#s-pager"), page, (offset) => { filters.sOffset = offset; writeUrl(); refresh(); });
}

/* ---------- inspection history: the server's page ---------- */

function historyQuery() {
  const p = new URLSearchParams({ limit: String(HISTORY_PAGE), offset: String(filters.hOffset) });
  if (filters.hMaterial) p.set("material", filters.hMaterial);
  if (filters.hChar) p.set("characteristic", filters.hChar);
  if (filters.hResult) p.set("result", filters.hResult);
  // A date box is a plant day, not an instant: "from the 3rd" means from the
  // start of the 3rd, and "to the 3rd" means to the end of it.
  if (filters.hFrom) p.set("since", `${filters.hFrom}T00:00:00`);
  if (filters.hTo) p.set("until", `${filters.hTo}T23:59:59`);
  return `/quality/checks?${p}`;
}

function decorateWhen() {
  const toggle = $("#h-when-toggle");
  if (!filters.hFrom && !filters.hTo) {
    toggle.textContent = "When ▾";
    toggle.classList.remove("filtering");
    return;
  }
  const short = (d) => (d ? new Date(`${d}T00:00:00`).toLocaleDateString(undefined,
    { month: "short", day: "numeric" }) : "…");
  toggle.textContent = `${short(filters.hFrom)}–${short(filters.hTo)}`;
  toggle.classList.add("filtering");
}

function drawChecks() {
  decorateWhen();
  const body = $("#checks tbody");
  body.textContent = "";
  const page = historyPage;
  filters.hOffset = page.offset;
  const narrowed = Boolean(filters.hMaterial || filters.hChar || filters.hResult
                           || filters.hFrom || filters.hTo);
  $("#h-count").textContent = `— ${page.items.length} of ${(page.total || 0).toLocaleString()}${narrowed ? " matching" : ""}`;
  for (const c of page.items) {
    const row = el("tr");
    row.appendChild(el("td", "muted", stamp(c.ts)));
    row.appendChild(el("td", null, c.material));
    row.appendChild(el("td", null, c.characteristic));
    row.appendChild(el("td", "num", c.value));
    row.appendChild(el("td", c.result === "fail" ? "result-fail" : "result-pass", c.result));
    const order = el("td");
    if (c.order) {
      const link = el("a", "obj", c.order);
      link.href = `/dashboard/orders?q=${encodeURIComponent(c.order)}`;
      order.appendChild(link);
    } else {
      // Not "—": a check taken against no order is a different fact from a
      // check whose order we lost.
      order.appendChild(el("span", "muted", "no order"));
    }
    row.appendChild(order);
    row.appendChild(el("td", "mono", c.checked_by || ""));
    body.appendChild(row);
  }
  if (!page.items.length) {
    const row = el("tr"); const cell = el("td", "muted", "No checks match."); cell.colSpan = 7;
    row.appendChild(cell); body.appendChild(row);
  }
  FS.pager($("#h-pager"), page, (offset) => { filters.hOffset = offset; writeUrl(); refresh(); });
}

function fillHistoryChars() {
  const select = $("#h-char");
  const keep = filters.hChar;
  const names = charFacets.characteristics.map((c) => c.name);
  select.replaceChildren(new Option("Any characteristic", ""),
    ...charFacets.characteristics.map((c) => new Option(`${c.name} (${c.specs})`, c.name)));
  if (names.includes(keep)) select.value = keep; else { filters.hChar = ""; select.value = ""; }
}

/* ---------- non-conformances: by status, searchable, paged ---------- */

/* ---------- the life of a non-conformance ----------
   open → under review → a disposition on the material → closed. Each step
   carries who took it and when, and the screen shows that rather than a
   status word on its own: "closed" without a name is the row nobody can
   answer a question about later. The server says which steps are next, so
   the rules live in one place. */

// What the count line calls each scope. The dropdown's value is the list of
// states the server is asked for; this is the words a person reads.
const NC_SCOPES = {
  "open,under_review,dispositioned": "still open",
  "open,under_review": "awaiting a decision",
  dispositioned: "decided but not closed",
  closed: "closed",
  "": "in any status",
};

const DISPOSITIONS = {
  use_as_is: "use as is",
  rework: "rework",
  scrap: "scrap",
  return: "return to supplier",
};

const STEP_WORDS = {
  opened: "raised",
  under_review: "under review",
  dispositioned: "decided",
  closed: "closed",
};

function ncButton(nc, label, run) {
  const button = el("button", "ghost", label);
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await run();
      toast(`${nc.code}: ${label.toLowerCase().replace("…", "")}`);
      await refresh();
    } catch (err) {
      // These are supervisor actions; an operator being refused is the
      // system working, so say which it was.
      toast(err.message, "bad");
      button.disabled = false;
    }
  });
  return button;
}

function drawHistory(nc) {
  const wrap = el("ol", "nc-history");
  for (const step of nc.history || []) {
    const item = el("li", "muted small");
    item.append(STEP_WORDS[step.step] || step.step);
    // Unknown is not "system": these rows predate the MES asking who.
    item.append(step.by ? ` by ${step.by}` : " by — (not recorded)");
    if (step.at) item.append(` · ${stamp(step.at)}`);
    wrap.appendChild(item);
  }
  return wrap;
}

function openDisposition(nc) {
  $("#d-code").textContent = nc.code;
  $("#d-what").textContent = nc.description || "";
  $("#d-choice").value = "";
  $("#d-reason").value = "";
  $("#d-save").disabled = true;
  $("#disposition").classList.remove("hidden");
  $("#d-choice").focus();
}

function wireDisposition() {
  const ready = () => {
    $("#d-save").disabled = !$("#d-choice").value || !$("#d-reason").value.trim();
  };
  $("#d-choice").addEventListener("change", ready);
  $("#d-reason").addEventListener("input", ready);
  $("#d-cancel").addEventListener("click", () => $("#disposition").classList.add("hidden"));
  $("#d-save").addEventListener("click", async () => {
    const code = $("#d-code").textContent;
    $("#d-save").disabled = true;
    try {
      await api(`/quality/nonconformances/${code}/disposition`, { method: "POST", body: {
        disposition: $("#d-choice").value, reason: $("#d-reason").value.trim(),
      }});
      toast(`${code}: ${DISPOSITIONS[$("#d-choice").value]}`);
      $("#disposition").classList.add("hidden");
      await refresh();
    } catch (err) {
      toast(err.message, "bad");
      $("#d-save").disabled = false;
    }
  });
}

function ncQuery() {
  const p = new URLSearchParams({ limit: String(NC_PAGE), offset: String(filters.nOffset) });
  // Repeatable, one value each - "still open" is three states, and the API
  // answers a comma list with a 422.
  for (const one of filters.nStatus.split(",").filter(Boolean)) p.append("status", one);
  if (filters.nQ) p.set("q", filters.nQ);
  return `/quality/nonconformances?${p}`;
}

function drawNcs() {
  // The server's page, filtered there: a day of a busy plant is two
  // thousand non-conformances, and a month is a list no screen should fetch.
  const list = $("#ncs");
  list.textContent = "";
  const page = ncPage;
  filters.nOffset = page.offset;
  const what = NC_SCOPES[filters.nStatus] || "in any status";
  $("#n-count").textContent = `— ${page.items.length} of ${page.total.toLocaleString()} ${what}${filters.nQ ? " matching" : ""}`;
  if (!page.items.length) {
    list.appendChild(el("li", "muted", filters.nStatus.startsWith("open") && !filters.nQ
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

    // What the MES saw, when the MES raised it. The first question a
    // supervisor asks a machine-raised record is *why do you think so*.
    const why = nc.evidence;
    if (why && why.source === "spc") {
      const line = el("div", "muted small");
      line.append(`SPC rule ${why.rule} — ${why.what}`);
      if (why.material && why.characteristic) {
        const chart = el("a", "obj", `${why.material} ${why.characteristic}`);
        chart.href = `/dashboard/spc?spec=${encodeURIComponent(why.material)}|${encodeURIComponent(why.characteristic)}`;
        line.append(" on ", chart);
      }
      if (why.equipment) line.append(" at ", FS.link("machine", why.equipment));
      const w = why.window || {};
      if (w.centre !== undefined) {
        line.append(` · centre ${w.centre}, ±3σ [${w.lower}, ${w.upper}] from ${w.n} readings`);
      }
      body.appendChild(line);
    }

    if (nc.disposition) {
      const decided = el("div", "muted small");
      decided.append(`disposition: ${DISPOSITIONS[nc.disposition] || nc.disposition}`);
      if (nc.disposition_reason) decided.append(` — ${nc.disposition_reason}`);
      body.appendChild(decided);
    }
    body.appendChild(drawHistory(nc));
    li.appendChild(body);

    // Gated: showing a button that will 403 is worse than not showing
    // it. This reverses the earlier let-the-403-do-the-talking decision.
    const steps = nc.next_steps || [];
    if (!steps.length || !window.FS || !FS.can("quality.close_nc")) {
      list.appendChild(li);
      continue;
    }
    const actions = el("div", "nc-actions");
    if (steps.includes("review")) {
      actions.appendChild(ncButton(nc, "Take under review", () =>
        api(`/quality/nonconformances/${nc.code}/review`, { method: "POST" })));
    }
    if (steps.includes("disposition")) {
      const decide = el("button", "ghost", "Decide…");
      decide.type = "button";
      decide.addEventListener("click", () => openDisposition(nc));
      actions.appendChild(decide);
    }
    if (steps.includes("close")) {
      actions.appendChild(ncButton(nc, "Close", () =>
        api(`/quality/nonconformances/${nc.code}/close`, { method: "POST" })));
    }
    li.appendChild(actions);
    list.appendChild(li);
  }
  FS.pager($("#n-pager"), page, (offset) => { filters.nOffset = offset; writeUrl(); refresh(); });
}

/* ---------- refresh ---------- */

async function refresh() {
  try {
    const [f, sp, h, n, all, failed, stillOpen] = await Promise.all([
      api("/quality/specs/facets"),
      api(specQuery()),
      api(historyQuery()),
      api(ncQuery()),
      api("/quality/checks?limit=1"),
      api("/quality/checks?limit=1&result=fail"),
      // Not closed, which is three states now. A tile counting only the
      // untouched ones would read lower every time somebody started work.
      api("/quality/nonconformances?status=open&status=under_review"
          + "&status=dispositioned&limit=1"),
    ]);
    facets = f;
    specPage = sp;
    historyPage = h;
    ncPage = n;

    // The chart opens on something: the material asked for in the address
    // bar, else the first material that has a specification at all.
    if (!filters.material && facets.materials.length) filters.material = facets.materials[0].code;
    if (filters.material && !facets.materials.some((m) => m.code === filters.material)) {
      filters.material = facets.materials.length ? facets.materials[0].code : "";
      filters.characteristic = "";
    }
    await loadChars().catch(() => { chars = { items: [], total: 0 }; });

    // The history's characteristic list narrows to its material when one is
    // chosen; otherwise it is the plant's.
    charFacets = filters.hMaterial
      ? await api(`/quality/specs/facets?material=${encodeURIComponent(filters.hMaterial)}`).catch(() => facets)
      : facets;

    fillMaterialSelect($("#q-material"), filters.material, "Choose a material");
    fillMaterialSelect($("#h-material"), filters.hMaterial, "Any material");
    fillMaterialSelect($("#s-material"), filters.sMaterial, "Any material");
    fillHistoryChars();

    const total = all.total || 0;
    $("#kpi-checks").textContent = total.toLocaleString();
    $("#kpi-pass").textContent = total ? (((total - (failed.total || 0)) / total) * 100).toFixed(1) + "%" : "—";
    $("#kpi-ncs").textContent = stillOpen && stillOpen.total !== undefined ? stillOpen.total.toLocaleString() : "—";
    $("#kpi-specs").textContent = (facets.specs_total || 0).toLocaleString();

    await loadSeries().catch(() => { series = []; });
    writeUrl();
    drawTabs(); drawChart(); drawSpecs(); drawChecks(); drawNcs();
    window.__fsmesPageData = {
      specs_total: facets.specs_total, materials_total: facets.materials_total,
      chart: { material: filters.material, characteristic: filters.characteristic,
               points: series.length },
      series: series.slice(0, 40),
      specifications: { total: specPage.total, shown: specPage.items.length },
      history: { total: historyPage.total, shown: historyPage.items.length },
      nonconformances: ncPage.items.slice(0, 40),
      filters: { ...filters },
    };
    showInstruction();
    live(true);
  } catch (err) {
    live(false);
  }
}

/* ---------- controls ---------- */

$("#q-material").addEventListener("change", async () => {
  filters.material = $("#q-material").value;
  filters.characteristic = "";
  await loadChars().catch(() => { chars = { items: [], total: 0 }; });
  writeUrl(); drawTabs();
  await loadSeries().catch(() => { series = []; });
  drawChart(); showInstruction();
});

let charTyping = null;
$("#q-char").addEventListener("input", () => {
  clearTimeout(charTyping);
  charTyping = setTimeout(async () => {
    filters.charQ = $("#q-char").value.trim();
    await loadChars().catch(() => { chars = { items: [], total: 0 }; });
    writeUrl(); drawTabs();
    await loadSeries().catch(() => { series = []; });
    drawChart(); showInstruction();
  }, 250);
});

let specTyping = null;
$("#s-q").addEventListener("input", () => {
  clearTimeout(specTyping);
  specTyping = setTimeout(() => {
    filters.sQ = $("#s-q").value.trim(); filters.sOffset = 0; writeUrl(); refresh();
  }, 250);
});
$("#s-material").addEventListener("change", () => {
  filters.sMaterial = $("#s-material").value; filters.sOffset = 0; writeUrl(); refresh();
});

$("#h-material").addEventListener("change", () => {
  filters.hMaterial = $("#h-material").value; filters.hChar = ""; filters.hOffset = 0; writeUrl(); refresh();
});
$("#h-char").addEventListener("change", () => { filters.hChar = $("#h-char").value; filters.hOffset = 0; writeUrl(); refresh(); });
$("#h-result").addEventListener("change", () => { filters.hResult = $("#h-result").value; filters.hOffset = 0; writeUrl(); refresh(); });
FS.popover("#h-when-toggle", "#h-when-pop");
$("#h-from").addEventListener("change", () => { filters.hFrom = $("#h-from").value; filters.hOffset = 0; writeUrl(); refresh(); });
$("#h-to").addEventListener("change", () => { filters.hTo = $("#h-to").value; filters.hOffset = 0; writeUrl(); refresh(); });
$("#h-clear").addEventListener("click", () => {
  filters.hMaterial = ""; filters.hChar = ""; filters.hResult = "";
  filters.hFrom = ""; filters.hTo = ""; filters.hOffset = 0;
  $("#h-material").value = ""; $("#h-result").value = "";
  $("#h-from").value = ""; $("#h-to").value = "";
  writeUrl(); refresh();
});

$("#n-status").addEventListener("change", () => { filters.nStatus = $("#n-status").value; filters.nOffset = 0; writeUrl(); refresh(); });
let ncTyping = null;
$("#n-q").addEventListener("input", () => {
  clearTimeout(ncTyping);
  ncTyping = setTimeout(() => { filters.nQ = $("#n-q").value.trim(); filters.nOffset = 0; writeUrl(); refresh(); }, 250);
});

(async function boot() {
  // Know who is asking before the first draw, or capability-gated buttons
  // appear one refresh late.
  await FS.whoami().catch(() => {});
  wireDisposition();
  readUrl();
  await refresh();
  setInterval(refresh, REFRESH_MS);
})();
