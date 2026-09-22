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

/* A section that is a key in the plant's pack rather than a list somebody
   edits on a screen. Neither capability column can say the truth about one:
   nobody drafts it and nobody signs it. So both cells say where it lives and
   what changing it takes, and the row still points at the screen its effect
   is read on. */
function packCell(key, half) {
  const cell = el("td");
  if (half === "define") {
    cell.appendChild(el("code", null, key));
    cell.append(" ");
    cell.appendChild(el("span", "muted small", "— in this plant's pack"));
  } else {
    cell.appendChild(el("span", "muted",
                        "nobody — it changes when the pack is applied and the plant restarts"));
  }
  return cell;
}

function draw(page) {
  $("#domain-title").textContent = `${page.title} configuration`;
  document.title = `${page.title} configuration — FactorySemantics MES`;
  $("#domain-about").textContent = page.about;

  /* Every list states its total, even when the total is one: this is a list
     of sections that happens to be short today, not a screen wearing a
     plural name. */
  $("#section-count").textContent =
    `— ${page.total.toLocaleString()} ${page.total === 1 ? "section" : "sections"}`;

  const body = $("#sections-table").querySelector("tbody");
  body.replaceChildren();
  for (const section of page.items) {
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
    if (section.pack_key) {
      row.appendChild(packCell(section.pack_key, "define"));
      row.appendChild(packCell(section.pack_key, "approve"));
    } else {
      row.appendChild(whoCell(section.define, section.may_define,
                              "anybody who can see this screen"));
      row.appendChild(whoCell(section.approve, section.may_approve,
                              "nobody — a change here takes effect when it is saved"));
    }
    body.appendChild(row);
  }

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

/* ---------- boot ---------- */

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  draw(await api(`/dashboard/config/${encodeURIComponent(DOMAIN)}/sections`));
  live(true);
})().catch(fail);
