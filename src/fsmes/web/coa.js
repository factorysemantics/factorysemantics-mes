/* Quality › Certificates: the certificate of analysis, readable and printable.

   The document is Markdown with tables, rendered here without a library
   (principle 5) and printed from the browser (the print stylesheet hides
   everything but the certificate). Reissuing supersedes; nothing is edited. */

const { $, el, api, fmt, toast } = window.FS;

let currentOrder = null;

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

/* A small Markdown renderer: headings, paragraphs, pipe tables, bold, rules. */
function renderMarkdown(text, into) {
  into.textContent = "";
  const lines = (text || "").split("\n");
  let table = null;
  const inline = (raw, node) => {
    for (const part of raw.split(/(\*\*[^*]+\*\*)/g)) {
      if (part.startsWith("**") && part.endsWith("**")) node.appendChild(el("strong", null, part.slice(2, -2)));
      else if (part) node.appendChild(document.createTextNode(part));
    }
  };
  const cells = (line) => line.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith("|")) {
      if (/^\|[\s:|-]+\|$/.test(line)) continue;   // the alignment row
      if (!table) { table = el("table", "cert-table"); into.appendChild(table); table._head = true; }
      const tr = el("tr");
      for (const c of cells(line)) { const td = el(table._head ? "th" : "td"); inline(c, td); tr.appendChild(td); }
      (table._head ? table.appendChild(el("thead")) : (table.tBodies[0] || table.appendChild(el("tbody")))).appendChild(tr);
      table._head = false;
      continue;
    }
    table = null;
    if (!line) continue;
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) { const h = el(`h${Math.min(heading[1].length + 1, 4)}`); inline(heading[2], h); into.appendChild(h); continue; }
    if (line === "---") { into.appendChild(el("hr")); continue; }
    const p = el("p"); inline(line, p); into.appendChild(p);
  }
}

const isPallet = (code) => /^PL\d/i.test(code);
function certPath(code) { return isPallet(code) ? `/coa/pallet/${encodeURIComponent(code)}` : `/coa/${encodeURIComponent(code)}`; }

async function open(order) {
  order = (order || "").trim().toUpperCase();
  if (!order) return;
  $("#banner").classList.add("hidden");
  const cert = await api(certPath(order));
  currentOrder = order;
  const url = new URL(location); url.searchParams.set("order", order); history.replaceState(null, "", url);
  document.title = `${cert.document} — FactorySemantics MES`;
  $("#cert-panel").hidden = false;
  $("#cert-code").textContent = `${cert.document} · revision ${cert.revision}`;
  $("#cert-revisions").textContent = cert.revisions.length > 1
    ? `revisions ${cert.revisions.join(", ")} — earlier ones are superseded, not deleted` : "";
  renderMarkdown(cert.body, $("#cert-body"));
  window.__fsmesPageData = cert;
}

const LIST_PAGE = 50;
let listOffset = 0;

async function loadList() {
  // One certificate per completed order: the server pages the plant's history.
  const params = new URLSearchParams({ limit: String(LIST_PAGE), offset: String(listOffset) });
  if ($("#list-q").value.trim()) params.set("q", $("#list-q").value.trim().toUpperCase());
  if ($("#list-material").value) params.set("material", $("#list-material").value);
  const page = await api(`/coa?${params}`);
  listOffset = page.offset;
  const rows = page.items;
  const body = $("#list-table tbody");
  body.replaceChildren();
  if (!rows.length) { const tr = el("tr"); const td = el("td", "muted", page.total ? "No certificate matches." : "No certificate has been issued yet. One is issued when an order completes."); td.colSpan = 5; tr.append(td); body.append(tr); }
  for (const r of rows) {
    const tr = el("tr", r.order === currentOrder ? "selected" : null);
    tr.append(el("td", "code", r.order), el("td", "code", r.material || ""), el("td", "num", String(r.revision)),
              el("td", "muted small", fmt.stamp(r.issued_at)), el("td", "muted small", r.issued_by || ""));
    tr.addEventListener("click", () => open(r.order).then(loadList).catch(fail));
    body.append(tr);
  }
  $("#list-count").textContent = `— ${rows.length} of ${page.total.toLocaleString()}`;
  FS.pager($("#list-pager"), page, (offset) => { listOffset = offset; loadList().catch(fail); });
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  $("#find").addEventListener("submit", (e) => { e.preventDefault(); open($("#q").value).then(loadList).catch(fail); });
  $("#print").addEventListener("click", () => window.print());
  $("#reissue").addEventListener("click", async () => {
    if (!currentOrder) return;
    try { const out = await api(certPath(currentOrder), { method: "POST" }); toast(`${out.document} revision ${out.issued_revision} issued`); await open(currentOrder); await loadList(); }
    catch (err) { toast(err.message, "bad"); }
  });
  const materials = await api("/masterdata/materials?type=finished").catch(() => []);
  $("#list-material").replaceChildren(new Option("Any material", ""), ...materials.map((m) => new Option(m.code, m.code)));
  let typing = null;
  $("#list-q").addEventListener("input", () => { clearTimeout(typing); typing = setTimeout(() => { listOffset = 0; loadList().catch(fail); }, 250); });
  $("#list-material").addEventListener("change", () => { listOffset = 0; loadList().catch(fail); });
  const wanted = new URL(location).searchParams.get("order");
  if (wanted) { $("#q").value = wanted; await open(wanted).catch(fail); }
  await loadList().then(() => live(true)).catch(fail);
})().catch(fail);
