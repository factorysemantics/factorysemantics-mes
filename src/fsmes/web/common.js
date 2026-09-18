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

  /* ---------- breadcrumbs, one rule for every screen ----------
     A machine page and the Machines tree both draw the same trail -
     enterprise > site > area > line > machine - and each used to pick its
     own link for each level, so which ancestors were clickable, and where
     they went, depended on which screen you were standing on. A person
     reading that trail on a large plant found one level of five clickable
     and said so. The rule lives here now, once:

     - a work center is a line, and opens the Line view;
     - every other ancestor opens the Machines tree scoped to that node,
       which is the screen that shows a site, an area or an enterprise;
     - the node you are already on is text, not a link to itself, and says
       so to a screen reader.

     `path` is the ancestor chain the API hands over, outermost first -
     `/equipment/{code}`'s `path`, or the ancestors of a scoped tree. */

  FS.crumbs = function crumbs(container, path, current) {
    container.replaceChildren();
    for (const node of path) {
      const a = node.level === "work_center"
        ? FS.link("line", node.code, null, node.name || node.code)
        : FS.link("equipment", node.code, null, node.name || node.code);
      if (node.level) a.title = `${String(node.level).replace("_", " ")} ${node.code}`;
      container.append(a, FS.el("span", "sep", "\u203a"));
    }
    const here = FS.el("span", "mono", current);
    here.setAttribute("aria-current", "page");
    container.append(here);
    return container;
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

  /* Which clock every screen reads. The MES stores naive UTC and the plant
     works in its own zone, so a browser left to its own devices showed a
     Chicago line's ten o'clock break at four in the afternoon to anyone
     opening the screen from Europe. /health states the plant's zone; until
     it answers, the browser's zone stands, because a dash where a time
     should be is worse than a time an engineer can re-read a second later.
     A zone this browser does not know is ignored rather than thrown. */
  let plantZone = null;

  FS.setZone = function setZone(zone) {
    if (!zone) return null;
    try {
      new Date().toLocaleString(undefined, { timeZone: zone });
      plantZone = zone;
    } catch (e) { plantZone = null; }
    return plantZone;
  };

  const inPlantZone = (extra) => (plantZone ? { ...extra, timeZone: plantZone } : extra);
  const asDate = (ts) => {
    const s = String(ts);
    return new Date(s + (s.endsWith("Z") ? "" : "Z"));
  };

  FS.fmt = {
    qty: (v) => {
      if (v === null || v === undefined) return "—";
      const n = Math.round(Number(v) * 100) / 100;
      return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    },
    pct: (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%"),
    clock: (ts) => (ts ? asDate(ts).toLocaleTimeString(undefined, inPlantZone()) : ""),
    stamp: (ts) => (ts ? asDate(ts).toLocaleString(undefined, inPlantZone()) : "—"),
    /* A date as the plant would write it - a due date, a shift day. */
    day: (ts) => (ts ? asDate(ts).toLocaleDateString(undefined, inPlantZone()) : "—"),
    zone: () => plantZone,
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

  /* A filter bar holds one line (docs/design/STYLE.md): a long tail of
     controls goes behind a toggle. Written once here because the Orders
     screen and the Quality screen were about to have one each. */
  FS.popover = function popover(toggleSel, popSel) {
    const toggle = document.querySelector(toggleSel);
    const pop = document.querySelector(popSel);
    if (!toggle || !pop) return;
    toggle.addEventListener("click", () => {
      const open = pop.classList.toggle("hidden");
      toggle.setAttribute("aria-expanded", String(!open));
    });
    document.addEventListener("click", (event) => {
      if (!pop.contains(event.target) && event.target !== toggle) {
        pop.classList.add("hidden");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  };

  /* Every row of a paged list, page by page, up to a stated ceiling.

     For the handful of places that genuinely need the whole of a bounded
     list - a picker that groups every characteristic by its material - and
     never for a list that grows with time. It returns `complete`, so a
     screen that hit the ceiling says so instead of presenting a truncated
     list as the plant. */
  FS.allPages = async function allPages(path, { limit = 500, cap = 2000 } = {}) {
    const join = path.includes("?") ? "&" : "?";
    let items = [];
    let total = 0;
    for (let offset = 0; offset < cap; offset += limit) {
      const page = await FS.api(`${path}${join}limit=${limit}&offset=${offset}`);
      total = page.total || 0;
      items = items.concat(page.items || []);
      if (!page.has_more || !(page.items || []).length) break;
    }
    return { items, total, complete: items.length >= total };
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

    /* Which plant this is. A person with two plants open in two tabs, or a
       console operator who followed a link, has to be able to tell them
       apart without reading the address bar. Filled in from /health, which
       is public, so it appears on the sign-in screen too. */
    const where = FS.el("div", "plant");
    where.id = "plant-name";
    header.appendChild(where);

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


  /* ---------- the connection, shared ----------
     Whether the MES can still see a machine is a second fact beside what the
     machine is doing, and every screen that shows a state has to show it
     (decision 0030). One helper, so the five screens cannot drift apart on
     what "disconnected" looks like.

     `unknown` here means nothing has ever reported a connection for this
     machine - fed by hand, or over MQTT - and is deliberately silent: a badge
     on every machine on a plant with no OPC agent is noise, not information.
     Only a connection that was there and is gone gets a badge. */

  FS.connection = {
    lost(m) {
      const c = m && m.connection;
      return c && c.state === "disconnected" ? c : null;
    },
    /* The class the card and the pill take: the machine's state, unless the
       MES cannot see it, in which case there is no honest state to show. */
    stateClass(m) {
      return FS.connection.lost(m) ? "disconnected" : (m.state || "unknown");
    },
    stateText(m) {
      return FS.connection.lost(m) ? "disconnected" : (m.state || "unknown");
    },
    /* The strip that says since when, and why. Null when the link is fine. */
    badge(m) {
      const c = FS.connection.lost(m);
      if (!c) return null;
      const node = FS.el("div", "disconnect small");
      const since = c.since ? FS.fmt.clock(c.since) : "an unknown time";
      node.append(`⚡ no connection since ${since}`);
      if (c.reason) node.append(FS.el("span", "muted", ` — ${c.reason}`));
      // The whole point: nobody knows what the machine did in this gap, so
      // nothing here guesses.
      node.title = "Nothing was watching this machine. Its time in this gap "
                 + "counts as unknown, not as running, idle or down.";
      return node;
    },
  };

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

  /* ---------- a replayed plant ----------
     The plant is playing a recording back faster than it was recorded, so
     one second on this screen is several seconds of the line. Every rate the
     MES reports is computed on the line's clock and every duration beside it
     is this screen's, and a person reading either has to be able to see that
     from where they are standing. Same bar, same argument and same absence of
     a dismiss button as shadow mode: it is a fact about how this plant was
     started, not a notification.

     Drawn from `/health`, which is public, and after the shadow bar so the
     two stack rather than cover each other. A plant whose clock is the
     line's - every real plant - has no `replay` and gets no bar. */

  FS.replayBar = function replayBar() {
    return fetch("/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((state) => {
        if (!state || !state.replay) return null;
        const bar = FS.el("div", "replay-bar");
        bar.id = "replay-bar";
        bar.setAttribute("role", "status");
        bar.append(
          FS.el("strong", null, `Replay ${state.replay.factor}\u00d7`),
          FS.el("span", null, state.replay.means),
          FS.el("span", "muted", state.replay.standing_plant),
        );
        const shadow = document.getElementById("shadow-bar");
        const above = shadow ? shadow.offsetHeight : 0;
        if (shadow) shadow.insertAdjacentElement("afterend", bar);
        else document.body.insertBefore(bar, document.body.firstChild);
        bar.style.top = `${above}px`;
        // The header sticks below both bars, however many of them there are.
        document.documentElement.style.setProperty(
          "--shadow-top", `${above + bar.offsetHeight}px`);
        return state.replay;
      })
      .catch(() => null);  // a bar that could not load must not break a screen
  };

  const header = document.querySelector("header[data-nav]");
  if (header) buildHeader(header);
  // In order: the replay bar measures the shadow bar to sit under it.
  FS.shadowBar().then(() => FS.replayBar());

  /* The plant's identity, fetched once per page and shared. Public, like
     /shadow, and for the same reason: the sign-in screen needs it too.
     After buildHeader, which is what puts #plant-name on the page. */
  FS.plant = fetch("/health")
    .then((r) => (r.ok ? r.json() : null))
    .then((who) => {
      if (!who) return null;
      FS.setZone(who.timezone);
      const slot = FS.$("#plant-name");
      if (slot) {
        slot.textContent = "";
        slot.appendChild(FS.el("strong", null, who.plant));
        if (who.timezone) {
          const zone = FS.el("span", "muted small", who.timezone);
          // A zone nobody chose is a guess about the plant, and a reader is
          // told it was one.
          if (who.timezone_defaulted) {
            zone.textContent += " (defaulted)";
            zone.title = "No MES_PLANT_TIMEZONE is set, so times are shown in "
              + "this server's own zone.";
          }
          slot.appendChild(zone);
        } else if (who.timezone_defaulted) {
          const zone = FS.el("span", "muted small", "zone not set");
          zone.title = "No MES_PLANT_TIMEZONE is set and this server's zone has "
            + "no name; times are shown in your browser's zone.";
          slot.appendChild(zone);
        }
      }
      return who;
    })
    .catch(() => null);  // an identity that could not load must not break a screen

  window.FS = FS;
})();
