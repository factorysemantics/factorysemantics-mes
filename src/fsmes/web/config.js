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

/* One setting somebody was sent here for, named in the query string:
   `?setting=cpk_capable`. The assistant's walk arrives this way, and so does
   the "what is it now" link after a change is saved - both of them need to
   point at one box on a page of them, and a path cannot say which. The box and
   its section's Save carry an anchor the walk can find; a person who came here
   by hand names no setting and sees exactly what they saw before. */
const FOCUS = new URLSearchParams(window.location.search).get("setting");

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

/* A pack-key section that is *not* edited here: nobody drafts it and nobody
   signs it, so neither capability column can say the truth about one and both
   cells say instead where it lives and what changing it takes.

   Every section on this product answered this way until 2026-09-24, when the
   eleven `[quality]` keys moved onto `plant_settings` and became live -
   `edit_here` on the registry entry is what tells the two apart. Three
   sections reach it, all on Setup > Configuration, and they are what it was
   kept for: the list envelope's default and ceiling are published in this
   plant's own API document and cannot move under a caller holding it, logging
   is configured before the plant's database is open, and the fleet probe is
   the console's number rather than any one plant's. A box that appeared to
   work and took effect at the next restart would be worse than this
   sentence. */
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

   Two values, not three, and the page says only what it can know: **the
   product's default, unchanged**, or **this plant set it**. Naming the pack
   that wrote it would be a guess even now that the value is a row in this
   plant's own database, because a row written by `fsmes pack apply` says which
   door the value came in by and not which file - and this column exists so
   that nobody has to guess what their plant is set to.

   For a section its registry entry marks `edit_here`, the cell is where the
   value is changed as well as read: one input per key, and one Save for the
   section, because the Cpk bars and the gauge ratios are each one judgment
   written as two numbers and a screen that saved half of one would be a screen
   that made `marginal` unreachable for as long as it took to type the other
   half. A person without the section's `define` capability reads the same
   values and gets no input - the gate is the server's, and this only declines
   to offer a control the server would refuse. */
function valueCell(section) {
  const cell = el("td");
  if (!section.pack_keys.length) {
    // A vocabulary is not a value: it is a list with its own screen, and its
    // own count is on that screen rather than summarised here.
    cell.appendChild(el("span", "muted small", "—"));
    return cell;
  }
  const live = section.edit_here && section.may_define;
  for (const key of section.pack_keys) {
    const line = el("div", live ? "setting-line" : null);
    if (live) {
      const field = el("input");
      field.type = "text";
      field.value = key.value;
      field.size = Math.max(6, Math.min(24, (key.value || "").length + 4));
      field.setAttribute("aria-label", key.key);
      field.dataset.settingKey = key.name;
      field.title = key.about || key.key;
      if (key.name === FOCUS) field.dataset.assist = "setting-in-focus";
      line.appendChild(field);
      line.append(" ");
      line.appendChild(el("code", "muted small", key.key));
    } else {
      line.appendChild(el("span", "code", key.value === "" ? "(nothing)" : key.value));
    }
    line.appendChild(el("span", "muted small",
                        key.is_default ? " — the product's default" : " — this plant set it"));
    cell.appendChild(line);
  }
  if (live) cell.appendChild(saveRow(section, cell));
  return cell;
}

/* The one Save for one section, and the sentence it leaves behind.

   There is no approve step and no draft to review: rule three of decision
   0035 says a number a plant administrator edits and which takes effect when
   saved has no pending state, and these have none - nothing anywhere records
   the Cpk bar that was in force when it judged something, so there is nothing
   for a revision to protect. Undo is typing the old number back; the audit
   trail says what it was. */
function saveRow(section, cell) {
  const row = el("div", "setting-save");
  const button = el("button", "small", "Save");
  /* One Save per section, so it is this section's Save that a walk about one
     of its keys has to end on. */
  if (section.pack_keys.some((k) => k.name === FOCUS)) {
    button.dataset.assist = "setting-save-in-focus";
  }
  const said = el("span", "muted small");
  button.addEventListener("click", async () => {
    const fields = [...cell.querySelectorAll("input[data-setting-key]")];
    const changed = fields.filter((f) => f.value !== originalOf(section, f.dataset.settingKey));
    if (!changed.length) {
      said.textContent = "nothing changed";
      said.className = "muted small";
      return;
    }
    button.disabled = true;
    said.textContent = "saving…";
    said.className = "muted small";
    try {
      const failed = await saveEach(changed);
      /* One retry, and only for what was refused, because the two pairs are
         judged against each other: moving both Cpk bars down means the first
         save is refused by the second's old value, and refusing the person's
         whole edit for the order they typed it in would be this page being
         clever at their expense. Anything still refused after the other half
         has landed is a real refusal, and its own sentence is what is shown. */
      const stillFailed = failed.length ? await saveEach(failed.map((f) => f.field)) : [];
      if (stillFailed.length) throw new Error(stillFailed[0].why);
      FS.toast("saved — in force now, with no restart");
      draw(await api(`/dashboard/config/${encodeURIComponent(DOMAIN)}/sections`));
    } catch (error) {
      said.textContent = error.message;
      said.className = "refused";
      button.disabled = false;
    }
  });
  row.appendChild(button);
  row.append(" ");
  row.appendChild(said);
  return row;
}

/* Each changed field, saved on its own: one setting is one row and one audit
   entry, so a screen that posted four at once would be one record of four
   decisions. Returns what was refused, with the server's own sentence. */
async function saveEach(fields) {
  const failed = [];
  for (const field of fields) {
    try {
      const key = encodeURIComponent(field.dataset.settingKey);
      await api(`/dashboard/config/${encodeURIComponent(DOMAIN)}/settings/${key}`,
                { method: "PATCH", body: { value: field.value } });
    } catch (error) {
      failed.push({ field, why: error.message });
    }
  }
  return failed;
}

/* What the server last said this key was, so Save sends only what moved. Read
   from the listing rather than remembered in a variable of its own: the
   listing is redrawn from the server after every save, which makes it the one
   answer to *what is this plant set to* on this page. */
function originalOf(section, name) {
  const key = section.pack_keys.find((k) => k.name === name);
  return key ? key.value : "";
}

/* Twelve, which is not this file's number: `services/analysis.py` already
   answered "how many rows is one screenful" once, and a codebase with two
   answers to one question has neither. */
const SCREENFUL = 12;

let listed = null;

function matches(section, q) {
  if (!q) return true;
  const parts = [section.label, section.about, section.define || "", section.approve || ""];
  for (const key of section.pack_keys) parts.push(key.key, key.name, key.value, key.about || "");
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
    ? `Grouped by the module each section belongs to, in the order this plant `
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
  /* The grouping the sort line claims, drawn. Engineering carries eighteen
     sections from six different modules, and an order a reader is told about
     but cannot see is an order they have to take on trust between two
     readings. One heading per module, only where there is more than one group
     to tell apart, and it follows the filter: a search that leaves two rows
     from one module says which module, not all six. */
  let group = null;
  const groups = new Set(shown.map((s) => s.module).filter(Boolean));
  for (const section of shown) {
    if (groups.size > 1 && section.module && section.module !== group) {
      group = section.module;
      const heading = el("tr", "group");
      const cell = el("th", null, group);
      cell.colSpan = 5;
      cell.scope = "colgroup";
      heading.appendChild(cell);
      body.appendChild(heading);
    }
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
    if (section.pack_keys.length && !section.edit_here) {
      row.appendChild(packCell(section.pack_keys, "define"));
      row.appendChild(packCell(section.pack_keys, "approve"));
    } else {
      /* A live pack-key section answers both columns honestly with the
         ordinary cells: somebody holding `define` writes it, and nobody signs
         it because it is in force when it is saved. The keys themselves have
         moved into the value cell, beside the input for each. */
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
