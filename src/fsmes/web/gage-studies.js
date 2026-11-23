/* Quality › Gauge studies: is it the gauge, the people, or the parts?

   A gauge study (gage R&R) splits the spread of a gauge's readings into
   repeatability (the gauge), reproducibility (the people reading it) and
   part-to-part (the parts). Every number on this page is the API's: the
   ANOVA, the percentages, the band and the verdict are computed in
   services/gage_rr.py, and a verdict the study cannot stand behind comes
   back withheld, with the reasons, and is drawn that way. */

const { $, el, api, toast } = window.FS;

const REFRESH_MS = 15000;
let list = null;
let selected = new URLSearchParams(location.search).get("study");
let current = null;

function fail(error) {
  FS.live(false);
  const banner = $("#banner");
  banner.textContent = error.message;
  banner.classList.remove("hidden");
}

const SOURCES = {
  repeatability: "Repeatability — the gauge",
  reproducibility: "Reproducibility — the people",
  appraiser: "  of which appraiser",
  interaction: "  of which appraiser × part",
  gauge_rr: "Gauge R&R",
  part: "Part-to-part",
  total: "Total",
};

const pct = (v) => (v === null || v === undefined ? "—" : `${v.toFixed(1)} %`);
const num = (v) => (v === null || v === undefined ? "—" : v.toPrecision(4));

async function loadList() {
  list = await api("/quality/gage-studies");
  const body = $("#studies-table tbody");
  body.replaceChildren();
  if (!list.studies.length) {
    const tr = el("tr"); const td = el("td", "muted", "No gauge study yet."); td.colSpan = 6; tr.append(td); body.append(tr);
  }
  for (const s of list.studies) {
    const tr = el("tr", s.code === selected ? "selected" : null);
    tr.append(el("td", "code", s.code), el("td", "code", s.gauge),
      el("td", null, `${s.material} · ${s.characteristic}`),
      el("td", "muted small", `${s.parts} parts × ${s.appraisers.length} appraisers × ${s.trials} trials`),
      el("td", null, s.state), el("td", "num", `${s.readings} of ${s.expected}`));
    tr.addEventListener("click", () => { selected = s.code; loadList().catch(fail); loadStudy().catch(fail); });
    body.append(tr);
  }
  $("#studies-count").textContent = `${list.total} in total`;
  const by = (state) => list.studies.filter((s) => s.state === state).length;
  $("#kpi-studies").textContent = list.total;
  $("#kpi-complete").textContent = by("complete");
  $("#kpi-collecting").textContent = by("collecting");
  $("#kpi-planned").textContent = by("planned");
  if (!selected && list.studies.length) { selected = list.studies[0].code; await loadStudy(); }
}

function drawVerdict(s) {
  const box = $("#study-verdict");
  box.replaceChildren();
  const band = s.analysis ? s.analysis.band : null;
  if (s.verdict) {
    box.append(el("p", band === "acceptable" ? "good" : band === "marginal" ? "warn" : "alarm", s.verdict));
  } else {
    box.append(el("p", "muted", "Not judged. " + (s.note || "")));
  }
  const why = $("#study-withheld");
  why.replaceChildren(...(s.withheld || []).map((w) => el("li", "warn", w)));
}

function drawComponents(s) {
  const body = $("#components-table tbody");
  body.replaceChildren();
  $("#study-chart").replaceChildren();
  if (!s.analysis) {
    const tr = el("tr"); const td = el("td", "muted", "Computed once every reading is in."); td.colSpan = 6; tr.append(td); body.append(tr);
    return;
  }
  for (const c of s.analysis.components) {
    const tr = el("tr", c.source === "gauge_rr" ? "selected" : null);
    tr.append(el("td", null, SOURCES[c.source] || c.source), el("td", "num", num(c.variance)),
      el("td", "num", num(c.sd)), el("td", "num", pct(c.pct_contribution)),
      el("td", "num", pct(c.pct_study_var)), el("td", "num", pct(c.pct_tolerance)));
    body.append(tr);
  }
  /* One chart: % of study variation by source - the one a person reads
     fastest. The rows are the envelope's own figures. */
  const rows = s.analysis.components
    .filter((c) => ["repeatability", "reproducibility", "part"].includes(c.source))
    .map((c) => ({ label: SOURCES[c.source].split(" — ")[0], value: c.pct_study_var }));
  FS.kit.draw($("#study-chart"), "bars", { rows, rows_total: rows.length },
    { labelKey: "label", valueKey: "value", noun: "source", labelWidth: 130,
      title: "% of study variation, by source" });
  $("#study-convention").textContent =
    `${s.analysis.method}; interaction ${s.analysis.interaction_significant ? "kept" : "pooled into repeatability"}` +
    ` (p = ${s.analysis.interaction_p}, α ${s.analysis.interaction_alpha}); ` +
    `${s.analysis.distinct_categories ?? "—"} distinct categories. ${s.convention}.`;
}

function drawGrid(s) {
  const head = $("#grid-table thead");
  const body = $("#grid-table tbody");
  const taken = new Map(s.grid.map((r) => [`${r.part}|${r.appraiser}|${r.trial}`, r]));
  const hr = el("tr"); hr.append(el("th", null, "Part"));
  for (const a of s.appraisers) for (let t = 1; t <= s.trials; t += 1) hr.append(el("th", "num", `${a} · ${t}`));
  head.replaceChildren(hr);
  body.replaceChildren();
  const open = s.state !== "complete";
  for (let p = 1; p <= s.parts; p += 1) {
    const tr = el("tr"); tr.append(el("td", "code", String(p)));
    for (const a of s.appraisers) {
      for (let t = 1; t <= s.trials; t += 1) {
        const td = el("td", "num");
        const have = taken.get(`${p}|${a}|${t}`);
        if (have) {
          td.textContent = String(have.value);
          td.title = `${have.recorded_by}, ${FS.fmt.clock ? FS.fmt.clock(have.recorded_at) : have.recorded_at}`;
        } else if (open) {
          const input = el("input");
          Object.assign(input, { type: "number", step: "any", size: 6 });
          input.dataset.part = p; input.dataset.appraiser = a; input.dataset.trial = t;
          input.setAttribute("aria-label", `part ${p}, ${a}, trial ${t}`);
          td.append(input);
        } else td.textContent = "—";
        tr.append(td);
      }
    }
    body.append(tr);
  }
  $("#grid-count").textContent = `${s.readings} of ${s.expected} in`;
}

async function loadStudy() {
  if (!selected) return;
  const s = await api(`/quality/gage-studies/${encodeURIComponent(selected)}`);
  current = s;
  $("#study-panel").classList.remove("hidden");
  $("#study-title").textContent = s.code;
  const tol = s.lower_spec !== null && s.upper_spec !== null ? `, tolerance ${s.lower_spec} to ${s.upper_spec} ${s.unit}` : ", no tolerance";
  $("#study-sub").textContent = `— ${s.gauge} on ${s.material} · ${s.characteristic}${tol}; ${s.state}`;
  drawVerdict(s);
  drawComponents(s);
  // Never redraw the grid under somebody typing in it.
  const typing = [...document.querySelectorAll("#grid-table input")].some((i) => i.value !== "");
  if (!typing) drawGrid(s);
  window.__fsmesPageData = s;
}

async function loadGauges() {
  const register = await api("/quality/gauges");
  $("#plan-gauge").replaceChildren(...register.gauges.map((g) => new Option(`${g.code} — ${g.name}`, g.code)));
}

function wire() {
  $("#grid-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const readings = [...document.querySelectorAll("#grid-table input")]
      .filter((i) => i.value !== "")
      .map((i) => ({ part: Number(i.dataset.part), appraiser: i.dataset.appraiser,
                     trial: Number(i.dataset.trial), value: Number(i.value) }));
    if (!readings.length || !current) { $("#grid-note").textContent = "type a reading first"; return; }
    try {
      const out = await api(`/quality/gage-studies/${encodeURIComponent(current.code)}/readings`,
        { method: "POST", body: { readings } });
      toast(`${out.recorded} reading(s) saved · ${out.still_to_take} still to take`);
      $("#grid-note").textContent = "";
      document.querySelectorAll("#grid-table input").forEach((i) => { i.value = ""; });
      await loadStudy(); await loadList();
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#plan-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api("/quality/gage-studies", { method: "POST", body: {
        gauge: $("#plan-gauge").value, material: $("#plan-material").value.trim(),
        characteristic: $("#plan-characteristic").value.trim(),
        appraisers: $("#plan-appraisers").value.split(",").map((a) => a.trim()).filter(Boolean),
        parts: Number($("#plan-parts").value), trials: Number($("#plan-trials").value),
        purpose: $("#plan-purpose").value.trim() || null } });
      toast(`${out.code} planned: ${out.expected} readings to take`);
      selected = out.code;
      await loadList(); await loadStudy();
    } catch (err) { toast(err.message, "bad"); }
  });
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  wire();
  await loadGauges().catch(() => {});
  const run = () => loadList().then(loadStudy).then(() => FS.live(true)).catch(fail);
  await run();
  setInterval(run, REFRESH_MS);
})().catch(fail);
