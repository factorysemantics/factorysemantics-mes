/* Engineering › Downtime reasons: the plant's own words for why a machine
   stopped.

   PR #85 built this vocabulary end to end for *signing* — the catalogue, the
   station select, the pareto, and the Waiting-for-you panel that puts a draft
   in force. It built nowhere to *draft* one, so the only person who could
   exercise the drafting half was somebody with a terminal and a token. This
   screen is that missing half, and nothing else: it drafts, it edits an open
   draft, and it drafts a retirement. Signing stays where the design put it,
   on the Floor screen's panel, in front of whoever holds `process.approve`.

   Modelled on Engineering › Triggers, which is the same shape of screen — a
   draft-then-approve lifecycle with a form above the list it feeds — so the
   filter bar, the client-side page, the status pills and the count all read
   the same on both. */

const { $, el, api, fmt, toast } = window.FS;

const REFRESH_MS = 15000;
const PAGE = 25;

let rows = [];
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
   tell an engineer a word is off the floor while operators are choosing it. */
function inForce(row) {
  return row.in_force ? row.in_force.status : "none";  // approved | retired | none
}

/* The status cell, as one or two pills: where the word stands, and then what
   is waiting on somebody. A retirement waiting is not the same act as a
   rename waiting, and the difference decides whether the word survives. */
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

/* The revision a reader is looking at: what is in force, because that is what
   the operators have; a word with nothing in force shows its draft, which is
   all there is of it. */
function showing(row) {
  return row.in_force || row.draft || null;
}

/* A search reads everything the plant has written about a word, the draft
   included - looking for a reason you half-remember drafting and not finding
   it because it is not signed yet is the search failing. */
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
  const data = await api("/equipment/downtime-reasons/vocabulary");
  rows = data.reasons;
  draw();
  window.__fsmesPageData = { vocabulary: rows, total: data.total };
}

function draw() {
  const q = $("#v-q").value.trim().toLowerCase();
  const status = $("#v-status").value;
  const matching = rows.filter((row) => matches(row, q, status));
  const page = FS.clientPage(matching, offset, PAGE);
  offset = page.offset;

  const body = $("#reasons-table tbody");
  body.replaceChildren();
  for (const row of page.items) body.append(drawRow(row));

  const empty = $("#v-empty");
  empty.classList.toggle("hidden", page.items.length > 0);
  if (!page.items.length) {
    empty.textContent = rows.length
      ? "No word in this plant's vocabulary matches."
      : "This plant has no downtime vocabulary yet. Until it has one the station "
        + "screen keeps its text box, and the pareto groups on whatever was typed. "
        + "Draft the first word above.";
  }

  // The count says what it covers: the filter's total, and the plant's.
  $("#reason-count").textContent = FS.countText(page, rows.length, "in the vocabulary");
  FS.pager($("#v-pager"), page, (next) => { offset = next; draw(); });

  // The tiles count the plant's vocabulary, never the page's (STYLE.md 5).
  const by = (state) => rows.filter((row) => inForce(row) === state).length;
  $("#kpi-approved").textContent = by("approved").toLocaleString();
  $("#kpi-drafts").textContent = rows.filter((row) => row.draft).length.toLocaleString();
  $("#kpi-retired").textContent = by("retired").toLocaleString();
  $("#kpi-codes").textContent = rows.length.toLocaleString();

  // The form's code box offers what is already there, so drafting a second
  // revision of a word is typing its name rather than remembering its code.
  $("#reason-codes").replaceChildren(...rows.map((row) => {
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
  // What a waiting draft would rename it to, where that is the change: the
  // pill says a change is waiting and this says what the change is.
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

  // The revision in force, and the one waiting behind it where there is one.
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
  // (STYLE.md 8): a draft has not been signed, which is not the same as
  // having been signed by no one.
  tr.append(el("td", "muted small", (row.in_force && row.in_force.approved_by)
    ? `${row.in_force.approved_by} · ${fmt.stamp(row.in_force.approved_at)}`
    : "—"));

  const labels = el("td", "num", row.labels_intervals.toLocaleString());
  labels.title = `${row.labels_intervals.toLocaleString()} recorded intervals carry ${row.code}`;
  tr.append(labels);

  tr.append(actionsFor(row));
  return tr;
}

function actionsFor(row) {
  const cell = el("td");
  if (!FS.can("process.define")) return cell;

  if (row.draft) {
    const edit = el("button", "small", "Edit draft");
    edit.type = "button";
    edit.addEventListener("click", () => editDraft(row));
    cell.append(edit);
  } else {
    const revise = el("button", "small", "New revision");
    revise.type = "button";
    revise.addEventListener("click", () => editDraft(row));
    cell.append(revise);
  }

  // Only a word that is actually on the list can be taken off it; the API
  // says so too, and this is not a second copy of that rule but the reason
  // the button is absent rather than refused.
  if (inForce(row) === "approved" && !(row.draft && row.draft.retires)) {
    const retire = el("button", "ghost small", "Retire");
    retire.type = "button";
    retire.addEventListener("click", () => retireIt(row));
    cell.append(" ", retire);
  }
  return cell;
}

/* ---------- drafting ---------- */

function editDraft(row) {
  // The draft where there is one: that is the text being worked on. A form
  // that loaded the approved wording over the top of an unsigned edit would
  // quietly undo it on save.
  const shown = row.draft || row.in_force || {};
  $("#r-code").value = row.code;
  $("#r-name").value = shown.name || "";
  $("#r-description").value = shown.description || "";
  $("#form-title").textContent = row.draft
    ? `Editing the open draft of ${row.code}`
    : `A new revision of ${row.code}`;
  $("#r-cancel").classList.remove("hidden");
  $("#r-name").focus();
}

function resetForm() {
  $("#reason-form").reset();
  $("#form-title").textContent = "A new word";
  $("#r-cancel").classList.add("hidden");
}

async function submit(event) {
  event.preventDefault();
  try {
    // Nothing here re-states a rule the server owns. The code's spelling, its
    // uniqueness and the words the product has already taken are checked in
    // one place, and what a person reads when one of them refuses is the
    // server's own sentence — a second copy in JavaScript is a second copy
    // that drifts, and the one that drifts is the one people read.
    const out = await api("/equipment/downtime-reasons", { method: "POST", body: {
      code: $("#r-code").value.trim(),
      name: $("#r-name").value.trim(),
      description: $("#r-description").value.trim(),
    } });
    toast(`${out.code} rev ${out.revision} drafted — it reaches the floor when `
      + "somebody signs it on the Floor screen");
    resetForm();
    await load();
  } catch (err) { toast(err.message, "bad"); }
}

async function retireIt(row) {
  // The number comes before the decision, not after a refusal. The API asks
  // the drafter to say how much history carries the code; this screen is
  // where they are told what that number is.
  const carried = row.labels_intervals;
  const name = (row.in_force || {}).name || row.code;
  const ask = carried
    ? `Retire ${row.code}? ${carried.toLocaleString()} recorded `
      + `${carried === 1 ? "interval carries" : "intervals carry"} it. They keep `
      + "the label — retiring changes what may be chosen next, never what was "
      + "chosen before. The retirement is a draft until somebody signs it."
    : `Retire ${row.code}? No recorded interval carries it. The retirement is a `
      + "draft until somebody signs it.";
  if (!window.confirm(ask)) return;

  try {
    await api("/equipment/downtime-reasons", { method: "POST", body: {
      code: row.code, name, description: (row.in_force || {}).description || "",
      retires: true, labels_intervals: carried,
    } });
    toast(`${row.code}: retirement drafted — it leaves the list when somebody signs it`);
    await load();
  } catch (err) { toast(err.message, "bad"); }
}

/* ---------- boot ---------- */

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  $("#reason-form").addEventListener("submit", submit);
  $("#r-cancel").addEventListener("click", resetForm);
  for (const id of ["v-q", "v-status"]) {
    $(`#${id}`).addEventListener("input", () => { offset = 0; draw(); });
  }
  const run = () => load().then(() => live(true)).catch(fail);
  await run();
  setInterval(run, REFRESH_MS);
})().catch(fail);
