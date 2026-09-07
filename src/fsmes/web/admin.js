/* Administration: people, roles, routings.

   The screen gates itself on users.manage rather than on a role name, for the
   same reason the API does - what matters is what you may do. */

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

let me = null;
let roles = [];
let capabilities = [];
let materials = [];
let userTotal = 0;
let userHasMore = false;
let userOffset = 0;
let userFilterRole = "";
let userFilterText = "";
const userPageSize = 25;
let equipment = [];
let routings = [];
let routingText = "";
let routingMaterial = "";
let routingOffset = 0;
const routingPageSize = 25;

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

/* ---------- people ---------- */

function renderUsers(users) {
  const body = $("#users tbody");
  body.textContent = "";
  if (!users.length) { const tr = el("tr"); const td = el("td", "muted", "Nobody matches."); td.colSpan = 4; tr.append(td); body.append(tr); }
  for (const u of users) {
    const row = el("tr");
    row.appendChild(el("td", "mono", u.code));
    row.appendChild(el("td", null, u.name));

    const cell = el("td");
    const select = el("select");
    for (const role of roles) {
      const opt = el("option", null, role.name);
      opt.value = role.code;
      if (role.code === u.role) opt.selected = true;
      select.appendChild(opt);
    }
    select.addEventListener("change", async () => {
      try {
        await api(`/admin/users/${u.code}/role`, { method: "PUT", body: { role: select.value } });
        toast(`${u.code} is now ${select.value} — effective immediately`);
        await refresh();
      } catch (err) {
        toast(err.message, "bad");
        select.value = u.role;
      }
    });
    cell.appendChild(select);
    row.appendChild(cell);

    const signIn = el("td", u.can_sign_in ? "" : "muted", u.can_sign_in ? "yes" : "no");
    const reset = el("button", "ghost small", "Reset password…");
    reset.type = "button";
    reset.addEventListener("click", async () => {
      const password = window.prompt(`New password for ${u.code}:`);
      if (!password) return;
      try {
        await api(`/auth/users/${u.code}/password`, { method: "POST", body: { password } });
        toast(`${u.code}: password set`);
      } catch (err) { toast(err.message, "bad"); }
    });
    signIn.appendChild(document.createTextNode(" "));
    signIn.appendChild(reset);
    row.appendChild(signIn);
    body.appendChild(row);
  }
}

/* ---------- roles ---------- */

function renderRoles() {
  const list = $("#roles");
  list.textContent = "";
  for (const role of roles) {
    const card = el("div", "role");
    const head = el("div", "role-head");
    head.appendChild(el("strong", null, role.name));
    head.appendChild(el("span", "code", role.code));
    if (role.builtin) head.appendChild(el("span", "grant", "built-in"));
    card.appendChild(head);
    if (role.description) card.appendChild(el("p", null, role.description));

    const grants = el("div", "grants");
    if (!role.capabilities.length) {
      grants.appendChild(el("span", "grant", "nothing"));
    }
    for (const cap of role.capabilities) grants.appendChild(el("span", "grant", cap));
    card.appendChild(grants);

    const editRow = el("div", "role-actions");
    const edit = el("button", "ghost small", "Edit");
    edit.type = "button";
    edit.addEventListener("click", () => {
      const f = $("#form-role");
      f.code.value = role.code;
      f.code.readOnly = true;
      f.name.value = role.name;
      f.description.value = role.description || "";
      document.querySelectorAll("#cap-checks input").forEach((b) => (b.checked = role.capabilities.includes(b.value)));
      f.dataset.editing = role.code;
      f.querySelector("button").textContent = `Save ${role.code}`;
      f.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    editRow.appendChild(edit);
    card.appendChild(editRow);
    if (!role.protected) {
      const actions = el("div", "role-actions");
      const del = el("button", "ghost", "Delete");
      del.addEventListener("click", async () => {
        try {
          await api(`/admin/roles/${role.code}`, { method: "DELETE" });
          toast(`${role.code} deleted`);
          await refresh();
        } catch (err) {
          // Refusals here are the system protecting itself - a role still held
          // by someone, or the admin role. Show the reason, it is specific.
          toast(err.message, "bad");
        }
      });
      actions.appendChild(del);
      card.appendChild(actions);
    }
    list.appendChild(card);
  }

  const select = $("#new-user-role");
  select.textContent = "";
  for (const role of roles) {
    const opt = el("option", null, role.name);
    opt.value = role.code;
    if (role.code === "operator") opt.selected = true;
    select.appendChild(opt);
  }
}

function renderCapabilityChecks() {
  const grid = $("#cap-checks");
  grid.textContent = "";
  for (const cap of capabilities) {
    const label = el("label", "cap");
    const box = el("input");
    box.type = "checkbox";
    box.value = cap.name;
    label.appendChild(box);
    label.appendChild(el("code", null, cap.name));
    label.appendChild(el("span", "what", cap.description));
    grid.appendChild(label);
  }
}

/* ---------- routings ---------- */

function renderRoutings() {
  // One routing per material is the norm, so this is the plant's product
  // count: filtered and paged on the screen, with the total stated.
  const q = routingText.toLowerCase();
  const matching = routings.filter((r) =>
    (!routingMaterial || r.material === routingMaterial)
    && (!q || `${r.code} ${r.name}`.toLowerCase().includes(q)));
  const page = window.FS.clientPage(matching, routingOffset, routingPageSize);
  routingOffset = page.offset;
  const body = $("#routings tbody");
  body.textContent = "";
  if (!page.items.length) { const tr = el("tr"); const td = el("td", "muted", "No routing matches."); td.colSpan = 4; tr.append(td); body.append(tr); }
  for (const r of page.items) {
    const row = el("tr");
    row.appendChild(el("td", "mono", r.code));
    row.appendChild(el("td", null, r.name));
    row.appendChild(el("td", null, r.material));
    row.appendChild(el("td", "muted",
      r.operations.map((o) => `${o.seq} ${o.name} @ ${o.equipment}`).join("  →  ")));
    body.appendChild(row);
  }
  $("#routing-count").textContent = window.FS.countText(page, routings.length);
  window.FS.pager($("#r-pager"), page, (offset) => { routingOffset = offset; renderRoutings(); });
}

function addOperationRow(seq) {
  const row = el("div", "op-row");
  const seqInput = el("input");
  seqInput.name = "seq"; seqInput.type = "number"; seqInput.value = seq; seqInput.step = 10;
  const nameInput = el("input");
  nameInput.name = "op-name"; nameInput.placeholder = "Operation";
  const machine = el("select");
  machine.name = "op-equipment";
  for (const e of equipment) {
    const opt = el("option", null, `${e.code} — ${e.name}`);
    opt.value = e.code;
    machine.appendChild(opt);
  }
  const remove = el("button", null, "×");
  remove.type = "button";
  remove.addEventListener("click", () => row.remove());

  row.append(seqInput, nameInput, machine, remove);
  $("#ops").appendChild(row);
}

/* ---------- wiring ---------- */

$("#form-user").addEventListener("submit", async (event) => {
  event.preventDefault();
  const f = event.target;
  try {
    await api("/auth/users", { method: "POST", body: {
      code: f.code.value.trim().toUpperCase(),
      name: f.name.value.trim(),
      password: f.password.value,
      role: f.role.value,
    }});
    toast(`${f.code.value.toUpperCase()} created`);
    f.reset();
    await refresh();
  } catch (err) { toast(err.message, "bad"); }
});

$("#form-role").addEventListener("submit", async (event) => {
  event.preventDefault();
  const f = event.target;
  const chosen = [...document.querySelectorAll("#cap-checks input:checked")].map((b) => b.value);
  if (!chosen.length) { toast("A role that grants nothing is not a role.", "bad"); return; }
  try {
    const editing = f.dataset.editing;
    const body = {
      code: f.code.value.trim(),
      name: f.name.value.trim(),
      description: f.description.value.trim() || null,
      capabilities: chosen,
    };
    // The same form defines a role and redefines one; the difference is
    // whether Edit filled it. A redefinition is effective on everyone's next
    // action, because capabilities are read live.
    await api(editing ? `/admin/roles/${editing}` : "/admin/roles", { method: editing ? "PUT" : "POST", body });
    toast(editing ? `${editing} redefined — effective immediately` : `${f.code.value} created`);
    f.reset();
    delete f.dataset.editing;
    f.code.readOnly = false;
    f.querySelector("button").textContent = "Create role";
    document.querySelectorAll("#cap-checks input:checked").forEach((b) => (b.checked = false));
    await refresh();
  } catch (err) { toast(err.message, "bad"); }
});

$("#add-op").addEventListener("click", () => {
  addOperationRow(($("#ops").children.length + 1) * 10);
});

$("#form-routing").addEventListener("submit", async (event) => {
  event.preventDefault();
  const f = event.target;
  const operations = [...$("#ops").children].map((row) => ({
    seq: Number(row.querySelector('[name="seq"]').value),
    name: row.querySelector('[name="op-name"]').value.trim(),
    equipment: row.querySelector('[name="op-equipment"]').value,
  })).filter((o) => o.name);
  if (!operations.length) { toast("A routing needs at least one operation.", "bad"); return; }
  try {
    await api("/masterdata/routings", { method: "POST", body: {
      code: f.code.value.trim(),
      name: f.name.value.trim(),
      material: f.material.value,
      operations,
    }});
    toast(`${f.code.value} created with ${operations.length} operation(s)`);
    f.reset();
    $("#ops").textContent = "";
    addOperationRow(10);
    await refresh();
  } catch (err) { toast(err.message, "bad"); }
});

async function refresh() {
  try {
    const query = new URLSearchParams({ limit: String(userPageSize),
                                        offset: String(userOffset) });
    if (userFilterRole) query.set("role", userFilterRole);
    if (userFilterText) query.set("q", userFilterText);
    const [rolesOut, users, routingsOut] = await Promise.all([
      api("/admin/roles"), api(`/admin/users?${query}`), api("/masterdata/routings"),
    ]);
    roles = rolesOut;
    routings = routingsOut;
    userTotal = users.total;
    userHasMore = users.has_more;
    renderRoles();
    renderUsers(users.items);
    $("#user-count").textContent = users.total === undefined ? ""
      : `— ${users.items.length} of ${users.total.toLocaleString()}${userFilterText || userFilterRole ? " matching" : ""}`;
    window.FS.pager($("#u-pager"), users, (offset) => { userOffset = offset; refresh(); });
    const roleSelect = $("#u-role");
    const keep = roleSelect.value;
    roleSelect.replaceChildren(new Option("Any role", ""), ...roles.map((r) => new Option(r.name, r.code)));
    if ([...roleSelect.options].some((o) => o.value === keep)) roleSelect.value = keep;
    renderRoutings();
    live(true);
  } catch (err) {
    live(false);
  }
}

(async function boot() {
  me = await api("/auth/me");
  const allowed = (me.capabilities || []).includes("users.manage");
  $("#denied-who").textContent = `${me.name} (${me.role})`;
  $("#denied").classList.toggle("hidden", allowed);
  $("#admin-main").classList.toggle("hidden", !allowed);
  if (!allowed) { live(true); return; }

  const [caps, mats, equip] = await Promise.all([
    api("/admin/capabilities"), api("/masterdata/materials"), api("/masterdata/equipment"),
  ]);
  capabilities = caps.capabilities;
  materials = mats;
  equipment = equip;

  renderCapabilityChecks();
  const select = $("#routing-material");
  for (const m of materials) {
    const opt = el("option", null, `${m.code} — ${m.name}`);
    opt.value = m.code;
    select.appendChild(opt);
    $("#r-material").appendChild(new Option(m.code, m.code));
  }
  let typing = null;
  $("#u-q").addEventListener("input", (e) => {
    clearTimeout(typing);
    typing = setTimeout(() => { userFilterText = e.target.value.trim(); userOffset = 0; refresh(); }, 250);
  });
  $("#u-role").addEventListener("change", (e) => { userFilterRole = e.target.value; userOffset = 0; refresh(); });
  $("#r-q").addEventListener("input", (e) => { routingText = e.target.value.trim(); routingOffset = 0; renderRoutings(); });
  $("#r-material").addEventListener("change", (e) => { routingMaterial = e.target.value; routingOffset = 0; renderRoutings(); });
  addOperationRow(10);

  await refresh();
  setInterval(refresh, 8000);
})();
