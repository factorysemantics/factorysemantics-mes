/* Quality › Non-conformance severities: the plant's own words for how bad a
   finding is.

   The second vocabulary in this product, and deliberately the same screen as
   the first — Engineering › Downtime reasons — because a second list that
   read differently would be a second thing to learn rather than the same
   thing again. Draft, edit an open draft, draft a retirement; signing stays
   on the Floor screen's Waiting-for-you panel, in front of whoever holds
   `quality.approve`.

   The one thing this screen says that the reasons screen does not: two of
   these words are written by the product itself, so they can be renamed and
   described and never retired. The server says which, and the row says so
   where the Retire button would otherwise be — a button that is absent with
   its reason beside it beats a button that refuses. */

const { $, el, api, fmt, toast } = window.FS;

const REFRESH_MS = 15000;
const PAGE = 25;

let rows = [];
let written = [];
let offset = 0;

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

/* What the plant is living with right now: the approved or retired revision,
   or nothing at all where the word has never been signed. A word can be in
   force AND have a change waiting, and saying only the second of those would
   tell an engineer a word grades nothing while records are being raised at
   it. */
function inForce(row) {
  return row.in_force ? row.in_force.status : "none";  // approved | retired | none
}

function pillsFor(row) {
  if (!row.in_force) {
    return row.draft
      ? [["planned", "drafted, not signed"]]
      : [["down", "never signed"]];
  }
  const where = row.in_force.status === "retired"
    ? ["down", "retired"] : ["running", "on the list"];
  if (!row.draft) return [where];
  return [where, ["planned", row.draft.retires ? "retirement waiting" : "change waiting"]];
}

function showing(row) {
  return row.in_force || row.draft || null;
}

function matches(row, q, status) {
  if (status && (status === "draft" ? !row.draft : inForce(row) !== status)) return false;
  if (!q) return true;
  const parts = [row.code];
  for (const rev of [row.in_force, row.draft]) {
    if (rev) parts.push(rev.name || "", rev.description || "");
  }
  return parts.join(" ").toLowerCase().includes(q);
}

async function load() {
  const data = await api("/quality/severities/vocabulary");
  rows = data.severities;
  written = data.product_writes || [];
  draw();
  window.__fsmesPageData = { vocabulary: rows, total: data.total };
}

function draw() {
  const q = $("#v-q").value.trim().toLowerCase();
  const status = $("#v-status").value;
  const matching = rows.filter((row) => matches(row, q, status));
  const page = FS.clientPage(matching, offset, PAGE);
  offset = page.offset;

  const body = $("#severities-table tbody");
  body.replaceChildren();
  for (const row of page.items) body.append(drawRow(row));

  const empty = $("#v-empty");
  empty.classList.toggle("hidden", page.items.length > 0);
  if (!page.items.length) {
    empty.textContent = rows.length
      ? "No word in this plant's severities matches."
      : "This plant has no severity vocabulary yet. Until it has one, a "
        + "non-conformance is graded with whatever the code that raised it "
        + "wrote, and nothing checks the word. Draft the first one above.";
  }

  $("#severity-count").textContent = FS.countText(page, rows.length, "in the vocabulary");
  FS.pager($("#v-pager"), page, (next) => { offset = next; draw(); });

  // The tiles count the plant's vocabulary, never the page's (STYLE.md 5).
  const by = (state) => rows.filter((row) => inForce(row) === state).length;
  $("#kpi-approved").textContent = by("approved").toLocaleString();
  $("#kpi-drafts").textContent = rows.filter((row) => row.draft).length.toLocaleString();
  $("#kpi-retired").textContent = by("retired").toLocaleString();
  $("#kpi-codes").textContent = rows.length.toLocaleString();

  // Which words the product writes itself, said once above the list rather
  // than repeated on every row that has one.
  $("#product-writes").textContent = written.length
    ? `This product opens a non-conformance itself when a check falls out of `
      + `specification and when an SPC rule fires, and it writes `
      + `${written.map((code) => `“${code}”`).join(" and ")}. Rename `
      + `${written.length === 1 ? "it" : "those"} and describe `
      + `${written.length === 1 ? "it" : "them"} in your own words; `
      + `${written.length === 1 ? "it cannot" : "they cannot"} be retired, `
      + `because the refusal would land on a machine raising a hold instead of here.`
    : "";

  $("#severity-codes").replaceChildren(...rows.map((row) => {
    const option = document.createElement("option");
    option.value = row.code;
    option.label = (showing(row) || {}).name || "";
    return option;
  }));
}

function drawRow(row) {
  const shown = showing(row) || {};
  const tr = el("tr");

  const what = el("td");
  what.append(el("span", "code", row.code));
  if (shown.name) what.append(el("span", "small", ` ${shown.name}`));
  if (row.draft && !row.draft.retires && row.in_force
      && row.draft.name !== row.in_force.name) {
    what.append(el("div", "muted small", `waiting: ${row.draft.name}`));
  }
  tr.append(what);

  tr.append(el("td", "small", shown.description || ""));

  const status = el("td");
  for (const [cls, word] of pillsFor(row)) {
    status.append(el("span", `pill ${cls}`, word), " ");
  }
  tr.append(status);

  const revision = el("td", "num", shown.revision === undefined ? "—" : String(shown.revision));
  if (row.draft && row.in_force) {
    revision.textContent = `${row.in_force.revision} → ${row.draft.revision}`;
    revision.title = `revision ${row.in_force.revision} is in force; `
      + `revision ${row.draft.revision} is drafted and waiting to be signed`;
  }
  tr.append(revision);

  const drafted = row.draft || shown;
  tr.append(el("td", "muted small", drafted.created_by
    ? `${drafted.created_by}${drafted.on_behalf_of ? ` for ${drafted.on_behalf_of}` : ""} · ${fmt.stamp(drafted.created_at)}`
    : "—"));
  // Unknown renders as a dash, never as a blank that reads like nobody
  // (STYLE.md 8).
  tr.append(el("td", "muted small", (row.in_force && row.in_force.approved_by)
    ? `${row.in_force.approved_by} · ${fmt.stamp(row.in_force.approved_at)}`
    : "—"));

  const labels = el("td", "num", row.labels_records.toLocaleString());
  labels.title = `${row.labels_records.toLocaleString()} non-conformances carry ${row.code}`;
  tr.append(labels);

  tr.append(actionsFor(row));
  return tr;
}

function actionsFor(row) {
  const cell = el("td");
  if (!FS.can("quality.define")) return cell;

  const button = el("button", "small", row.draft ? "Edit draft" : "New revision");
  button.type = "button";
  button.addEventListener("click", () => editDraft(row));
  cell.append(button);

  // Only a word that is actually on the list can be taken off it, and a word
  // the product writes itself never can. The server says so too; this is why
  // the button is absent rather than refused, and the title says which of the
  // two reasons applies.
  if (inForce(row) === "approved" && !(row.draft && row.draft.retires)) {
    if (row.product_writes) {
      const why = el("span", "muted small", " — kept: the product writes it");
      why.title = row.product_writes;
      cell.append(why);
    } else {
      const retire = el("button", "ghost small", "Retire");
      retire.type = "button";
      retire.addEventListener("click", () => retireIt(row));
      cell.append(" ", retire);
    }
  }
  return cell;
}

/* ---------- drafting ---------- */

function editDraft(row) {
  // The draft where there is one: that is the text being worked on. A form
  // that loaded the approved wording over the top of an unsigned edit would
  // quietly undo it on save.
  const shown = row.draft || row.in_force || {};
  $("#s-code").value = row.code;
  $("#s-name").value = shown.name || "";
  $("#s-description").value = shown.description || "";
  $("#form-title").textContent = row.draft
    ? `Editing the open draft of ${row.code}`
    : `A new revision of ${row.code}`;
  $("#s-cancel").classList.remove("hidden");
  $("#s-name").focus();
}

function resetForm() {
  $("#severity-form").reset();
  $("#form-title").textContent = "A new word";
  $("#s-cancel").classList.add("hidden");
}

async function submit(event) {
  event.preventDefault();
  try {
    // Nothing here re-states a rule the server owns: the code's spelling, its
    // uniqueness and the words the product has already taken are checked in
    // one place, and what a person reads when one of them refuses is the
    // server's own sentence.
    const out = await api("/quality/severities", { method: "POST", body: {
      code: $("#s-code").value.trim(),
      name: $("#s-name").value.trim(),
      description: $("#s-description").value.trim(),
    } });
    toast(`${out.code} rev ${out.revision} drafted — it grades records when `
      + "somebody signs it on the Floor screen");
    resetForm();
    await load();
  } catch (err) { toast(err.message, "bad"); }
}

async function retireIt(row) {
  // The number comes before the decision, not after a refusal.
  const carried = row.labels_records;
  const name = (row.in_force || {}).name || row.code;
  const ask = carried
    ? `Retire ${row.code}? ${carried.toLocaleString()} `
      + `${carried === 1 ? "non-conformance carries" : "non-conformances carry"} it. `
      + "They keep the grade — retiring changes what may be graded next, never "
      + "what was graded before. The retirement is a draft until somebody signs it."
    : `Retire ${row.code}? No non-conformance carries it. The retirement is a `
      + "draft until somebody signs it.";
  if (!window.confirm(ask)) return;

  try {
    await api("/quality/severities", { method: "POST", body: {
      code: row.code, name, description: (row.in_force || {}).description || "",
      retires: true, labels_records: carried,
    } });
    toast(`${row.code}: retirement drafted — it leaves the list when somebody signs it`);
    await load();
  } catch (err) { toast(err.message, "bad"); }
}

/* ---------- boot ---------- */

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  $("#severity-form").addEventListener("submit", submit);
  $("#s-cancel").addEventListener("click", resetForm);
  for (const id of ["v-q", "v-status"]) {
    $(`#${id}`).addEventListener("input", () => { offset = 0; draw(); });
  }
  const run = () => load().then(() => live(true)).catch(fail);
  await run();
  setInterval(run, REFRESH_MS);
})().catch(fail);
