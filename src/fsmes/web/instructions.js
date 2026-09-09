/* Work instructions.

   Reading is the common case, so the reader is the bigger half. Approving is
   deliberately a separate act with its own capability - drafting a procedure
   and putting it in force are different jobs, and in a regulated shop they are
   different people. */

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const stamp = (ts) => (ts ? new Date(ts + (ts.endsWith("Z") ? "" : "Z")).toLocaleString() : "—");

let me = null;
let catalogue = [];
let selected = null;

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

function toast(message, kind = "good") {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast ${kind}`;
  clearTimeout(node._t);
  node._t = setTimeout(() => node.classList.add("hidden"), 4000);
}

function live(ok) {
  $("#live-dot").className = "dot" + (ok ? "" : " bad");
  $("#live-text").textContent = ok ? "live" : "reconnecting…";
}

/* A deliberately small Markdown subset: paragraphs, numbered and bulleted
   lists, bold, and the Purpose line. Everything the house style produces and
   nothing else - a parser that handles more is a parser with more to go
   wrong, and no library may be loaded here. */
function renderMarkdown(text, into) {
  into.textContent = "";
  const lines = (text || "").split("\n");
  let list = null;

  const inline = (raw, node) => {
    const parts = raw.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
    for (const part of parts) {
      if (part.startsWith("**") && part.endsWith("**")) {
        node.appendChild(el("strong", null, part.slice(2, -2)));
      } else if (part.startsWith("`") && part.endsWith("`")) {
        node.appendChild(el("code", null, part.slice(1, -1)));
      } else if (part) {
        node.appendChild(document.createTextNode(part));
      }
    }
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { list = null; continue; }

    const ordered = /^\d+[.)]\s+(.*)$/.exec(line);
    const bullet = /^[-*]\s+(.*)$/.exec(line);
    const heading = /^#{1,6}\s+(.*)$/.exec(line);

    if (ordered || bullet) {
      const want = ordered ? "OL" : "UL";
      if (!list || list.tagName !== want) {
        list = el(want === "OL" ? "ol" : "ul");
        into.appendChild(list);
      }
      const li = el("li");
      inline((ordered || bullet)[1], li);
      list.appendChild(li);
      continue;
    }
    list = null;

    if (heading) { into.appendChild(el("h4", null, heading[1])); continue; }
    if (/^purpose\s*:/i.test(line)) {
      const p = el("p", "purpose");
      inline(line, p);
      into.appendChild(p);
      continue;
    }
    const p = el("p");
    inline(line, p);
    into.appendChild(p);
  }
}

const DOC_PAGE = 25;
let docOffset = 0;

function renderCatalogue() {
  const body = $("#catalogue tbody");
  body.textContent = "";
  $("#empty").classList.toggle("hidden", catalogue.length > 0);

  // A document per specification and routing: the plant's catalogue is
  // hundreds of rows, filtered and paged on the screen.
  const q = ($("#doc-q").value || "").trim().toLowerCase();
  const state = $("#doc-state").value;
  const matching = catalogue.filter((d) =>
    (!state || (state === "in_force" ? !!d.approved_revision : !d.approved_revision))
    && (!q || `${d.code} ${d.title}`.toLowerCase().includes(q)));
  const page = window.FS.clientPage(matching, docOffset, DOC_PAGE);
  docOffset = page.offset;
  if (catalogue.length && !page.items.length) {
    const row = el("tr"); const cell = el("td", "muted", "No document matches."); cell.colSpan = 4; row.appendChild(cell); body.appendChild(row);
  }
  $("#catalogue-count").textContent = catalogue.length ? window.FS.countText(page, catalogue.length) : "";
  window.FS.pager($("#doc-pager"), page, (offset) => { docOffset = offset; renderCatalogue(); });

  for (const entry of page.items) {
    const row = el("tr");
    if (entry.code === selected) row.classList.add("selected");
    row.appendChild(el("td", "mono", entry.code));
    row.appendChild(el("td", null, entry.title));
    row.appendChild(el("td", entry.approved_revision ? "" : "muted",
      entry.approved_revision ? `rev ${entry.approved_revision}` : "none"));

    const state = el("td");
    if (entry.kind === "walkthrough") state.appendChild(el("span", "pill setup", "walkthrough"));
    if (entry.approved_revision) state.appendChild(el("span", "pill approved", "in force"));
    if (entry.draft_revision) {
      state.appendChild(el("span", "pill draft",
        entry.approved_revision ? `rev ${entry.draft_revision} draft` : "draft"));
    }
    row.appendChild(state);

    row.addEventListener("click", () => { selected = entry.code; renderCatalogue(); openDoc(); });
    body.appendChild(row);
  }
}

/* A recorded walkthrough: the steps as text, and the button that plays it on
   the real screens. A draft plays from its own steps; a walk in force plays
   by id so a page change resumes it. */
function renderWalkthrough(doc, box) {
  box.textContent = "";
  const play = el("button", null, "Show me — walk me through it");
  play.className = "assist-launch";
  play.style.position = "static";
  play.style.marginBottom = "12px";
  play.addEventListener("click", () => {
    if (!window.fsmesAssist) return;
    const guide = { title: doc.title, steps: doc.steps || [], recorded_by: doc.created_by,
                    revision: doc.revision, approved_by: doc.approved_by };
    if (doc.status === "approved") guide.id = `doc:${doc.code}`;
    window.fsmesAssist.beginWalk(guide);
  });
  box.appendChild(play);
  if (doc.body) box.appendChild(el("p", "muted", `Ask for it as: ${doc.body}`));
  const ol = el("ol", "walk-steps");
  for (const s of doc.steps || []) {
    const li = el("li");
    li.appendChild(el("strong", null, s.title));
    if (s.body) li.appendChild(el("p", null, s.body));
    const where = `${s.page} · ${s.anchor}` + (s.fill ? ` · prefills "${s.fill.value}"` : "");
    li.appendChild(el("code", null, where));
    ol.appendChild(li);
  }
  box.appendChild(ol);
  box.appendChild(el("p", "muted", `Recorded by ${doc.created_by}. Needs ${doc.needs || "plant.read"} to follow.`));
}

async function openDoc() {
  if (!selected) return;
  const [doc, history] = await Promise.all([
    api(`/documents/${selected}`),
    api(`/documents/${selected}/revisions`),
  ]);
  if (doc.error) { toast(doc.error, "bad"); return; }

  $("#doc-empty").classList.add("hidden");
  $("#doc").classList.remove("hidden");
  $("#doc-title").textContent = doc.title;
  $("#doc-status").textContent = doc.status;
  $("#doc-status").className = `pill ${doc.status}`;
  $("#doc-rev").textContent = `revision ${doc.revision}`;
  $("#doc-approval").textContent = doc.approved_by
    ? `approved by ${doc.approved_by} — ${stamp(doc.approved_at)}`
    : "not yet approved";

  const anchors = $("#doc-anchors");
  anchors.textContent = "";
  for (const [key, value] of Object.entries(doc.anchors || {})) {
    const chip = el("span", "anchor");
    chip.appendChild(document.createTextNode(`${key}: `));
    chip.appendChild(el("b", null, value));
    anchors.appendChild(chip);
  }
  if (!Object.keys(doc.anchors || {}).length) {
    anchors.appendChild(el("span", "anchor", "general — not tied to a material"));
  }

  const model = $("#doc-model");
  model.classList.toggle("hidden", !doc.drafted_by_model);
  if (doc.drafted_by_model) {
    model.textContent =
      `First drafted by ${doc.drafted_by_model} from this plant's own specifications. `
      + (doc.status === "draft"
        ? "It is not in force until somebody reads it and approves it."
        : "It was reviewed and approved before going into force.");
  }

  if (doc.kind === "walkthrough") renderWalkthrough(doc, $("#doc-body"));
  else renderMarkdown(doc.body, $("#doc-body"));

  const actions = $("#doc-actions");
  actions.textContent = "";
  const can = (c) => (me.capabilities || []).includes(c);

  if (doc.status === "draft" && can("documents.approve")) {
    const approve = el("button", null, `Approve revision ${doc.revision}`);
    approve.addEventListener("click", async () => {
      try {
        await api(`/documents/${selected}/approve/${doc.revision}`, { method: "POST" });
        toast(`${selected} rev ${doc.revision} is now in force`);
        await refresh();
        await openDoc();
      } catch (err) { toast(err.message, "bad"); }
    });
    actions.appendChild(approve);
  }
  if (doc.status !== "withdrawn" && can("documents.approve")) {
    const withdraw = el("button", "ghost", "Withdraw");
    withdraw.addEventListener("click", async () => {
      if (!window.confirm(`Withdraw ${selected}? The floor stops following it.`)) return;
      try {
        await api(`/documents/${selected}/withdraw`, { method: "POST" });
        toast(`${selected} withdrawn`);
        await refresh();
        await openDoc();
      } catch (err) { toast(err.message, "bad"); }
    });
    actions.appendChild(withdraw);
  }
  if (doc.status === "approved" && can("documents.write")) {
    const revise = el("button", "ghost", "Open next revision");
    revise.addEventListener("click", async () => {
      try {
        const next = await api(`/documents/${selected}/revise`, { method: "POST", body: {} });
        toast(`revision ${next.revision} opened as a draft`);
        await refresh();
        await openDoc();
      } catch (err) { toast(err.message, "bad"); }
    });
    actions.appendChild(revise);
  }

  const body = $("#history tbody");
  body.textContent = "";
  for (const rev of history) {
    const row = el("tr");
    row.appendChild(el("td", "num", rev.revision));
    const state = el("td");
    state.appendChild(el("span", `pill ${rev.status}`, rev.status));
    row.appendChild(state);
    row.appendChild(el("td", "muted", rev.drafted_by_model || rev.created_by));
    row.appendChild(el("td", "muted", rev.approved_by || "—"));
    row.appendChild(el("td", "muted", stamp(rev.approved_at || rev.created_at)));
    body.appendChild(row);
  }
}

async function refresh() {
  try {
    catalogue = await api("/documents");
    if (!selected && catalogue.length) selected = catalogue[0].code;
    renderCatalogue();
    live(true);
  } catch (err) { live(false); }
}

(async function boot() {
  me = await api("/auth/me");
  for (const id of ["doc-q", "doc-state"]) $(`#${id}`).addEventListener("input", () => { docOffset = 0; renderCatalogue(); });
  await refresh();
  if (selected) await openDoc();
  setInterval(refresh, 10000);
})();

/* ---------- a new document, drafted by a person ---------- */
(function wireNewDocument() {
  const form = document.getElementById("form-doc");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const made = await api("/documents", { method: "POST", body: {
        code: form.code.value.trim().toUpperCase(), title: form.title.value.trim(), body: form.body.value } });
      toast(`${made.code} created as a draft`);
      form.reset();
      await refresh();
    } catch (err) { toast(err.message, "bad"); }
  });
})();
