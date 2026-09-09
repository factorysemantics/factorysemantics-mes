/* Engineering › Machines: the plant as a tree.

   The hierarchy has been in the schema since the port and drawn nowhere.
   This is the first screen that walks it - every depth, with the cost
   center each node inherits or overrides, the state of every machine, and a
   link to each machine's own page.

   ?under=CODE scopes the tree to one node - an area, a site, a cell - which
   is where a machine page's breadcrumb lands when a level above the line is
   clicked. The KPIs then count that node, and say so (STYLE.md rule 5). */

const { $, el, api, fmt } = window.FS;
const REFRESH_MS = 5000;
const under = new URL(location).searchParams.get("under") || "";

function live(ok) {
  $("#live-dot").className = "dot" + (ok ? " ok" : " bad");
  $("#live-text").textContent = ok ? "live" : "reconnecting…";
}

function node(n, alarms) {
  const li = el("li");
  const row = el("div", "node");
  row.append(el("span", "level", n.level.replace("_", " ")));
  if (n.level === "work_unit") {
    row.append(FS.link("machine", n.code));
    row.append(el("span", "muted", n.name));
    row.append(el("span", `pill ${n.state}`, n.state));
    const active = (alarms[n.code] || []);
    if (active.length) row.append(el("span", "alarm small", active.join(", ")));
  } else if (n.level === "work_center") {
    row.append(FS.link("line", n.code));
    row.append(el("span", "muted", n.name));
  } else {
    // Any other level narrows the tree to itself, so a plant of twenty
    // lines is read one area at a time.
    row.append(FS.link("equipment", n.code));
    row.append(el("span", "muted", n.name));
  }
  if (n.cost_center) row.append(el("span", "cc", `cc ${n.cost_center}`));
  li.append(row);
  if (n.children && n.children.length) {
    const ul = el("ul");
    for (const child of n.children) ul.append(node(child, alarms));
    li.append(ul);
  }
  return li;
}

function count(nodes, pred) {
  let n = 0;
  for (const x of nodes) {
    if (x.level === "work_unit" && pred(x)) n += 1;
    n += count(x.children || [], pred);
  }
  return n;
}

/* The node named by ?under=, and the path down to it. */
function find(nodes, code, path = []) {
  for (const n of nodes) {
    if (n.code === code) return { node: n, path };
    const hit = find(n.children || [], code, [...path, n]);
    if (hit) return hit;
  }
  return null;
}

function scope(tree) {
  const crumbs = $("#tree-crumbs");
  crumbs.replaceChildren();
  if (!under) return { roots: tree.roots, label: "the plant" };
  const hit = find(tree.roots, under);
  if (!hit) {
    crumbs.append(el("span", "muted", `no equipment node ${under}`), el("span", "sep", "›"));
    const all = el("a", "obj", "the whole plant");
    all.href = "/dashboard/machines";
    crumbs.append(all);
    return { roots: tree.roots, label: "the plant" };
  }
  const all = el("a", "obj", "Plant");
  all.href = "/dashboard/machines";
  crumbs.append(all, el("span", "sep", "›"));
  for (const ancestor of hit.path) {
    crumbs.append(FS.link("equipment", ancestor.code, null, ancestor.name || ancestor.code), el("span", "sep", "›"));
  }
  crumbs.append(el("span", "mono", hit.node.code));
  return { roots: [hit.node], label: `${hit.node.level.replace("_", " ")} ${hit.node.code}` };
}

async function refresh() {
  try {
    const [tree, alarms] = await Promise.all([api("/equipment/tree"), api("/equipment/alarms")]);
    const active = Object.fromEntries(alarms.map((a) => [a.equipment, a.active]));
    const { roots, label } = scope(tree);
    const codes = new Set();
    (function collect(nodes) { for (const n of nodes) { if (n.level === "work_unit") codes.add(n.code); collect(n.children || []); } })(roots);

    const host = $("#tree");
    host.replaceChildren(...roots.map((r) => node(r, active)));
    const title = $("#tree-title");
    title.replaceChildren(under ? `Within ${label} ` : "The plant ",
      el("span", "muted", under
        ? `— ${codes.size} of ${count(tree.roots, () => true)} machines in the plant`
        : "— site, lines, cells, machines, and whose cost center each bills to"));
    document.querySelectorAll(".kpi-label").forEach((k) => {
      k.textContent = k.textContent.replace(/ \(.*\)$/, "") + (under ? ` (${label})` : "");
    });
    $("#kpi-machines").textContent = count(roots, () => true);
    $("#kpi-running").textContent = count(roots, (m) => m.state === "running");
    $("#kpi-down").textContent = count(roots, (m) => m.state === "down");
    const inAlarm = alarms.filter((a) => a.active.length && codes.has(a.equipment));
    $("#kpi-alarms").textContent = inAlarm.length;
    const list = $("#alarms");
    list.replaceChildren();
    if (!inAlarm.length) list.append(el("li", "muted", `No alarm bits set on any machine in ${label}.`));
    for (const a of inAlarm) {
      const li = el("li");
      li.append(FS.link("machine", a.equipment), " ", el("span", "alarm", a.active.join(", ")),
                " ", el("span", "muted small", fmt.clock(a.ts)));
      list.append(li);
    }
    window.__fsmesPageData = { under: under || null, scope: label, machines: codes.size, alarms: inAlarm };
    live(true);
  } catch (err) {
    live(false);
    const banner = $("#banner");
    banner.textContent = err.message;
    banner.classList.remove("hidden");
  }
}

(async function boot() {
  await FS.whoami().catch(() => {});
  await refresh();
  setInterval(refresh, REFRESH_MS);
})();
