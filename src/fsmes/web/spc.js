/* Quality › SPC: the individuals chart, with the two questions kept apart.

   Is the process stable (control limits from its own variation, four
   Western Electric rules)? Is it capable (Cp, Cpk against the specification,
   withheld while unstable)? The Quality screen plots readings against spec;
   this one shows whether the process is behaving, which spec limits alone
   never say. Drawn with the shared kit, from the API's own numbers. */

const { $, el, api, fmt, kit } = window.FS;

const REFRESH_MS = 10000;
let specs = [];
let chosen = null;
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

const num = (v, d = 3) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));

function draw(data) {
  const host = $("#chart");
  host.replaceChildren();
  if (!data.points.length) return kit.empty(host, "No readings for this characteristic yet.");

  const left = 52, right = 14, top = 12, bottom = 26;
  const width = Math.max(host.clientWidth || 900, 620);
  const height = 260;
  const plot = width - left - right;
  const plotH = height - top - bottom;
  const values = data.points.map((p) => p.value);
  const guides = [data.lower_spec, data.upper_spec,
                  data.control && data.control.lower, data.control && data.control.upper]
    .filter((v) => v !== null && v !== undefined);
  let lo = Math.min(...values, ...guides), hi = Math.max(...values, ...guides);
  if (hi === lo) { hi += 0.5; lo -= 0.5; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const x = (i) => left + (i / Math.max(values.length - 1, 1)) * plot;
  const y = (v) => top + plotH - ((v - lo) / (hi - lo)) * plotH;

  const chart = kit.svg(width, height);
  for (let i = 0; i <= 3; i++) {
    const v = lo + ((hi - lo) * i) / 3;
    kit.add(chart, "line", { x1: left, y1: y(v), x2: left + plot, y2: y(v), class: "grid-line" });
    kit.add(chart, "text", { x: left - 6, y: y(v) + 3, class: "axis", "text-anchor": "end" }, v.toFixed(Math.abs(hi - lo) < 10 ? 2 : 0));
  }
  const guide = (v, cls, label) => {
    if (v === null || v === undefined) return;
    kit.add(chart, "line", { x1: left, y1: y(v), x2: left + plot, y2: y(v), class: cls });
    kit.add(chart, "text", { x: left + plot - 4, y: y(v) - 3, class: "axis", "text-anchor": "end" }, `${label} ${v}`);
  };
  guide(data.lower_spec, "spec-line", "LSL");
  guide(data.upper_spec, "spec-line", "USL");
  if (data.control) {
    guide(data.control.lower, "limit-line", "LCL");
    guide(data.control.upper, "limit-line", "UCL");
    guide(data.control.centre, "centre-line", "x̄");
  }
  const flagged = new Set(data.signals.flatMap((s) => s.points || s.indexes || (s.index !== undefined ? [s.index] : [])));
  kit.add(chart, "polyline", { points: values.map((v, i) => `${x(i)},${y(v)}`).join(" "), class: "trend-line" });
  values.forEach((v, i) => {
    const dot = kit.add(chart, "circle", { cx: x(i), cy: y(v), r: flagged.has(i) ? 4.5 : 2.5,
      class: flagged.has(i) ? "spc-flag" : "spc-dot" });
    kit.add(dot, "title", {}, `${v}${data.unit ? " " + data.unit : ""} · ${kit.utc(data.points[i].ts).toLocaleString()}`);
  });
  kit.add(chart, "text", { x: left + 4, y: top + 10, class: "axis" }, `${data.material} ${data.characteristic}${data.unit ? ` (${data.unit})` : ""}`);
  host.appendChild(chart);
}

async function load() {
  if (!chosen) return;
  const [material, characteristic] = chosen.split("|");
  const data = await api(`/quality/spc/${encodeURIComponent(material)}/${encodeURIComponent(characteristic)}`);
  $("#f-n").textContent = data.n;
  $("#f-centre").textContent = data.control ? num(data.control.centre, 2) : "—";
  $("#f-sigma").textContent = data.control ? num(data.control.sigma, 3) : "—";
  // Capability is withheld while the chart is out of control: the verdict
  // says the number is not meaningful, so the facts row must not print it.
  // The value stays in the tooltip for the engineer who wants it anyway.
  const cap = data.capability;
  const unstable = data.stable === false;
  const capCell = (id, value, absent) => {
    const node = $(id);
    node.title = "";
    if (!cap || value === null || value === undefined) { node.textContent = absent; return; }
    if (unstable) {
      node.textContent = "withheld";
      node.title = `${num(value, 2)} - not meaningful while the process is out of control`;
      return;
    }
    node.textContent = num(value, 2);
  };
  capCell("#f-cp", cap && cap.cp, "withheld");
  capCell("#f-cpk", cap && cap.cpk, "withheld");
  capCell("#f-pp", cap && cap.pp, "—");
  const verdict = $("#verdict");
  verdict.textContent = data.verdict || data.note || "—";
  verdict.className = "verdict " + (data.stable === false ? "bad" : data.capability && data.capability.cpk >= 1.33 ? "good" : "");
  $("#chart-note").textContent = data.control ? "" : `— ${data.note || "no control limits"}`;
  draw(data);
  const body = $("#signals tbody");
  body.replaceChildren();
  if (!data.signals.length) {
    const tr = el("tr"); const td = el("td", "muted", data.control ? "No rule fired. The process is in control." : "—"); td.colSpan = 3; tr.append(td); body.append(tr);
  }
  for (const s of data.signals) {
    const tr = el("tr");
    tr.append(el("td", "code", `rule ${s.rule}`));
    const where = s.points || s.indexes || (s.index !== undefined ? [s.index] : []);
    tr.append(el("td", "muted small", where.length ? `reading ${where.map((i) => i + 1).join(", ")}` : (s.at !== undefined ? `reading ${s.at + 1}` : "")));
    tr.append(el("td", null, s.description || s.meaning || s.what || JSON.stringify(s)));
    body.append(tr);
  }
  $("#signal-count").textContent = `— ${data.signals.length}`;
  window.__fsmesPageData = data;
}

(async function boot() {
  await FS.whoami().catch(() => {});
  specs = await api("/quality/specs");
  const select = $("#spec");
  // Grouped by material: 127 specifications in one flat list is a scroll,
  // not a choice.
  const byMaterial = new Map();
  for (const s of specs) { if (!byMaterial.has(s.material)) byMaterial.set(s.material, []); byMaterial.get(s.material).push(s); }
  select.replaceChildren(...[...byMaterial.entries()].map(([material, rows]) => {
    const group = document.createElement("optgroup");
    group.label = material;
    group.append(...rows.map((s) => new Option(s.characteristic, `${s.material}|${s.characteristic}`)));
    return group;
  }));
  const wanted = new URL(location).searchParams.get("spec");
  chosen = specs.some((s) => `${s.material}|${s.characteristic}` === wanted) ? wanted : (specs.length ? `${specs[0].material}|${specs[0].characteristic}` : null);
  if (!chosen) return fail(new Error("No quality specification is defined yet."));
  select.value = chosen;
  select.addEventListener("change", () => { chosen = select.value; load().then(() => live(true)).catch(fail); });
  const run = () => load().then(() => live(true)).catch(fail);
  await run();
  timer = setInterval(run, REFRESH_MS);
})().catch(fail);
