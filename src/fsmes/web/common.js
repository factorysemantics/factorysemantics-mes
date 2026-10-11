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

  /* One Configuration entry per workspace, never one per configurable thing.
     Scott, 2026-09-21, after the downtime vocabulary arrived here as a chip
     of its own: there will eventually be hundreds of configurable sections,
     and a chip each is the bar nobody can read. A section lives on its
     domain's Configuration page, which is a list read from the module
     registry - so the next one is an entry in `fsmes/modules.py` and no
     change here at all. `test_web.py` refuses the two sides drifting apart. */

  FS.NAV = [
    { group: "Floor", items: [
      { href: "/dashboard", label: "Floor", cap: null },
      { href: "/dashboard/station", label: "Station", cap: null },
      { href: "/dashboard/line", label: "Line", cap: null },
    ] },
    { group: "Orders", items: [
      { href: "/dashboard/orders", label: "Orders", cap: null },
      { href: "/dashboard/schedule", label: "Schedule", cap: null },
      /* Supply chain's Configuration entry lives here rather than in a
         group of its own. The domain is `supply_chain` and what it
         configures is the ERP link - and this product has no ERP screen:
         an order from the ERP lands in this order book, and the outbox is
         a panel on Ops. Orders is the workspace those settings are about,
         so a group holding one Configuration chip and nothing else would
         have been a group invented for a settings page. §2a of
         config-assistance.md allows exactly this: the domain gets one
         Configuration entry, in the group its screens are already in. */
      { href: "/dashboard/config/supply_chain", label: "Configuration", cap: null },
    ] },
    { group: "Quality", items: [
      { href: "/dashboard/quality", label: "Quality", cap: null },
      { href: "/dashboard/spc", label: "SPC", cap: null },
      { href: "/dashboard/gauges", label: "Gauges", cap: null },
      { href: "/dashboard/trace", label: "Trace", cap: null },
      { href: "/dashboard/coa", label: "Certificates", cap: null },
      { href: "/dashboard/config/quality", label: "Configuration", cap: null },
    ] },
    { group: "Maintenance", items: [
      { href: "/dashboard/maintenance", label: "Maintenance", cap: null },
    ] },
    { group: "Engineering", items: [
      { href: "/dashboard/machines", label: "Machines", cap: null },
      { href: "/dashboard/tags", label: "Tags", cap: null },
      { href: "/dashboard/triggers", label: "Triggers", cap: null },
      { href: "/dashboard/config/engineering", label: "Configuration", cap: null },
      { href: "/dashboard/adjustments", label: "Adjustments", cap: null },
      { href: "/dashboard/analysis", label: "Analysis", cap: null },
      { href: "/dashboard/masterdata", label: "Master data", cap: null },
    ] },
    { group: "Setup", items: [
      { href: "/dashboard/instructions", label: "Instructions", cap: null },
      { href: "/dashboard/ops", label: "Ops", cap: "audit.read" },
      { href: "/dashboard/admin", label: "Admin", cap: "users.manage" },
      { href: "/dashboard/config/administration", label: "Configuration", cap: null },
    ] },
    /* AI is its own workspace beside Setup, not a chip per brain: one entry
       with Conversations, Status and Settings inside it, the same shape §2a
       of docs/design/config-assistance.md gives a Configuration workspace.
       Scott, 2026-09-26, asked for it in those words - "really show that this
       is putting AI and Agents into this system as first class citizens" -
       after an assistant failure whose only record was a screenshot.

       Most of it is behind `audit.read`, like Ops, because the trace is the
       record of what was done in this plant and by whom. The chip itself is
       `plant.read`, which every role holds, because since 2026-09-29 the
       screen has one tab that is everybody's: `My agent`, where a person sees
       their own conversations and every analysis that named them (decision
       0039 clause 4). A reciprocity clause reachable only by typing the
       address would have promised nothing. Somebody without `audit.read`
       opens it on that tab and the tabs that are not theirs are not on the
       page - so the chip leads somewhere rather than refusing. */
    { group: "AI", items: [
      { href: "/dashboard/ai", label: "AI", cap: "plant.read" },
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
  /* The API sends two shapes: a naive stamp out of the database, which is UTC
     and must be told so, and an offset-bearing one out of `datetime.isoformat`
     (`agent.status()`'s `last_used`). Appending "Z" to the second made
     "…+00:00Z", and the cell read "Invalid Date" where a date belonged. */
  const asDate = (ts) => {
    const s = String(ts);
    const zoned = s.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(s);
    return new Date(s + (zoned ? "" : "Z"));
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

  /* ---------- what this plant's screens are set to ----------
     Three refresh clocks, four page sizes, a ceiling, a toast duration, a
     debounce and the assistant's two: every one of them was a literal in
     this file, app.js, admin.js or assist.js until 2026-09-25. They are the
     `[screens]` pack table now, and this asks the server for them once per
     page load.

     **There is no copy of any default here, on purpose.** A browser default
     beside a server default is the thing the configuration audit kept
     finding - the same number in two places, drifting - so a page awaits
     these before it uses one. `FS.settings()` is cached the way `FS.whoami()`
     is: every caller on a page shares one request. */

  let ui = null;
  let uiPromise = null;

  FS.settings = function settings() {
    if (ui) return Promise.resolve(ui);
    if (!uiPromise) {
      uiPromise = FS.api("/dashboard/ui-settings").then((page) => {
        ui = page.settings;
        return ui;
      }).catch((err) => {
        uiPromise = null;
        throw err;
      });
    }
    return uiPromise;
  };

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
    /* The message appears now and the clock that hides it is set when this
       plant's answer arrives - which on every call but the first is the same
       turn, because `FS.settings` is cached. Written this way round rather
       than with a number in front of it: a fallback here would be the second
       copy of 3500 that this key exists to delete. */
    FS.settings().then((s) => {
      clearTimeout(node._t);
      node._t = setTimeout(() => node.classList.add("hidden"), s.toast_ms);
    }).catch(() => { /* the toast stays up; a stuck message beats a lost one */ });
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
  FS.allPages = async function allPages(path, { limit, cap } = {}) {
    const plant = await FS.settings();
    if (limit === undefined) limit = plant.all_pages_limit;
    if (cap === undefined) cap = plant.all_pages_cap;
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

  /* A page names itself with `data-nav`, and what it names is its own path
     under /dashboard: "orders", or "config/engineering" for a screen one
     level down. It used to be the last segment alone, which was the same
     thing while every screen was one level deep - and would have made
     Quality's configuration page (/dashboard/config/quality) light the
     Quality screen's chip the day a second workspace got one. */
  function isActive(href, active) {
    if (href === "/dashboard") return active === "" || active === "floor";
    return active === href.split("?")[0].replace(/^\/dashboard\//, "");
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
     viewer last chose, then this plant's own default.

     The list and the default are the plant's, from /dashboard/screens - which
     is `[process] report_windows` and `default_report_hours`, editable on
     Engineering's Configuration page. This file held both as literals until
     2026-09-25, which meant a plant that offered a twelve-hour window on the
     server offered the product's five in the browser and opened on eight
     hours whatever it had said.

     The shipped five stay here as the answer before /dashboard/screens has
     come back, and they are exactly what the server ships, because a picker
     that renders empty for a moment is worse than one that renders the
     product's list and is corrected. */

  const SHIPPED_WINDOWS = [0.25, 1, 8, 24, 168];
  let windows = SHIPPED_WINDOWS.slice();
  let defaultHours = 8;
  let windowHours = null;
  let chosen = false;   // whether this viewer picked the window themselves

  /* A window's label from its own length, so the plant's list needs no words
     beside it. It reproduces the five labels this file used to hold:
     0.25 → "15 min", 1 → "1 hour", 8 → "8 hours", 24 → "24 hours",
     168 → "7 days". */
  function windowLabel(h) {
    if (h < 1) return `${Math.round(h * 60)} min`;
    if (h === 1) return "1 hour";
    if (h < 48) return `${FS.fmt.qty(h)} hours`;
    return `${FS.fmt.qty(h / 24)} days`;
  }

  FS.window = {
    hours() {
      if (windowHours !== null) return windowHours;
      const fromUrl = parseFloat(new URL(window.location).searchParams.get("hours"));
      let saved = null;
      try { saved = parseFloat(localStorage.getItem("fsmes-window")); } catch (err) { /* private window */ }
      chosen = fromUrl > 0 || saved > 0;
      windowHours = fromUrl > 0 ? fromUrl : (saved > 0 ? saved : defaultHours);
      return windowHours;
    },
    /* This plant's list and default, fetched once per page. Awaited by `bind`
       before it builds the control, and safe to call again. */
    async ready() {
      const said = await FS.screens;
      if (!said) return;
      if (Array.isArray(said.report_windows) && said.report_windows.length) {
        windows = said.report_windows.slice();
      }
      if (said.default_report_hours > 0) defaultHours = said.default_report_hours;
      /* A viewer who has chosen nothing follows the plant. One who has picked
         a window, by URL or last time, keeps it: a plant changing its default
         is not a reason to move somebody off the window they are reading. */
      if (!chosen) windowHours = null;
    },
    set(hours) {
      windowHours = hours;
      chosen = true;
      try { localStorage.setItem("fsmes-window", String(hours)); } catch (err) { /* fine */ }
      const url = new URL(window.location);
      url.searchParams.set("hours", String(hours));
      history.replaceState(null, "", url);
    },
    async bind(select, onChange) {
      await FS.window.ready();
      const current = FS.window.hours();
      select.replaceChildren(...windows.map((h) => new Option(windowLabel(h), String(h), false, h === current)));
      if (!windows.some((h) => h === current)) select.appendChild(new Option(`${current} h`, String(current), true, true));
      select.addEventListener("change", () => { FS.window.set(parseFloat(select.value)); onChange(FS.window.hours()); });
    },
  };

  /* ---------- a window that is not "now" ----------
     `FS.window` above is the page head's trailing span: 8 hours back from
     this second, and every report route on the product takes it. It is the
     right control for "how is the line doing", and the wrong one for "what
     happened at twenty past six", because a shift that finished at 2 p.m.
     cannot be reached from it at all.

     `FS.span` is the richer window, and it is deliberately ONE object rather
     than one per chart: the trend graph and the state timeline sit on the
     same machine page, a reader who sets "last shift" on one means it on the
     other, and two controls that disagree would be the page telling two
     stories about the same shift. Any number of controls may be bound; they
     all read and write this state and all of them redraw.

     It only ever produces a QUERY STRING, never a start and an end of its
     own: which seconds "this shift" means is the plant's answer, from its own
     shift calendar, and a browser that worked it out locally would be a
     second definition of a shift living on a screen. */

  const SPAN_SHIFTS = [
    { value: "shift:current", label: "This shift", words: "this shift" },
    { value: "shift:previous", label: "Last shift", words: "last shift" },
  ];
  let span = null;                 /* {kind, hours, shift, start, end} */
  const spanBound = [];            /* every control drawn, so all redraw */

  /* The inputs are `datetime-local`, so what a person types is their own
     device's clock. It goes to the API as the instant that was, and comes
     back worded in the plant's zone, which is the zone every other time on
     these screens is in. */
  const spanLocalToIso = (local) => (local ? new Date(local).toISOString() : null);
  const spanIsoToLocal = (iso) => {
    if (!iso) return "";
    const at = new Date(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    const pad = (n) => String(n).padStart(2, "0");
    return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`
      + `T${pad(at.getHours())}:${pad(at.getMinutes())}`;
  };

  function spanState() {
    if (span) return span;
    const asked = new URL(window.location).searchParams;
    const start = asked.get("start");
    const end = asked.get("end");
    const shift = asked.get("shift");
    if (start && end) span = { kind: "between", start, end, shift: null, hours: null };
    else if (shift === "current" || shift === "previous") {
      span = { kind: "shift", shift, start: null, end: null, hours: null };
    } else span = { kind: "hours", hours: FS.window.hours(), shift: null, start: null, end: null };
    return span;
  }

  /* The URL carries the window, so a link to "what happened at 6:20" opens on
     the window it was read in. Replaced, not pushed: back is for tabs. */
  function spanToUrl() {
    const state = spanState();
    const url = new URL(window.location);
    for (const key of ["start", "end", "shift"]) url.searchParams.delete(key);
    if (state.kind === "between") {
      url.searchParams.set("start", state.start);
      url.searchParams.set("end", state.end);
    } else if (state.kind === "shift") url.searchParams.set("shift", state.shift);
    history.replaceState(null, "", url);
  }

  FS.span = {
    /* What this window is, for a caller that needs to branch on it. */
    kind: () => spanState().kind,
    /* The query string the analysis routes read. One place, so the two charts
       on the machine page cannot drift into asking for different windows. */
    query() {
      const state = spanState();
      if (state.kind === "between") {
        return `start=${encodeURIComponent(state.start)}&end=${encodeURIComponent(state.end)}`;
      }
      if (state.kind === "shift") return `shift=${encodeURIComponent(state.shift)}`;
      return `hours=${state.hours}`;
    },
    /* For the routes that take only `hours=` — the OEE read and the alarm
       list. A window with two ends still has a width, and a shift's width is
       the plant's shift length, so the figure beside a chosen window is of
       about the right amount of time even where the route cannot be told
       which stretch of the clock it was. */
    hours() {
      const state = spanState();
      if (state.kind === "between") {
        const width = (new Date(state.end) - new Date(state.start)) / 3600000;
        return width > 0 ? Math.round(width * 100) / 100 : FS.window.hours();
      }
      return state.kind === "hours" ? state.hours : FS.window.hours();
    },
    /* The window in words, from what the reader chose. `said()` below is the
       better sentence wherever an envelope is to hand, because the envelope
       knows the seconds the plant settled on and this does not. */
    words() {
      const state = spanState();
      if (state.kind === "between") {
        return `from ${FS.fmt.stamp(state.start)} to ${FS.fmt.stamp(state.end)}`;
      }
      if (state.kind === "shift") {
        return (SPAN_SHIFTS.find((s) => s.value === `shift:${state.shift}`) || {}).words
          || state.shift;
      }
      return `the last ${windowLabel(state.hours)}`;
    },
    /* THE WINDOW A CHART IS ACTUALLY DRAWN ON, worded from the chart's own
       envelope. The envelope says what KIND of window the plant settled on
       (`named`: a shift, a trailing span, a start and an end) and the two
       instants it came out as; this turns that into the sentence the chart
       and its panel both print, so one window is described one way in both
       places. Anything a read narrowed — a window clamped forward to the
       first reading there has ever been — is in these instants and therefore
       in this sentence. */
    said(window) {
      if (!window || !window.start || !window.end) return "a window nobody recorded";
      const from = FS.fmt.stamp(window.start);
      const to = FS.fmt.stamp(window.end);
      const named = window.named || null;
      const zone = FS.fmt.zone();
      const inZone = zone ? `, ${zone} time` : "";
      if (named === "shift" || window.shift) {
        /* A shift in an envelope is a RECORD - code, name, day, the two
           instants - and not a string. Printing it straight put "[object
           Object]" under the chart (2026-10-11). Its name is what a person
           on the floor calls it; its code is the fallback, and "the shift" is
           what is left when the read named none. */
        const shift = window.shift;
        const named_shift = (shift && typeof shift === "object")
          ? (shift.name || shift.code || null)
          : (shift || null);
        return `${named_shift || "the shift"}, ${from} to ${to}${inZone}`;
      }
      if (named === "between") return `${from} to ${to}${inZone}`;
      const width = (new Date(window.end) - new Date(window.start)) / 3600000;
      return `the last ${windowLabel(Math.round(width * 100) / 100)}, `
        + `${from} to ${to}${inZone}`;
    },
    set(next) {
      span = { kind: "hours", hours: FS.window.hours(), shift: null, start: null, end: null, ...next };
      spanToUrl();
      for (const control of spanBound) control.redraw();
    },
    /* Draw the control into `host`. Callable from more than one place on one
       page: every control drawn shows the same window, and a change to any of
       them redraws all of them and tells every caller. */
    async bind(host, onChange) {
      await FS.window.ready();
      const state = spanState();
      host.replaceChildren();
      host.classList.add("span");

      const pick = FS.el("label", "pick");
      pick.append(document.createTextNode("Window "));
      const select = FS.el("select");
      select.dataset.span = "kind";
      pick.append(select);

      const between = FS.el("span", "span-between");
      const from = Object.assign(document.createElement("input"),
                                 { type: "datetime-local" });
      from.dataset.span = "start";
      const to = Object.assign(document.createElement("input"),
                               { type: "datetime-local" });
      to.dataset.span = "end";
      const apply = FS.el("button", "small", "Apply");
      apply.type = "button";
      apply.dataset.span = "apply";
      const fromLabel = FS.el("label", "pick");
      fromLabel.append(document.createTextNode("From "), from);
      const toLabel = FS.el("label", "pick");
      toLabel.append(document.createTextNode("to "), to);
      between.append(fromLabel, toLabel, apply);

      const says = FS.el("p", "span-says muted");
      says.dataset.span = "says";
      host.append(pick, between, says);

      const redraw = () => {
        const now = spanState();
        const value = now.kind === "between" ? "between"
          : (now.kind === "shift" ? `shift:${now.shift}` : `h:${now.hours}`);
        select.replaceChildren(
          ...windows.map((h) => new Option(windowLabel(h), `h:${h}`)),
          ...SPAN_SHIFTS.map((s) => new Option(s.label, s.value)),
          new Option("A start and an end…", "between"));
        if (![...select.options].some((o) => o.value === value)) {
          select.append(new Option(FS.span.words(), value));
        }
        select.value = value;
        between.hidden = now.kind !== "between";
        if (now.kind === "between") {
          from.value = spanIsoToLocal(now.start);
          to.value = spanIsoToLocal(now.end);
        }
        says.textContent = `Showing ${FS.span.words()}.`;
      };

      const changed = () => { redraw(); onChange(); };
      select.addEventListener("change", () => {
        const value = select.value;
        if (value === "between") {
          /* Opening the two boxes is not a window yet: nothing is reloaded
             until Apply, because a half-typed start is not a question. */
          const state2 = spanState();
          const end = state2.end || new Date().toISOString();
          const start = state2.start
            || new Date(new Date(end) - FS.span.hours() * 3600000).toISOString();
          span = { kind: "between", start, end, shift: null, hours: null };
          redraw();
          from.focus();
          return;
        }
        if (value.startsWith("shift:")) FS.span.set({ kind: "shift", shift: value.slice(6) });
        else {
          const hours = parseFloat(value.slice(2));
          FS.window.set(hours);
          FS.span.set({ kind: "hours", hours });
        }
        onChange();
      });
      apply.addEventListener("click", () => {
        const start = spanLocalToIso(from.value);
        const end = spanLocalToIso(to.value);
        if (!start || !end) {
          says.textContent = "A window has two ends: give a start and an end.";
          return;
        }
        if (new Date(end) <= new Date(start)) {
          says.textContent = "The end is before the start — swap them.";
          return;
        }
        FS.span.set({ kind: "between", start, end });
        changed();
      });

      const control = { host, redraw };
      spanBound.push(control);
      redraw();
      return control;
    },
  };


  /* ---------- the panel beside a chart ----------
     Click a point, and a column beside the chart fills with every record this
     MES holds about that moment. #143 built the first one on the control
     chart; the trend graph and the state timeline are the second and third,
     and they sit on ONE page, so the primitives live here rather than three
     times over. A block, a heading, two columns of facts, a table that states
     its own total, a fold that remembers itself, and the line that says how
     much of the block's own window anybody watched.

     Nothing here knows what a reading is. What goes in the blocks is each
     screen's own business; that these panels look and behave like one panel
     is this file's. */

  FS.panel = {
    /* Rule 2, per block. Four different sentences for four different facts:
       a share, a share nobody could compute, a block that is a list of
       records and has no rate to state, and a block carrying no coverage
       field at all. A reader is entitled to tell them apart. */
    watched(block) {
      if (!block) return null;
      if (block.coverage === "absent") {
        return block.coverage_note || "No coverage figure: this is a list of records.";
      }
      if (!("coverage" in block)) return null;
      if (block.coverage === null || block.coverage === undefined) {
        return block.coverage_note || "How much of this window was watched is unknown.";
      }
      return `Watched ${FS.fmt.pct(block.coverage)} of this window`
           + (block.coverage_note ? ` — ${block.coverage_note}` : "");
    },
    /* One block: a heading, then whatever the screen puts in it. The watched
       line is added by `closeWith` at the end so it is always last, whatever
       went in between. */
    block(title) {
      const section = FS.el("section", "point-block");
      section.append(FS.el("h3", null, title));
      return section;
    },
    closeWith(section, block) {
      const said = FS.panel.watched(block);
      if (said) section.append(FS.el("p", "point-watched", said));
      return section;
    },
    /* Label and value in two columns. A value nobody recorded prints "—" and
       its label stays: a row that vanished would read as a question nobody
       asked (unknown is never 0 and never absent). */
    facts(rows) {
      const list = FS.el("dl", "point-facts");
      for (const [label, value, mono] of rows) {
        list.append(FS.el("dt", null, label));
        const dd = FS.el("dd", mono ? "mono" : null);
        if (value instanceof Node) dd.append(value);
        else {
          dd.textContent = value === null || value === undefined || value === ""
            ? "—" : String(value);
        }
        list.append(dd);
      }
      return list;
    },
    /* A table, stating its own total the way every list in this product does
       — or the sentence that says there was nothing, which is a different
       fact from a table with no rows in it. */
    table(headings, rows, empty) {
      if (!rows.length) return FS.el("p", "muted small", empty);
      const wrap = FS.el("div", "table-wrap");
      const table = FS.el("table");
      const head = FS.el("thead");
      const hr = FS.el("tr");
      for (const h of headings) hr.append(FS.el("th", null, h));
      head.append(hr);
      table.append(head);
      const body = FS.el("tbody");
      for (const row of rows) {
        const tr = FS.el("tr");
        for (const cell of row) {
          const td = FS.el("td");
          if (cell instanceof Node) td.append(cell);
          else td.textContent = cell === null || cell === undefined ? "—" : String(cell);
          tr.append(td);
        }
        body.append(tr);
      }
      table.append(body);
      wrap.append(table);
      return wrap;
    },
    /* A built block behind a summary that states what is inside it: the
       block's own heading becomes the label and the caller's sentence sits
       beside it, so nothing is hidden without saying so.

       The open/closed state is per block and per reader, in `localStorage`:
       somebody who opens the tags on every point should not have to open them
       again on the next one. Wrapped, because a browser with storage switched
       off is a browser this panel still has to work in. `store` namespaces
       the key, so the control chart's folds and the trend graph's are not one
       set of folds wearing two hats. */
    foldable(section, key, summary, { open, store = "panel" } = {}) {
      const name = `fsmes-${store}-fold-${key}`;
      let stored = null;
      try {
        const said = localStorage.getItem(name);
        if (said === "open") stored = false;
        if (said === "closed") stored = true;
      } catch (e) { /* storage off: the caller's default stands */ }
      const box = FS.el("details", "point-block point-fold");
      box.dataset.fold = key;
      box.open = stored === null ? Boolean(open) : !stored;
      const head = FS.el("summary");
      const title = section.querySelector("h3");
      head.append(FS.el("span", "point-fold-title", title ? title.textContent : key));
      head.append(FS.el("span", "point-fold-says", summary));
      if (title) title.remove();
      box.append(head, ...[...section.childNodes]);
      box.addEventListener("toggle", () => {
        try { localStorage.setItem(name, box.open ? "open" : "closed"); }
        catch (e) { /* nothing to remember it in, which breaks nothing */ }
      });
      return box;
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

  /* The numbers this plant's own screens are drawn with, fetched once per page
     and shared - the window list, the default window, the working week a new
     shift starts from, the board's horizon. Not public: it needs a session,
     which every screen that reads it already has.

     It resolves to null rather than rejecting on a plant that answers 403 or
     404, so a screen behind a sign-in wall renders the product's own defaults
     instead of failing to render. Every reader treats null as *nothing said*. */
  FS.screens = fetch("/dashboard/screens", { headers: { Accept: "application/json" } })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);

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
