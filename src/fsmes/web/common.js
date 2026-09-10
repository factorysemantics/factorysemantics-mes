/* The one copy of everything every screen was carrying its own copy of.

   Before this file existed, api() was defined nine times with drifted
   signatures (one was GET-only, one didn't handle 401), toast() four times,
   and the nav was pasted into eight HTML files - which is how one page grew a
   duplicate link and seven lost their current-page marker.

   Everything lives under the FS namespace rather than as globals, so a page
   that still declares its own `const $` keeps working until it is migrated.
   The per-page copies are deleted screen by screen; a test ratchet in
   test_web.py stops new ones appearing. */

(function () {
  "use strict";

  const FS = {};

  /* ---------- the navigation, as data ----------
     One manifest, filtered by what the signed-in person may actually do.
     `cap: null` means everyone. A link a person cannot use is not shown -
     an operator clicking Admin and landing on "Not your screen" was the
     old behaviour, and a dead-end chip is worse than no chip. */

  FS.NAV = [
    { group: "Floor", items: [
      { href: "/dashboard", label: "Floor", cap: null },
      { href: "/dashboard/station", label: "Station", cap: null },
      { href: "/dashboard/line", label: "Line", cap: null },
    ] },
    { group: "Orders", items: [
      { href: "/dashboard/orders", label: "Orders", cap: null },
      { href: "/dashboard/schedule", label: "Schedule", cap: null },
    ] },
    { group: "Quality", items: [
      { href: "/dashboard/quality", label: "Quality", cap: null },
      { href: "/dashboard/spc", label: "SPC", cap: null },
      { href: "/dashboard/gauges", label: "Gauges", cap: null },
      { href: "/dashboard/trace", label: "Trace", cap: null },
      { href: "/dashboard/coa", label: "Certificates", cap: null },
    ] },
    { group: "Maintenance", items: [
      { href: "/dashboard/maintenance", label: "Maintenance", cap: null },
    ] },
    { group: "Engineering", items: [
      { href: "/dashboard/machines", label: "Machines", cap: null },
      { href: "/dashboard/tags", label: "Tags", cap: null },
      { href: "/dashboard/triggers", label: "Triggers", cap: null },
      { href: "/dashboard/adjustments", label: "Adjustments", cap: null },
      { href: "/dashboard/analysis", label: "Analysis", cap: null },
      { href: "/dashboard/masterdata", label: "Master data", cap: null },
    ] },
    { group: "Setup", items: [
      { href: "/dashboard/instructions", label: "Instructions", cap: null },
      { href: "/dashboard/ops", label: "Ops", cap: "audit.read" },
      { href: "/dashboard/admin", label: "Admin", cap: "users.manage" },
    ] },
  ];

  /* Object pages have no nav entry of their own; they are reached by links
     and mark their workspace's leaf as active (a machine page says
     "machines"). This is the one place that knows an object's URL. */
  FS.link = function link(kind, code, cls, text) {
    const hrefs = {
      machine: `/dashboard/machine/${encodeURIComponent(code)}`,
      line: `/dashboard/line?line=${encodeURIComponent(code)}`,
      // Enterprise, site, area, cell: the Machines tree, scoped to that node.
      equipment: `/dashboard/machines?under=${encodeURIComponent(code)}`,
      station: `/dashboard/station?m=${encodeURIComponent(code)}`,
      serial: `/dashboard/trace?serial=${encodeURIComponent(code)}`,
      lot: `/dashboard/trace?lot=${encodeURIComponent(code)}`,
      certificate: `/dashboard/coa?order=${encodeURIComponent(code)}`,
    };
    const a = FS.el("a", `obj${cls ? " " + cls : ""}`, text === undefined ? code : text);
    a.href = hrefs[kind] || "#";
    return a;
  };

  /* ---------- DOM helpers ---------- */

  FS.$ = (sel, root = document) => root.querySelector(sel);

  FS.el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  /* ---------- formatting ----------
     fmt.qty exists because a lot label once read "74.20000000000171 l" -
     floating point is an implementation detail no operator should meet. */

  FS.fmt = {
    qty: (v) => {
      if (v === null || v === undefined) return "—";
      const n = Math.round(Number(v) * 100) / 100;
      return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    },
    pct: (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%"),
    clock: (ts) => {
      if (!ts) return "";
      const s = String(ts);
      return new Date(s + (s.endsWith("Z") ? "" : "Z")).toLocaleTimeString();
    },
    stamp: (ts) => {
      if (!ts) return "—";
      const s = String(ts);
      return new Date(s + (s.endsWith("Z") ? "" : "Z")).toLocaleString();
    },
  };

  /* ---------- the API, once ---------- */

  FS.api = async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
    if (response.status === 401) {
      // The floor screen owns the login form; everything else goes there.
      if (window.location.pathname !== "/dashboard") window.location = "/dashboard";
      throw new Error("signed out");
    }
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error((data && data.detail) || `${response.status} ${response.statusText}`);
    }
    return data;
  };

  /* ---------- who am I, and what may I do ----------
     Cached: every gate on a page shares one /auth/me call. The server reads
     the role live, so a re-poll after sign-in changes is the page reload. */

  let me = null;
  let mePromise = null;

  FS.whoami = function whoami() {
    if (me) return Promise.resolve(me);
    if (!mePromise) {
      mePromise = FS.api("/auth/me").then((who) => {
        me = who;
        return who;
      }).catch((err) => {
        mePromise = null;
        throw err;
      });
    }
    return mePromise;
  };

  FS.can = (cap) => !!(me && (me.capabilities || []).includes(cap));

  /* Hide everything marked data-needs-cap that this person cannot use.
     Capabilities, never role names - the ladder is dead. */
  FS.applyCapGates = function applyCapGates(root = document) {
    root.querySelectorAll("[data-needs-cap]").forEach((node) => {
      node.classList.toggle("hidden", !FS.can(node.dataset.needsCap));
    });
  };

  /* ---------- toast ---------- */

  FS.toast = function toast(message, kind = "good") {
    let node = document.getElementById("toast");
    if (!node) {
      node = FS.el("div", "toast hidden");
      node.id = "toast";
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.className = `toast ${kind}`;
    clearTimeout(node._t);
    node._t = setTimeout(() => node.classList.add("hidden"), 3500);
  };

  /* ---------- paging ----------
     One renderer for the {items,total,limit,offset,has_more} envelope, so
     "Showing 50 of 18,347" reads the same on every list. */

  FS.pager = function pager(container, page, onMove) {
    container.textContent = "";
    const shownFrom = page.total === 0 ? 0 : page.offset + 1;
    const shownTo = page.offset + (page.items ? page.items.length : 0);
    container.appendChild(FS.el(
      "span", "muted",
      `${shownFrom.toLocaleString()}–${shownTo.toLocaleString()} of ${page.total.toLocaleString()}`));

    const back = FS.el("button", "ghost", "‹ Prev");
    back.type = "button";
    back.disabled = page.offset === 0;
    back.addEventListener("click", () => onMove(Math.max(0, page.offset - page.limit)));
    const fwd = FS.el("button", "ghost", "Next ›");
    fwd.type = "button";
    fwd.disabled = !page.has_more;
    fwd.addEventListener("click", () => onMove(page.offset + page.limit));
    container.append(back, fwd);
  };

  /* A list the server hands over whole - master data, plans, triggers:
     hundreds of rows, not millions - is still shown a page at a time, in the
     same envelope and the same words as a server page. `matching` is what
     passed the screen's filters. */
  FS.clientPage = function clientPage(matching, offset, limit) {
    if (offset >= matching.length) offset = 0;
    return { items: matching.slice(offset, offset + limit), total: matching.length, limit, offset,
             has_more: offset + limit < matching.length };
  };

  /* "— 25 of 306", or "— 25 of 41 matching, 306 in the plant": the count
     always says what it covers (STYLE.md rule 4). */
  FS.countText = function countText(page, allCount, where) {
    const shown = page.items.length.toLocaleString();
    if (page.total === allCount) return `— ${shown} of ${allCount.toLocaleString()}`;
    return `— ${shown} of ${page.total.toLocaleString()} matching, ${allCount.toLocaleString()} ${where || "in the plant"}`;
  };

  /* ---------- the header, rendered from the manifest ----------
     A page opts in with <header data-nav="orders">. Structure lands
     synchronously (so page scripts that bind #logout or #live-dot find
     them); the nav links appear once /auth/me answers, because what a
     person sees depends on what they may do. */

  function buildHeader(header) {
    const active = header.dataset.nav;
    header.textContent = "";

    const brand = FS.el("a", "brand");
    brand.href = "/dashboard";
    brand.append("FactorySemantics", FS.el("span", null, " MES"));
    header.appendChild(brand);

    const live = FS.el("div", "live");
    const dot = FS.el("span", "dot");
    dot.id = "live-dot";
    const text = FS.el("span", null, "connecting…");
    text.id = "live-text";
    live.append(dot, text);
    header.appendChild(live);

    header.appendChild(FS.el("div", "spacer"));

    const nav = FS.el("nav", "nav");
    nav.setAttribute("aria-label", "Screens");
    header.appendChild(nav);

    const identity = FS.el("div", "user");
    const name = FS.el("strong");
    name.id = "user-name";
    const role = FS.el("span", "badge");
    role.id = "user-role";
    identity.append(name, role);
    header.appendChild(identity);

    const out = FS.el("button", "ghost", "Sign out");
    out.id = "logout";
    // One logout wiring for every screen. The floor's own handler also
    // clears its poll timer; double-binding is harmless because the second
    // fetch of an already-dead session just 401s into the login screen.
    out.addEventListener("click", async () => {
      try { await FS.api("/auth/logout", { method: "POST" }); } catch (err) { /* gone anyway */ }
      window.location = "/dashboard";
    });
    header.appendChild(out);

    FS.whoami().then((who) => {
      name.textContent = who.name || who.code;
      role.textContent = who.role;
      renderNav(nav, active, who);
      FS.applyCapGates();
    }).catch(() => {
      // Signed out: api() has already redirected everything but the floor
      // screen, whose own login card is about to take over.
    });
  }

  function renderNav(nav, active, who) {
    const caps = who.capabilities || [];
    const allowed = (entry) => entry.cap === null || caps.includes(entry.cap);

    nav.textContent = "";
    let current = null;
    for (const entry of FS.NAV) {
      const visible = entry.items.filter(allowed);
      if (!visible.length) continue;
      const here = visible.some((item) => isActive(item.href, active));
      if (here) current = visible;
      // The workspace chip opens its first screen; the row beneath the
      // header lists the rest, so Station stays one click from Floor.
      const chip = link({ href: visible[0].href, label: entry.group }, null);
      if (here) { chip.classList.add("active"); chip.setAttribute("aria-current", "page"); }
      nav.appendChild(chip);
    }
    renderSubnav(current, active);
  }

  function renderSubnav(items, active) {
    let row = document.querySelector(".subnav");
    if (!items || items.length < 2) { if (row) row.remove(); return; }
    if (!row) {
      row = FS.el("nav", "subnav");
      row.setAttribute("aria-label", "Screens in this workspace");
      const header = document.querySelector("header[data-nav]");
      header.insertAdjacentElement("afterend", row);
    }
    row.textContent = "";
    for (const item of items) row.appendChild(link(item, active));
  }

  function isActive(href, active) {
    const leaf = href.split("/").pop().split("?")[0];
    return active === leaf || (href === "/dashboard" && (active === "" || active === "floor"));
  }

  function link(entry, active) {
    const a = FS.el("a", "chip", entry.label);
    a.href = entry.href;
    if (isActive(entry.href, active)) {
      a.classList.add("active");
      a.setAttribute("aria-current", "page");
    }
    return a;
  }


  /* ---------- the time window, shared ----------
     One control every page with a window reads: ?hours= wins, then what the
     viewer last chose, then eight hours - a shift. */

  const WINDOWS = [[0.25, "15 min"], [1, "1 hour"], [8, "8 hours"], [24, "24 hours"], [168, "7 days"]];
  let windowHours = null;

  FS.window = {
    hours() {
      if (windowHours !== null) return windowHours;
      const fromUrl = parseFloat(new URL(window.location).searchParams.get("hours"));
      let saved = null;
      try { saved = parseFloat(localStorage.getItem("fsmes-window")); } catch (err) { /* private window */ }
      windowHours = fromUrl > 0 ? fromUrl : (saved > 0 ? saved : 8);
      return windowHours;
    },
    set(hours) {
      windowHours = hours;
      try { localStorage.setItem("fsmes-window", String(hours)); } catch (err) { /* fine */ }
      const url = new URL(window.location);
      url.searchParams.set("hours", String(hours));
      history.replaceState(null, "", url);
    },
    bind(select, onChange) {
      const current = FS.window.hours();
      select.replaceChildren(...WINDOWS.map(([h, label]) => new Option(label, String(h), false, h === current)));
      if (!WINDOWS.some(([h]) => h === current)) select.appendChild(new Option(`${current} h`, String(current), true, true));
      select.addEventListener("change", () => { FS.window.set(parseFloat(select.value)); onChange(FS.window.hours()); });
    },
  };

  /* ---------- tabs ----------
     <div class="tabs"><button class="tab" data-tab="x"> ... and
     <section class="tab-panel" data-panel="x">. The hash is the tab, so a
     link can open a page on one, and back returns to the last. */

  let currentTab = null;

  FS.tabs = {
    current: () => currentTab,
    init(root, onChange) {
      const buttons = [...root.querySelectorAll(".tab[data-tab]")];
      const panels = [...root.querySelectorAll(".tab-panel[data-panel]")];
      const names = buttons.map((b) => b.dataset.tab);
      const show = (name) => {
        if (!names.includes(name)) name = names[0];
        currentTab = name;
        for (const b of buttons) b.setAttribute("aria-selected", String(b.dataset.tab === name));
        for (const p of panels) p.hidden = p.dataset.panel !== name;
        onChange(name);
      };
      for (const b of buttons) b.addEventListener("click", () => {
        if (location.hash !== `#${b.dataset.tab}`) location.hash = b.dataset.tab; else show(b.dataset.tab);
      });
      window.addEventListener("hashchange", () => show(location.hash.slice(1)));
      show(location.hash.slice(1));
    },
  };

  /* ---------- shadow mode ----------
     The MES is watching a real plant and can change nothing in it. Somebody
     reading a number on any screen has to be able to see that from where
     they are standing, so the bar sits above the header on every screen,
     sticks there, and has no dismiss button - it is a fact about the
     installation, not a notification.

     It is drawn from /shadow, which is public: the bar has to appear on the
     sign-in screen too, where nobody is signed in yet. */

  FS.shadowBar = function shadowBar() {
    return fetch("/shadow")
      .then((r) => (r.ok ? r.json() : null))
      .then((state) => {
        if (!state || !state.shadow) return null;
        const bar = FS.el("div", "shadow-bar");
        bar.id = "shadow-bar";
        bar.setAttribute("role", "status");
        bar.append(
          FS.el("strong", null, "Shadow mode"),
          FS.el("span", null, state.means),
          FS.el("span", "muted",
                `${state.outbound_paths_closed} of ${state.outbound_paths_total} outbound paths closed.`),
        );
        const how = FS.el("a", "shadow-how", "What this means");
        how.href = "/shadow";
        bar.appendChild(how);
        document.body.insertBefore(bar, document.body.firstChild);
        // The header sticks below the bar rather than on top of it.
        document.documentElement.style.setProperty("--shadow-top", `${bar.offsetHeight}px`);
        return state;
      })
      .catch(() => null);  // a bar that could not load must not break a screen
  };

  const header = document.querySelector("header[data-nav]");
  if (header) buildHeader(header);
  FS.shadowBar();

  window.FS = FS;
})();
