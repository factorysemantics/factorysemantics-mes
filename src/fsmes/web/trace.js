/* Quality › Trace: one serial, or one lot.

   Serialisation had six working endpoints and no screen. A serial shows
   what is inside it (the packing tree), what went into it (the lots, and
   where they entered), and lets a supervisor hold it - with everything
   inside, because holding a pallet without holding the cases on it holds
   nothing. A lot shows where it went: the orders that drew on it and the
   lots they made - the answer every line that issues lots has - and, where
   the line serialises, the packages a warehouse can pull. */

const { $, el, api, fmt, toast } = window.FS;

let current = { kind: null, code: null };

function fail(error) {
  FS.live(false);
  const banner = $("#banner");
  banner.textContent = error.message;
  banner.classList.remove("hidden");
}

function remember(kind, code) {
  current = { kind, code };
  const url = new URL(location);
  url.searchParams.delete("serial"); url.searchParams.delete("lot");
  url.searchParams.set(kind, code);
  history.replaceState(null, "", url);
  document.title = `${code} — FactorySemantics MES`;
}

/* ---------- a serial ---------- */

/* The tree is drawn to the server's limit per node and counted in full:
   a pallet of 240 packs shows its first fifty and says "and 190 more",
   because six thousand rows is not a screen anybody reads. */
function node(u) {
  const li = el("li");
  const row = el("div", "node");
  row.append(FS.link("serial", u.serial), el("span", "muted", u.material), el("span", `pill ${u.status === "quarantined" || u.status === "scrapped" ? "down" : "running"}`, u.status));
  if (u.contains_total) row.append(el("span", "muted small", ` holds ${u.contains_total.toLocaleString()}`));
  li.append(row);
  if (u.contains && u.contains.length) {
    const ul = el("ul");
    for (const c of u.contains) ul.append(node(c));
    const more = (u.contains_total || u.contains.length) - u.contains.length;
    if (more > 0) ul.append(el("li", "muted small", `… and ${more.toLocaleString()} more (open ${u.serial} to see them)`));
    li.append(ul);
  }
  return li;
}

function summaryText(unit) {
  const inside = unit.units_inside || 0;
  if (!inside) return "";
  const by = Object.entries(unit.by_material || {}).map(([m, n]) => `${n.toLocaleString()} ${m}`).join(", ");
  return `— ${inside.toLocaleString()} unit(s) inside${by ? `: ${by}` : ""}${unit.partial ? " (partial: the tree is larger than one read)" : ""}`;
}

async function showSerial(serial) {
  const [unit, trace] = await Promise.all([
    api(`/trace/units/${encodeURIComponent(serial)}`),
    api(`/trace/units/${encodeURIComponent(serial)}/trace`),
  ]);
  remember("serial", unit.serial);
  $("#serial-panels").hidden = false;
  $("#lot-panels").hidden = true;
  $("#s-serial").textContent = unit.serial;
  $("#s-material").textContent = unit.material;
  $("#s-status").textContent = unit.status;
  $("#s-order").textContent = unit.order || "—";
  const eq = $("#s-equipment"); eq.replaceChildren(); eq.append(unit.equipment ? FS.link("machine", unit.equipment) : "—");
  const parent = $("#s-parent"); parent.replaceChildren(); parent.append(unit.packed_into ? FS.link("serial", unit.packed_into) : "—");
  $("#s-produced").textContent = fmt.stamp(unit.produced_at);
  const cert = $("#s-certificate");
  if (cert) {
    cert.replaceChildren();
    if (/^PL\d/i.test(unit.serial)) {
      const a = el("a", "obj", "Certificate of analysis");
      a.href = `/dashboard/coa?order=${encodeURIComponent(unit.serial)}`;
      cert.append(a);
    }
  }
  $("#s-note").textContent = unit.note || "";
  const tree = $("#s-tree");
  tree.replaceChildren();
  if (!unit.contains.length) tree.append(el("li", "muted", "Nothing packed inside this unit."));
  for (const c of unit.contains) tree.append(node(c));
  const more = (unit.contains_total || 0) - unit.contains.length;
  if (more > 0) tree.append(el("li", "muted small", `… and ${more.toLocaleString()} more directly inside; the counts above cover them all.`));
  $("#s-count").textContent = summaryText(unit);
  const body = $("#s-components tbody");
  body.replaceChildren();
  if (!trace.components.length) {
    const tr = el("tr"); const td = el("td", "muted", "No lot consumption is recorded against this unit or anything inside it."); td.colSpan = 4; tr.append(td); body.append(tr);
  }
  for (const c of trace.components) {
    const tr = el("tr");
    const lot = el("td"); lot.append(FS.link("lot", c.lot)); tr.append(lot);
    tr.append(el("td", "code", c.material || ""),
              el("td", "num", c.units === undefined ? "—" : c.units.toLocaleString()));
    // Where the claim comes from: the unit's own component record, or
    // what the order that made it consumed - the batch path never writes
    // per-piece components, and the screen says so rather than implying it.
    const basis = el("td", "muted small", c.basis === "order" ? "via the order's consumption"
      : c.basis === "unit" ? "recorded on the unit" : (c.basis || ""));
    if (c.from_order) basis.append(` · lot from ${c.from_order}`);
    tr.append(basis);
    body.append(tr);
  }
  $("#s-components-count").textContent = `— ${trace.components.length} lot(s)`;
  window.__fsmesPageData = { unit, trace };
}

/* ---------- a lot ---------- */

/* The orders that drew on the lot: where it went in, how much, when, and
   what each order made. A station the issue did not record is said to be
   unrecorded, not left blank and not guessed. */
function amount(q, unit) {
  return `${Number(q).toLocaleString(undefined, { maximumFractionDigits: 3 })}${unit ? " " + unit : ""}`;
}

function orderLink(code) {
  const a = el("a", "obj", code);
  a.href = `/dashboard/orders?q=${encodeURIComponent(code)}`;
  return a;
}

function showOrders(w) {
  const orders = w.orders || [];
  $("#l-orders-count").textContent = `— ${orders.length} order(s)`;
  const body = $("#l-orders tbody");
  body.replaceChildren();
  if (!orders.length) {
    const tr = el("tr"); const td = el("td", "muted", "No order has drawn on this lot."); td.colSpan = 6;
    tr.append(td); body.append(tr);
  }
  for (const o of orders) {
    const tr = el("tr");
    const code = el("td"); code.append(orderLink(o.order)); tr.append(code);
    tr.append(el("td", null, o.status || ""));
    const at = el("td");
    o.stations.forEach((s, i) => {
      if (i) at.append(", ");
      if (s.station) at.append(FS.link("machine", s.station)); else at.append(el("span", "muted", "station not recorded"));
      if (s.operation) at.append(el("span", "muted small", ` ${s.operation}`));
    });
    tr.append(at);
    tr.append(el("td", "num", amount(o.quantity, w.unit)));
    tr.append(el("td", "small", o.first_drawn === o.last_drawn ? fmt.stamp(o.first_drawn)
      : `${fmt.stamp(o.first_drawn)} – ${fmt.stamp(o.last_drawn)}`));
    const made = el("td");
    if (!o.lots_made.length) made.append(el("span", "muted", o.status === "completed" || o.status === "closed"
      ? "none recorded" : "none yet"));
    o.lots_made.forEach((m, i) => {
      if (i) made.append(", ");
      made.append(FS.link("lot", m.lot), el("span", "muted small", ` ${m.material || ""} · ${amount(m.quantity)}`));
    });
    tr.append(made);
    body.append(tr);
  }
}

async function showLot(lot) {
  const w = await api(`/trace/where-used/${encodeURIComponent(lot)}`);
  remember("lot", w.lot);
  $("#lot-panels").hidden = false;
  $("#serial-panels").hidden = true;
  $("#l-lot").textContent = w.lot;
  $("#l-verdict").textContent = `— ${w.verdict}`;
  $("#l-material").textContent = w.material;
  $("#l-units").textContent = w.units_affected;
  $("#l-packages").textContent = w.packages_to_hold.length;
  $("#l-orders-total").textContent = w.orders_total;
  showOrders(w);
  $("#l-packages-count").textContent = `— ${w.packages_to_hold.length} package(s)`;
  const body = $("#l-table tbody");
  body.replaceChildren();
  if (!w.packages_to_hold.length) {
    const tr = el("tr"); const td = el("td", "muted", w.units_affected
      ? "Every unit carrying it is loose - none is packed."
      : "No serialised unit carries this lot. A line that books by the lot answers in the orders above.");
    td.colSpan = 4; tr.append(td); body.append(tr);
  }
  for (const p of w.packages_to_hold) {
    const tr = el("tr");
    const pk = el("td"); pk.append(FS.link("serial", p.package)); tr.append(pk);
    tr.append(el("td", "code", p.material || ""), el("td", null, p.status || ""),
              el("td", "num", String(p.units !== undefined ? p.units : (p.units_inside !== undefined ? p.units_inside : "—"))));
    body.append(tr);
  }
  window.__fsmesPageData = w;
}

async function lookup(code) {
  code = (code || "").trim().toUpperCase();
  if (!code) return;
  $("#banner").classList.add("hidden");
  try {
    try { await showSerial(code); }
    catch (err) {
      if (!/404/.test(err.message) && !/not found|no unit/i.test(err.message)) throw err;
      await showLot(code);
    }
    FS.live(true);
  } catch (err) { fail(err); }
}

/* ---------- actions ---------- */

function wire() {
  $("#find").addEventListener("submit", (e) => { e.preventDefault(); lookup($("#q").value); });
  $("#st-submit").addEventListener("click", async () => {
    if (current.kind !== "serial") return;
    try {
      const out = await api(`/trace/units/${encodeURIComponent(current.code)}/status`, { method: "POST", body: {
        status: $("#st-status").value, note: $("#st-note").value.trim() || null, cascade: $("#st-cascade").checked } });
      toast(`${out.serial}: ${out.status} (${out.units_changed} unit(s))`);
      await showSerial(current.code);
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#pack-submit").addEventListener("click", async () => {
    const into = $("#pack-into").value.trim().toUpperCase();
    if (current.kind !== "serial" || !into) return;
    try {
      await api("/trace/units/pack", { method: "POST", body: { serial: current.code, into } });
      toast(`${current.code} packed into ${into}`);
      $("#pack-into").value = "";
      await showSerial(into);
    } catch (err) { toast(err.message, "bad"); }
  });
  $("#produce").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const out = await api("/trace/units", { method: "POST", body: {
        material: $("#p-material").value.trim().toUpperCase(), order: $("#p-order").value.trim() || null,
        equipment: $("#p-equipment").value.trim() || null, count: Number($("#p-count").value || 1) } });
      $("#p-result").replaceChildren(`Produced ${out.count}: `);
      out.produced.forEach((s, i) => { if (i) $("#p-result").append(", "); $("#p-result").append(FS.link("serial", s)); });
      toast(`${out.count} unit(s) produced`);
    } catch (err) { toast(err.message, "bad"); }
  });
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  wire();
  const url = new URL(location);
  const serial = url.searchParams.get("serial"), lot = url.searchParams.get("lot");
  if (serial) { $("#q").value = serial; await showSerial(serial).then(() => FS.live(true)).catch(fail); }
  else if (lot) { $("#q").value = lot; await showLot(lot).then(() => FS.live(true)).catch(fail); }
  else FS.live(true);
})().catch(fail);
