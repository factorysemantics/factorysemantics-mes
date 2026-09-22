/* <Workspace> › Configuration: one page per domain, every configurable thing
   in that domain on it.

   Scott, 2026-09-21, after the downtime vocabulary arrived in the nav bar as
   a chip of its own: he does not want a top-level entry per configurable
   thing. There will eventually be hundreds of them, and a bar with a chip
   each is the clutter this whole effort exists to remove. So each workspace
   gets one Configuration entry, and this is what it opens.

   The page holds no configuration itself. It is a list of the sections that
   do, read from the module registry through
   /dashboard/config/{domain}/sections, so adding the next one is a registry
   entry rather than a change to the navigation - and each section keeps its
   own rules about who may draft and who may sign, exactly where its author
   put them. This screen only says where the door is. */

const { $, el, api } = window.FS;

/* Which workspace this is: the last segment of this page's own address, the
   way machine.html reads a machine code out of its URL. */
const DOMAIN = window.location.pathname.split("/").filter(Boolean).pop();

/* The header's live dot belongs to every screen, and a screen that never
   sets it says "connecting…" for as long as it is open. This page asks the
   server once, so the dot means exactly that: the list below came back. */
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

/* Who may act on a section, as one cell. The capability is named rather than
   hidden: an administrator composing a role needs to know which word to put
   in the bundle, and it is the same word the API refuses with. */
function whoCell(capability, held, noneMeans) {
  const cell = el("td");
  if (!capability) {
    cell.appendChild(el("span", "muted", noneMeans));
    return cell;
  }
  cell.appendChild(el("code", null, capability));
  cell.append(" ");
  cell.appendChild(el("span", "muted small",
                      held ? "— you hold this" : "— not yours"));
  return cell;
}

/* A section that is keys in the plant's pack rather than a list somebody
   edits on a screen. Neither capability column can say the truth about one:
   nobody drafts it and nobody signs it. So both cells say where it lives and
   what changing it takes, and the row still points at the screen its effect
   is read on. */
function packCell(keys, half) {
  const cell = el("td");
  if (half === "define") {
    for (const key of keys) {
      const line = el("div");
      line.appendChild(el("code", null, key.key));
      if (key.about) line.title = key.about;
      cell.appendChild(line);
    }
    cell.appendChild(el("span", "muted small", "in this plant's pack"));
  } else {
    cell.appendChild(el("span", "muted",
                        "nobody — it changes when the pack is applied and the plant restarts"));
  }
  return cell;
}

/* What this plant is actually running on, and whether the plant chose it.

   Two values, not three, and the page says only what it can know: by the time
   a plant is serving, a pack key is an environment variable and the file it
   was compiled from is not recorded anywhere this process can see. So the
   honest answers are "the product's default, unchanged" and "this plant set
   it" — naming a pack would be a guess, and this column exists so nobody has
   to guess what their plant is set to. */
function valueCell(section) {
  const cell = el("td");
  if (!section.pack_keys.length) {
    // A vocabulary is not a value: it is a list with its own screen, and its
    // own count is on that screen rather than summarised here.
    cell.appendChild(el("span", "muted small", "—"));
    return cell;
  }
  for (const key of section.pack_keys) {
    const line = el("div");
    line.appendChild(el("span", "code", key.value === "" ? "(nothing)" : key.value));
    line.appendChild(el("span", "muted small",
                        key.is_default ? " — the product's default" : " — this plant set it"));
    cell.appendChild(line);
  }
  return cell;
}

/* Twelve, which is not this file's number: `services/analysis.py` already
   answered "how many rows is one screenful" once, and a codebase with two
   answers to one question has neither. */
const SCREENFUL = 12;

let listed = null;

function matches(section, q) {
  if (!q) return true;
  const parts = [section.label, section.about, section.define || "", section.approve || ""];
  for (const key of section.pack_keys) parts.push(key.key, key.value, key.about || "");
  return parts.join(" ").toLowerCase().includes(q);
}

function draw(page) {
  listed = page;
  $("#domain-title").textContent = `${page.title} configuration`;
  document.title = `${page.title} configuration — FactorySemantics MES`;
  $("#domain-about").textContent = page.about;

  /* Past a screenful, the list needs search and a stated, stable order. The
     order is the module registry's, which is the order this product mounts
     its modules in and does not change between loads; it is said out loud
     rather than left to be inferred, because an order a person cannot name
     is an order they cannot trust between two readings of the same page. */
  const many = page.total > SCREENFUL;
  $("#section-filter").classList.toggle("hidden", !many);
  $("#section-sort").textContent = many
    ? `Sorted by the module each section belongs to, in the order this plant `
      + `serves them — the same order on every load.`
    : "";

  redraw();

  const empty = $("#sections-empty");
  empty.classList.toggle("hidden", page.total > 0);
  empty.textContent = "Nothing in this workspace is configurable on this plant.";

  /* What this plant does not serve. Named rather than counted away: a
     workspace with one section and a workspace with one section and three
     modules switched off are different plants, and only one of them has a
     missing screen to go looking for. */
  const off = $("#switched-off");
  off.classList.toggle("hidden", !page.switched_off.length);
  off.textContent = page.switched_off.length
    ? `${page.switched_off.length} more in this version, not served by this plant `
      + `(MES_MODULES): ${page.switched_off.join(", ")}.`
    : "";
}

/* The rows themselves, redrawn when the search changes. The count says what
   it covers - the filter's total and the workspace's - because a page that
   showed three of twelve and said "3 sections" would be a page telling
   somebody their plant has three. */
function redraw() {
  const q = ($("#c-q").value || "").trim().toLowerCase();
  const shown = listed.items.filter((section) => matches(section, q));

  const body = $("#sections-table").querySelector("tbody");
  body.replaceChildren();
  for (const section of shown) {
    const row = el("tr");

    const name = el("td");
    name.style.whiteSpace = "nowrap";
    // The same link every in-table link on these screens is: `a.obj`, which
    // is the one styled anchor this product has inside a table.
    const link = el("a", "obj", section.label);
    link.href = section.href;
    name.appendChild(link);
    row.appendChild(name);

    row.appendChild(el("td", "muted", section.about));
    row.appendChild(valueCell(section));
    if (section.pack_keys.length) {
      row.appendChild(packCell(section.pack_keys, "define"));
      row.appendChild(packCell(section.pack_keys, "approve"));
    } else {
      row.appendChild(whoCell(section.define, section.may_define,
                              "anybody who can see this screen"));
      row.appendChild(whoCell(section.approve, section.may_approve,
                              "nobody — a change here takes effect when it is saved"));
    }
    body.appendChild(row);
  }

  /* Every list states its total, even when the total is one: this is a list
     of sections that happens to be short today, not a screen wearing a
     plural name. */
  const noun = listed.total === 1 ? "section" : "sections";
  $("#section-count").textContent = shown.length === listed.total
    ? `— ${listed.total.toLocaleString()} ${noun}`
    : `— ${shown.length.toLocaleString()} of ${listed.total.toLocaleString()} ${noun}`;

  if (!shown.length && listed.total) {
    const row = el("tr");
    const cell = el("td", "muted", "Nothing in this workspace matches.");
    cell.colSpan = 5;
    row.appendChild(cell);
    body.appendChild(row);
  }
}

/* ---------- boot ---------- */

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  $("#c-q").addEventListener("input", redraw);
  draw(await api(`/dashboard/config/${encodeURIComponent(DOMAIN)}/sections`));
  live(true);
})().catch(fail);
