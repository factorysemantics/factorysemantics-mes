/* Engineering › Master data: what the plant is made of.

   Equipment (any depth, with the cost center a node bills to), materials
   and their bills of material with the operation each component enters at,
   quality specifications, and people. Everything here was API-only until
   this screen; the routings editor stays on Admin where it was. */

const { $, el, api, fmt, toast } = window.FS;

let equipment = [];
let materials = [];
let specs = [];
let people = [];
let chosenMaterial = null;
let timer = null;

/* Every table here is the plant's whole list, filtered and paged on the
   screen: a 108-station plant has 138 equipment nodes, 127 specifications
   and 300 people, and a wall of rows is not how anyone finds one. */
const PAGE = 25;
const filters = { eqQ: "", eqLevel: "", eqOffset: 0, matQ: "", matType: "", matOffset: 0,
                  specQ: "", specMaterial: "", specOffset: 0, pQ: "", pRole: "", pOffset: 0 };
const has = (text, q) => !q || text.toLowerCase().includes(q.toLowerCase());

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

function fill(select, items, label, keepFirst) {
  const keep = select.value;
  select.replaceChildren();
  if (keepFirst) select.appendChild(new Option(keepFirst, ""));
  for (const it of items) select.appendChild(new Option(label(it), it.code));
  if ([...select.options].some((o) => o.value === keep)) select.value = keep;
}

/* ---------- equipment ---------- */

async function loadEquipment() {
  equipment = await api("/masterdata/equipment");
  drawEquipment();
  fill($("#eq-parent"), equipment.filter((e) => e.level !== "work_unit"), (e) => `${e.code} (${e.level.replace("_", " ")})`, "no parent");
}

function drawEquipment() {
  const matching = equipment.filter((e) =>
    (!filters.eqLevel || e.level === filters.eqLevel) && has(`${e.code} ${e.name}`, filters.eqQ));
  const page = FS.clientPage(matching, filters.eqOffset, PAGE);
  filters.eqOffset = page.offset;
  const body = $("#eq-table tbody");
  body.replaceChildren();
  for (const e of page.items) {
    const tr = el("tr");
    const code = el("td", "code");
    code.append(e.level === "work_unit" ? FS.link("machine", e.code) : e.level === "work_center" ? FS.link("line", e.code) : e.code);
    tr.append(code, el("td", null, e.name), el("td", "muted", e.level.replace("_", " ")),
              el("td", "muted", e.parent || "—"), el("td", "num", e.ideal_cycle_seconds ?? "—"),
              el("td", "muted", e.cost_center || "—"));
    body.append(tr);
  }
  if (!page.items.length) { const tr = el("tr"); const td = el("td", "muted", "No equipment matches."); td.colSpan = 6; tr.append(td); body.append(tr); }
  $("#eq-count").textContent = FS.countText(page, equipment.length);
  FS.pager($("#eq-pager"), page, (offset) => { filters.eqOffset = offset; drawEquipment(); });
}

function wireEquipment() {
  $("#eq-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api("/masterdata/equipment", { method: "POST", body: {
        code: $("#eq-code").value.trim().toUpperCase(), name: $("#eq-name").value.trim(),
        level: $("#eq-level").value, parent: $("#eq-parent").value || null,
        ideal_cycle_seconds: $("#eq-cycle").value ? Number($("#eq-cycle").value) : null,
        cost_center: $("#eq-cc").value.trim() || null } });
      toast(`${out.code} added`);
      $("#eq-form").reset();
      await loadEquipment();
    } catch (err) { toast(err.message, "bad"); }
  });
}

/* ---------- materials and BOM ---------- */

async function loadMaterials() {
  materials = await api("/masterdata/materials");
  drawMaterials();
  fill($("#bom-component"), materials, (m) => `${m.code} — ${m.name}`);
  fill($("#spec-material"), materials, (m) => `${m.code} — ${m.name}`);
  fill($("#spec-filter-material"), materials, (m) => m.code, "Any material");
}

function drawMaterials() {
  const matching = materials.filter((m) =>
    (!filters.matType || m.type === filters.matType) && has(`${m.code} ${m.name}`, filters.matQ));
  const page = FS.clientPage(matching, filters.matOffset, PAGE);
  filters.matOffset = page.offset;
  const body = $("#mat-table tbody");
  body.replaceChildren();
  for (const m of page.items) {
    const tr = el("tr", m.code === chosenMaterial ? "selected" : null);
    tr.append(el("td", "code", m.code), el("td", null, m.name), el("td", "muted", m.unit), el("td", "muted", m.type));
    tr.addEventListener("click", () => { chosenMaterial = m.code; loadBom().catch(fail); drawMaterials(); });
    body.append(tr);
  }
  if (!page.items.length) { const tr = el("tr"); const td = el("td", "muted", "No material matches."); td.colSpan = 4; tr.append(td); body.append(tr); }
  $("#mat-count").textContent = FS.countText(page, materials.length);
  FS.pager($("#mat-pager"), page, (offset) => { filters.matOffset = offset; drawMaterials(); });
}

async function loadBom() {
  if (!chosenMaterial) return;
  const items = await api(`/masterdata/materials/${encodeURIComponent(chosenMaterial)}/bom`);
  $("#bom-title").textContent = `— ${chosenMaterial}`;
  const body = $("#bom-table tbody");
  body.replaceChildren();
  if (!items.length) { const tr = el("tr"); const td = el("td", "muted", "No components."); td.colSpan = 3; tr.append(td); body.append(tr); }
  for (const i of items) {
    const tr = el("tr");
    tr.append(el("td", "code", i.component), el("td", "num", fmt.qty(i.quantity)),
              el("td", "muted", i.operation_seq === null ? "anywhere (unmodelled)" : `op ${i.operation_seq}`));
    body.append(tr);
  }
}

function wireMaterials() {
  $("#mat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api("/masterdata/materials", { method: "POST", body: {
        code: $("#mat-code").value.trim().toUpperCase(), name: $("#mat-name").value.trim(),
        unit: $("#mat-unit").value.trim() || "ea", type: $("#mat-type").value } });
      toast(`${out.code} added`);
      $("#mat-form").reset();
      await loadMaterials();
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#bom-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!chosenMaterial) { toast("Pick a material first.", "bad"); return; }
    try {
      await api(`/masterdata/materials/${encodeURIComponent(chosenMaterial)}/bom`, { method: "POST", body: {
        component: $("#bom-component").value, quantity: Number($("#bom-qty").value),
        operation_seq: $("#bom-seq").value ? Number($("#bom-seq").value) : null } });
      toast("Component added.");
      $("#bom-qty").value = ""; $("#bom-seq").value = "";
      await loadBom();
    } catch (err) { toast(err.message, "bad"); }
  });
}

/* ---------- specifications ---------- */

async function loadSpecs() {
  specs = await api("/quality/specs");
  drawSpecs();
}

function drawSpecs() {
  const matching = specs.filter((s) =>
    (!filters.specMaterial || s.material === filters.specMaterial) && has(`${s.material} ${s.characteristic}`, filters.specQ));
  const page = FS.clientPage(matching, filters.specOffset, PAGE);
  filters.specOffset = page.offset;
  const body = $("#spec-table tbody");
  body.replaceChildren();
  for (const s of page.items) {
    const tr = el("tr");
    tr.append(el("td", "code", s.material), el("td", null, s.characteristic),
              el("td", "num", s.min_value ?? "—"), el("td", "num", s.max_value ?? "—"), el("td", "muted", s.unit || ""));
    const link = el("td");
    const a = el("a", "obj", "SPC");
    a.href = `/dashboard/spc?spec=${encodeURIComponent(s.material)}|${encodeURIComponent(s.characteristic)}`;
    link.append(a);
    tr.append(link);
    body.append(tr);
  }
  if (!page.items.length) { const tr = el("tr"); const td = el("td", "muted", "No specification matches."); td.colSpan = 6; tr.append(td); body.append(tr); }
  $("#spec-count").textContent = FS.countText(page, specs.length);
  FS.pager($("#spec-pager"), page, (offset) => { filters.specOffset = offset; drawSpecs(); });
}

function wireSpecs() {
  $("#spec-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/quality/specs", { method: "POST", body: {
        material: $("#spec-material").value, characteristic: $("#spec-char").value.trim(),
        unit: $("#spec-unit").value.trim(),
        min_value: $("#spec-min").value ? Number($("#spec-min").value) : null,
        max_value: $("#spec-max").value ? Number($("#spec-max").value) : null } });
      toast("Specification added.");
      $("#spec-form").reset();
      await loadSpecs();
    } catch (err) { toast(err.message, "bad"); }
  });
}

/* ---------- people ---------- */

async function loadPeople() {
  people = await api("/masterdata/personnel");
  const roles = [...new Set(people.map((p) => p.role))].sort();
  fill($("#person-filter-role"), roles.map((r) => ({ code: r })), (r) => r.code, "Any role");
  drawPeople();
}

function drawPeople() {
  const matching = people.filter((p) =>
    (!filters.pRole || p.role === filters.pRole) && has(`${p.code} ${p.name}`, filters.pQ));
  const page = FS.clientPage(matching, filters.pOffset, PAGE);
  filters.pOffset = page.offset;
  const body = $("#person-table tbody");
  body.replaceChildren();
  for (const p of page.items) {
    const tr = el("tr");
    tr.append(el("td", "code", p.code), el("td", null, p.name), el("td", "muted", p.role));
    body.append(tr);
  }
  if (!page.items.length) { const tr = el("tr"); const td = el("td", "muted", "Nobody matches."); td.colSpan = 3; tr.append(td); body.append(tr); }
  $("#person-count").textContent = FS.countText(page, people.length);
  FS.pager($("#person-pager"), page, (offset) => { filters.pOffset = offset; drawPeople(); });
}

function wireFilters() {
  const bind = (id, key, draw) => $(`#${id}`).addEventListener("input", (e) => {
    filters[key] = e.target.value; filters[key.replace(/Q$|Level$|Type$|Material$|Role$/, "Offset")] = 0; draw();
  });
  bind("eq-q", "eqQ", drawEquipment); bind("eq-filter-level", "eqLevel", drawEquipment);
  bind("mat-q", "matQ", drawMaterials); bind("mat-filter-type", "matType", drawMaterials);
  bind("spec-q", "specQ", drawSpecs); bind("spec-filter-material", "specMaterial", drawSpecs);
  bind("person-q", "pQ", drawPeople); bind("person-filter-role", "pRole", drawPeople);
}

function wirePeople() {
  $("#person-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/masterdata/personnel", { method: "POST", body: {
        code: $("#person-code").value.trim().toUpperCase(), name: $("#person-name").value.trim(),
        role: $("#person-role").value.trim() || "operator" } });
      toast("Person added.");
      $("#person-form").reset();
      await loadPeople();
    } catch (err) { toast(err.message, "bad"); }
  });
}

/* ---------- tabs ---------- */

const LOADERS = {
  equipment: loadEquipment,
  materials: async () => { await loadMaterials(); await loadBom(); },
  specs: async () => { await loadMaterials(); await loadSpecs(); },
  people: loadPeople,
};

function onTab(name) {
  clearInterval(timer);
  const run = () => (LOADERS[name] || loadEquipment)().then(() => live(true)).catch(fail);
  run();
  timer = setInterval(run, 20000);
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  wireEquipment(); wireMaterials(); wireSpecs(); wirePeople(); wireFilters();
  FS.tabs.init(document, onTab);
})().catch(fail);
