/* The design conversation, on the screen it is about.

   The reason this exists: noticing something wrong on a screen, walking to a
   terminal, and describing it from memory loses most of what you noticed. So
   the conversation happens here, and what you are looking at goes with the
   question - the rendered text, the data the page fetched, the filters you
   set. "How would a supervisor use this?" becomes answerable because both
   sides can see the same screen.

   Off unless the server says it is on. */

(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  const SCREENS = {
    "/dashboard": "the floor dashboard",
    "/dashboard/orders": "the work orders screen",
    "/dashboard/quality": "the quality screen",
    "/dashboard/instructions": "the work instructions screen",
    "/dashboard/analysis": "the shift analysis screen",
    "/dashboard/ops": "the operations screen",
    "/dashboard/admin": "the administration screen",
    "/dashboard/line": "the 3D line view",
  };

  let status = null;
  let conversation = null;
  let panel = null;
  let busy = false;

  async function api(path, options = {}) {
    const r = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    const data = await r.json().catch(() => null);
    if (!r.ok) throw new Error((data && data.detail) || `${r.status}`);
    return data;
  }

  /* ---------- what am I looking at ---------- */

  function visibleText() {
    // The rendered text of the screen: the closest thing to what he is
    // actually seeing, and far more faithful than any description of it.
    const main = document.querySelector("main") || document.body;
    return (main.innerText || "").replace(/\n{3,}/g, "\n\n").slice(0, 24000);
  }

  function currentFilters() {
    // Whatever the screen is filtered to. A critique of a list is meaningless
    // without knowing which slice of it is on screen.
    const filters = {};
    for (const control of document.querySelectorAll("main select, main input")) {
      const key = control.id || control.name;
      if (!key || !control.value) continue;
      if (control.type === "password") continue;
      filters[key] = control.value;
    }
    const active = document.querySelector(".tab.active, .link.active");
    if (active) filters._tab = active.textContent.trim();
    return filters;
  }

  function pageData() {
    // Screens stash their last payload here so the conversation can see the
    // numbers behind the pixels, not just the pixels.
    try {
      return window.__fsmesPageData
        ? JSON.stringify(window.__fsmesPageData).slice(0, 24000) : "";
    } catch (err) {
      return "";
    }
  }

  /* ---------- panel ---------- */

  function build() {
    const p = el("div", "design-panel");

    const head = el("div", "design-head");
    head.appendChild(el("strong", null, "Design"));
    head.appendChild(el("span", "design-model", status.model));
    head.appendChild(el("span", "spacer"));
    const fresh = el("button", null, "New");
    fresh.title = "Start a separate conversation";
    fresh.addEventListener("click", () => {
      conversation = null;
      $("#design-log").textContent = "";
      note(`New conversation about ${SCREENS[location.pathname] || location.pathname}.`);
    });
    const close = el("button", null, "Close");
    close.addEventListener("click", hide);
    head.append(fresh, close);
    p.appendChild(head);

    const log = el("div", "design-log");
    log.id = "design-log";
    p.appendChild(log);

    const form = el("form", "design-ask");
    const box = el("textarea");
    box.id = "design-input";
    box.rows = 2;
    box.placeholder = "What is wrong with this screen?";
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) form.requestSubmit();
    });
    const send = el("button", null, "Send");
    form.append(box, send);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const message = box.value.trim();
      if (!message || busy) return;
      box.value = "";
      await ask(message);
    });
    p.appendChild(form);

    const foot = el("div", "design-foot");
    foot.appendChild(el("span", null,
      "It can see this screen's text, data and source. ⌘/Ctrl+Enter to send."));
    p.appendChild(foot);
    return p;
  }

  function say(text, cls) {
    const log = $("#design-log");
    const line = el("div", `design-msg ${cls}`, text);
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
    return line;
  }

  function note(text) {
    return say(text, "note");
  }

  async function ask(message) {
    busy = true;
    say(message, "you");
    const pending = say("thinking…", "thinking");
    try {
      const out = await api("/design/chat", {
        method: "POST",
        body: {
          message,
          route: location.pathname,
          screen: SCREENS[location.pathname] || location.pathname,
          visible: visibleText(),
          data: pageData(),
          filters: currentFilters(),
          conversation,
        },
      });
      pending.remove();
      if (out.error) { note(out.error); return; }
      conversation = out.conversation;
      say(out.say, "bot");
      if (out.hint) note(out.hint);
      $(".design-model").textContent = out.model;
    } catch (err) {
      pending.remove();
      note(`Could not reach the design surface: ${err.message}`);
    } finally {
      busy = false;
    }
  }

  function show() {
    if (!panel) { panel = build(); document.body.appendChild(panel); }
    panel.style.display = "flex";
    $(".design-launch").style.display = "none";
    if (!$("#design-log").childElementCount) {
      note(status.claude
        ? `Looking at ${SCREENS[location.pathname] || location.pathname}. `
          + "Ask me anything about it — I can see what you see."
        : status.note);
    }
    $("#design-input").focus();
  }

  function hide() {
    if (panel) panel.style.display = "none";
    const launch = $(".design-launch");
    if (launch) launch.style.display = "flex";
  }

  async function boot() {
    try {
      status = await api("/design/status");
    } catch (err) {
      return;   // not signed in, or the surface is not built
    }
    if (!status.enabled) return;

    const launch = el("button", "design-launch", "Design");
    launch.setAttribute("aria-label", "Discuss this screen");
    launch.addEventListener("click", show);
    document.body.appendChild(launch);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
