/* The floor assistant.

   Answering a question is the least interesting thing it does. Guide mode is
   the point: the assistant highlights the actual control on the actual screen
   and walks the person through the task, so they learn where things are
   instead of watching a bot do it for them.

   With a cloud brain configured the panel is also the agent's front door: the
   person says what they want done, the model reads what it needs, and every
   change comes back as a proposal card - "Do it" runs it on their behalf,
   "Show me" fills in the real form and walks them to the button, "Not that"
   says no. Nothing changes the plant without one of those clicks.

   A walk can span screens. Crossing one means the walk has to survive a page
   load, so the position is kept in sessionStorage and picked up on the other
   side. */

(function () {
  const KEY = "fsmes-guide";
  const SESSION_KEY = "fsmes-agent-session";
  const LOG_KEY = "fsmes-assist-log";     // what the panel showed, so a page change does not lose it
  const OPEN_KEY = "fsmes-assist-open";   // whether the panel was open when the page changed
  const $ = (s, root = document) => root.querySelector(s);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  let me = null;
  let guides = [];
  let agent = { available: false };
  let panel = null;
  let walk = null;      // { guide, index }  guide = { id?, title, steps }
  let ring = null;
  let coach = null;
  let busy = false;
  const headButtons = [];  // added by other scripts (the recorder) before the panel exists
  let entries = [];       // the conversation as rendered, kept in sessionStorage
  let replaying = false;  // re-rendering saved entries: do not record them again

  function loadEntries() {
    try { entries = JSON.parse(sessionStorage.getItem(LOG_KEY) || "[]"); } catch (e) { entries = []; }
    if (!Array.isArray(entries)) entries = [];
  }

  function saveEntries() {
    try { sessionStorage.setItem(LOG_KEY, JSON.stringify(entries.slice(-60))); } catch (e) { /* private mode */ }
  }

  function remember(entry) {
    if (replaying) return entry;
    entries.push(entry);
    saveEntries();
    return entry;
  }

  async function api(path, options = {}) {
    const r = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }

  /* ---------- the panel ---------- */

  function buildPanel() {
    const p = el("div", "assist-panel");

    const head = el("div", "assist-head");
    head.appendChild(el("strong", null, "Assistant"));
    const brain = el("span", "assist-brain");
    brain.id = "assist-brain";
    head.appendChild(brain);
    head.appendChild(el("span", "spacer"));
    for (const hb of headButtons) {
      const b = el("button", null, hb.label);
      b.title = hb.title || "";
      b.addEventListener("click", hb.handler);
      head.appendChild(b);
    }
    const fresh = el("button", null, "New chat");
    fresh.title = "Forget this conversation and start another";
    fresh.addEventListener("click", newChat);
    head.appendChild(fresh);
    const close = el("button", null, "Close");
    close.addEventListener("click", () => hidePanel(false));
    head.appendChild(close);
    p.appendChild(head);

    const log = el("div", "assist-log");
    log.id = "assist-log";
    p.appendChild(log);

    const suggest = el("div", "assist-suggest");
    suggest.id = "assist-suggest";
    p.appendChild(suggest);

    const ask = el("form", "assist-ask");
    const input = el("input");
    input.placeholder = agent.available ? "Ask, or say what to do…" : "Ask, or say how do I…";
    input.id = "assist-input";
    input.autocomplete = "off";
    const send = el("button", null, "Ask");
    ask.append(input, send);
    ask.addEventListener("submit", async (e) => {
      e.preventDefault();
      const question = input.value.trim();
      if (!question || busy) return;
      input.value = "";
      await askQuestion(question);
    });
    p.appendChild(ask);
    return p;
  }

  function say(text, cls) {
    const log = $("#assist-log");
    const line = el("div", `assist-msg ${cls}`, text);
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
    if (cls === "you" || cls === "bot") remember({ t: cls, text });
    return line;
  }

  function renderBrain() {
    const b = $("#assist-brain");
    if (!b) return;
    if (agent.available) {
      b.textContent = `${agent.model}`;
      b.title = `Cloud brain on. $${(agent.spend_usd || 0).toFixed(2)} of $${agent.cap_usd} this month.`;
      b.className = "assist-brain on";
    } else {
      b.textContent = "local";
      b.title = agent.reason || "The cloud brain is off; answers come from the local model.";
      b.className = "assist-brain";
    }
  }

  let suggestions = null;   // fetched once per page, for this screen

  async function renderSuggestions() {
    const box = $("#assist-suggest");
    if (!box) return;
    box.textContent = "";
    if (!agent.available) {
      // Only tasks this person is actually allowed to do - the server already
      // filtered them by capability.
      for (const g of guides.slice(0, 4)) {
        const b = el("button", null, g.title);
        b.addEventListener("click", () => startGuideById(g.id));
        box.appendChild(b);
      }
      return;
    }
    // With the agent on, the chips are things to *say*, for this screen, in
    // this plant's own names. They fill the box rather than send, so the
    // numbers can be changed first - and they leave once the talking starts.
    if (suggestions === null) {
      try {
        const out = await api(`/assist/suggestions?screen=${encodeURIComponent(window.location.pathname)}`);
        suggestions = out.suggestions || [];
      } catch (err) {
        suggestions = [];
      }
    }
    for (const text of suggestions) {
      const b = el("button", null, text);
      b.addEventListener("click", () => {
        const input = $("#assist-input");
        input.value = text;
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      });
      box.appendChild(b);
    }
  }

  function clearSuggestions() {
    const box = $("#assist-suggest");
    if (box) box.textContent = "";
  }

  function offerGuide(guide) {
    remember({ t: "guide", guide });
    const start = el("button", "assist-launch", `Show me — ${guide.title}`);
    start.style.position = "static";
    start.style.marginTop = "4px";
    start.addEventListener("click", () => beginWalk(guide));
    $("#assist-log").appendChild(start);
  }

  async function askQuestion(question) {
    say(question, "you");
    clearSuggestions();
    if (agent.available) { await agentSend(question); return; }
    const pending = say("thinking…", "thinking");
    try {
      const out = await api("/assist/ask", {
        method: "POST",
        body: { question, screen: window.location.pathname },
      });
      pending.remove();
      say(out.say, "bot");
      if (out.kind === "guide" && out.guide) offerGuide(out.guide);
    } catch (err) {
      pending.remove();
      say("I could not reach the assistant just then. The screens are still live.", "bot");
    }
  }

  /* ---------- the agent ---------- */

  function sessionId() {
    try { return sessionStorage.getItem(SESSION_KEY) || null; } catch (e) { return null; }
  }

  function rememberSession(id) {
    try { if (id) sessionStorage.setItem(SESSION_KEY, id); } catch (e) { /* private mode */ }
  }

  async function agentSend(message) {
    const pending = say("working…", "thinking");
    busy = true;
    try {
      const out = await api("/assist/agent", {
        method: "POST",
        body: { message, session: sessionId(), screen: window.location.pathname },
      });
      pending.remove();
      render(out);
    } catch (err) {
      pending.remove();
      say("I could not reach the assistant just then. The screens are still live.", "bot");
    } finally {
      busy = false;
    }
  }

  async function resolve(path, proposal, reason) {
    const pending = say("working…", "thinking");
    busy = true;
    try {
      const out = await api(path, {
        method: "POST",
        body: { session: sessionId(), proposal, reason },
      });
      pending.remove();
      render(out);
    } catch (err) {
      pending.remove();
      say("That did not go through. Nothing was changed.", "bot");
    } finally {
      busy = false;
    }
  }

  function render(out) {
    rememberSession(out.session);
    if (out.kind === "guide" && out.guide) {
      say(out.say, "bot");
      offerGuide(out.guide);
      return;
    }
    if (out.transcript && out.transcript.length) renderTranscript(out.transcript);
    if (out.say) say(out.say, "bot");
    if (out.kind === "unavailable") {
      agent.available = false;
      renderBrain();
      return;
    }
    if (out.kind === "proposals") {
      for (const p of out.proposals || []) renderProposal(p);
      return;
    }
    for (const d of out.done || []) {
      if (d.evidence) offerEvidence(d);
    }
  }

  function renderTranscript(rows) {
    remember({ t: "trace", rows });
    const details = el("details", "assist-trace");
    const names = [...new Set(rows.map((r) => r.tool))];
    details.appendChild(el("summary", null,
      `${rows.length} tool call${rows.length === 1 ? "" : "s"}: ${names.join(", ")}`));
    for (const r of rows) {
      const line = el("div", `trace-row ${r.ok ? "ok" : "bad"}`);
      const args = Object.entries(r.args || {}).map(([k, v]) => `${k}=${v}`).join(" ");
      line.appendChild(el("code", null, `${r.tool} ${args}`.trim()));
      line.appendChild(el("span", null, r.summary || ""));
      details.appendChild(line);
    }
    $("#assist-log").appendChild(details);
  }

  function humanTool(name) {
    return name.replace(/_/g, " ");
  }

  function renderProposal(p, settled) {
    const entry = remember({ t: "card", p, settled: settled || null });
    const card = el("div", "assist-card");
    card.dataset.proposal = p.id;
    card._entry = entry;
    card.appendChild(el("div", "kicker", "Proposed"));
    card.appendChild(el("h4", null, (p.preview && p.preview.would) || humanTool(p.tool)));
    const body = (p.preview && p.preview.request && p.preview.request.body) || p.args || {};
    const list = el("dl");
    for (const [k, v] of Object.entries(body)) {
      if (v === null || v === undefined || v === "") continue;
      list.appendChild(el("dt", null, k));
      list.appendChild(el("dd", null, String(v)));
    }
    card.appendChild(list);
    card.appendChild(el("p", "note",
      `Runs as the plant agent on behalf of ${me ? me.code : "you"}, and lands in the audit trail.`));

    const row = el("div", "row");
    const yes = el("button", null, "Do it");
    yes.addEventListener("click", () => { settle(card, "doing…"); resolve("/assist/agent/confirm", p.id); });
    row.appendChild(yes);
    if (p.surface && p.surface.steps && p.surface.steps.length) {
      const show = el("button", "ghost", "Show me");
      show.addEventListener("click", () => {
        settle(card, "showing you instead");
        // The model is told; the person does it by hand on the real form.
        resolve("/assist/agent/decline", p.id, "the person chose to do it by hand on the screen");
        beginWalk({ title: p.surface.title, steps: p.surface.steps, evidence: p.surface.evidence });
      });
      row.appendChild(show);
    }
    row.appendChild(el("span", "spacer"));
    const no = el("button", "ghost", "Not that");
    no.addEventListener("click", () => { settle(card, "declined"); resolve("/assist/agent/decline", p.id); });
    row.appendChild(no);
    card.appendChild(row);
    const log = $("#assist-log");
    log.appendChild(card);
    log.scrollTop = log.scrollHeight;
    if (settled) settle(card, settled);
  }

  function settle(card, verdict) {
    card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    card.classList.add("settled");
    card.appendChild(el("div", "verdict", verdict));
    if (card._entry && !replaying) { card._entry.settled = verdict; saveEntries(); }
  }

  function offerEvidence(done) {
    remember({ t: "evidence", done });
    const e = done.evidence;
    const b = el("button", "assist-launch", `See it — ${e.title}`);
    b.style.position = "static";
    b.style.marginTop = "4px";
    b.addEventListener("click", () => beginWalk({ title: e.title, steps: [e] }));
    $("#assist-log").appendChild(b);
  }

  /* The panel is a companion, not a page: it follows the person across
     screens. What it showed is replayed from sessionStorage, and the server
     still holds the conversation, so a proposal left open on one screen can
     be confirmed from the next. */
  function replayEntries() {
    replaying = true;
    try {
      for (const e of entries) {
        if (e.t === "you" || e.t === "bot") say(e.text, e.t);
        else if (e.t === "trace") renderTranscript(e.rows);
        else if (e.t === "card") renderProposal(e.p, e.settled);
        else if (e.t === "evidence") offerEvidence(e.done);
        else if (e.t === "guide") offerGuide(e.guide);
      }
    } finally {
      replaying = false;
    }
    const log = $("#assist-log");
    log.scrollTop = log.scrollHeight;
  }

  function newChat() {
    entries = [];
    saveEntries();
    try { sessionStorage.removeItem(SESSION_KEY); } catch (e) { /* fine */ }
    const log = $("#assist-log");
    if (log) log.textContent = "";
    suggestions = null;
    hello();
    renderSuggestions();
    $("#assist-input").focus();
  }

  function hello() {
    const what = agent.available
      ? "Ask me about this plant, tell me what to do and I will propose it for you to confirm, "
        + "or ask how to do something and I will walk you through it on screen."
      : "Ask me about this plant, or ask how to do something and I will walk you through it on screen.";
    say(`Hello ${me ? me.name.split(" ")[0] : ""}. ${what}`, "bot");
  }

  function showPanel() {
    if (!panel) { panel = buildPanel(); document.body.appendChild(panel); }
    panel.style.display = "flex";
    $(".assist-launch").style.display = "none";
    try { sessionStorage.setItem(OPEN_KEY, "1"); } catch (e) { /* fine */ }
    if (!$("#assist-log").childElementCount && entries.length) replayEntries();
    if (!$("#assist-log").childElementCount) hello();
    renderBrain();
    if (entries.length <= 1) renderSuggestions(); else clearSuggestions();
    $("#assist-input").focus();
  }

  function hidePanel(keepOpen) {
    if (panel) panel.style.display = "none";
    const launch = $(".assist-launch");
    if (launch) launch.style.display = "flex";
    if (!keepOpen) { try { sessionStorage.removeItem(OPEN_KEY); } catch (e) { /* fine */ } }
  }

  /* ---------- guide mode ---------- */

  async function startGuideById(id) {
    try {
      const guide = await api(`/assist/guides/${id}`);
      if (guide.error) { say(guide.error, "bot"); return; }
      beginWalk(guide);
    } catch (err) {
      say("That guide is not available to you.", "bot");
    }
  }

  function beginWalk(guide) {
    walk = { guide, index: 0 };
    hidePanel(true);   // the panel comes back when the walk ends, on whatever screen that is
    showStep();
  }

  function endWalk(finished) {
    sessionStorage.removeItem(KEY);
    if (ring) { ring.remove(); ring = null; }
    if (coach) { coach.remove(); coach = null; }
    const wasGuide = walk && walk.guide;
    walk = null;
    const launch = $(".assist-launch");
    if (launch) launch.style.display = "flex";
    if (finished && wasGuide) {
      showPanel();
      say(`That is ${wasGuide.title.toLowerCase()}. Ask me again any time.`, "bot");
      if (wasGuide.evidence) offerEvidence({ evidence: wasGuide.evidence });
    }
  }

  function saveWalk(exact) {
    const g = walk.guide;
    const saved = g.id
      ? { id: g.id, index: walk.index }
      : { walk: { title: g.title, steps: g.steps, evidence: g.evidence || null,
                  recorded_by: g.recorded_by, revision: g.revision, approved_by: g.approved_by },
          index: walk.index, exact };
    sessionStorage.setItem(KEY, JSON.stringify(saved));
  }

  /* A step may carry a value for the control it points at: the agent's
     proposal, typed into the real form so the person only has to check it
     and press the button. Selects fill from the API after sign-in, so the
     option may not exist yet - wait for it a little. */
  function applyFill(target, step, attempt = 0) {
    if (!step.fill || !target || step.fill.value === undefined) return;
    const value = String(step.fill.value);
    if (target.type === "checkbox") {
      const want = ["true", "1", "yes", "on"].includes(value.toLowerCase());
      if (target.checked !== want) { target.checked = want; target.dispatchEvent(new Event("change", { bubbles: true })); }
      return;
    }
    if (target.tagName === "SELECT") {
      const has = [...target.options].some((o) => o.value === value);
      if (!has) {
        if (attempt < 20) setTimeout(() => applyFill(target, step, attempt + 1), 150);
        return;
      }
    }
    if (target.value !== value) {
      target.value = value;
      target.dispatchEvent(new Event("input", { bubbles: true }));
      target.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function showStep() {
    if (!walk) return;
    const step = walk.guide.steps[walk.index];

    // A guide may cross screens. Remember where we are and let the next page
    // pick the walk back up.
    if (step.page && window.location.pathname !== step.page) {
      saveWalk(true);
      window.location = step.page;
      return;
    }

    // A control may live behind a tab: open it first.
    if (step.tab) {
      const tab = document.querySelector(`[data-tab="${step.tab}"]`);
      if (tab && tab.getAttribute("aria-selected") !== "true" && !tab.classList.contains("active")) tab.click();
    }
    const target = document.querySelector(`[data-assist="${step.anchor}"]`);
    if (!target) {
      // The anchor is gone - almost always because a panel is hidden for this
      // person's role. Say so rather than pointing at nothing.
      paint(null, step, "That control is not on this screen for your role.");
      return;
    }
    // A control may sit in a popover that a button opens: press it first.
    if (step.open && target.offsetParent === null) {
      const opener = document.querySelector(`[data-assist="${step.open}"]`);
      if (opener) opener.click();
    }
    applyFill(target, step);
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => paint(target, step, null), 260);
  }

  function paint(target, step, note) {
    if (!ring) { ring = el("div", "assist-ring"); document.body.appendChild(ring); }
    if (!coach) { coach = el("div", "assist-coach"); document.body.appendChild(coach); }

    if (target) {
      const r = target.getBoundingClientRect();
      const pad = 6;
      Object.assign(ring.style, {
        display: "block",
        left: `${r.left - pad}px`, top: `${r.top - pad}px`,
        width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px`,
      });
    } else {
      ring.style.display = "none";
    }

    coach.textContent = "";
    coach.appendChild(el("div", "step",
      `Step ${walk.index + 1} of ${walk.guide.steps.length}`));
    coach.appendChild(el("h4", null, step.title));
    coach.appendChild(el("p", null, note || step.body));
    if (walk.guide.steps.length > 1) {
      const dots = el("div", "dots");
      walk.guide.steps.forEach((_, i) => dots.appendChild(el("i", i === walk.index ? "on" : (i < walk.index ? "done" : ""))));
      coach.appendChild(dots);
    }
    if (walk.guide.recorded_by) {
      const who = `Recorded by ${walk.guide.recorded_by}`
        + (walk.guide.revision ? ` · rev ${walk.guide.revision}` : "")
        + (walk.guide.approved_by ? ` · approved by ${walk.guide.approved_by}` : " · draft");
      coach.appendChild(el("div", "meta", who));
    }

    const row = el("div", "row");
    const quit = el("button", "ghost", "Stop");
    quit.addEventListener("click", () => endWalk(false));
    row.appendChild(quit);
    row.appendChild(el("span", "spacer"));

    if (walk.index > 0) {
      const back = el("button", "ghost", "Back");
      back.addEventListener("click", () => { walk.index -= 1; showStep(); });
      row.appendChild(back);
    }
    const last = walk.index === walk.guide.steps.length - 1;
    const next = el("button", null, last ? "Done" : "Next");
    next.addEventListener("click", () => {
      if (last) { endWalk(true); return; }
      walk.index += 1;
      showStep();
    });
    row.appendChild(next);
    coach.appendChild(row);

    // Place the coach card near the ring without covering it.
    const cr = coach.getBoundingClientRect();
    if (target) {
      const r = target.getBoundingClientRect();
      const below = r.bottom + 14;
      const fits = below + cr.height < window.innerHeight - 10;
      coach.style.top = fits ? `${below}px` : `${Math.max(10, r.top - cr.height - 14)}px`;
      coach.style.left = `${Math.min(Math.max(10, r.left), window.innerWidth - cr.width - 10)}px`;
    } else {
      coach.style.top = "80px";
      coach.style.left = `${Math.max(10, window.innerWidth - cr.width - 20)}px`;
    }
  }

  const reposition = () => { if (walk) showStep(); };
  window.addEventListener("resize", reposition);
  window.addEventListener("scroll", () => {
    if (!walk || !ring || ring.style.display === "none") return;
    const step = walk.guide.steps[walk.index];
    const target = document.querySelector(`[data-assist="${step.anchor}"]`);
    if (target) paint(target, step, null);
  }, { passive: true });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && walk) endWalk(false);
  });

  /* ---------- boot ---------- */

  async function boot() {
    try {
      me = await api("/auth/me");
    } catch (err) {
      return;  // not signed in: the login screen has no use for an assistant
    }
    try {
      guides = (await api("/assist/guides")).guides || [];
    } catch (err) {
      guides = [];
    }
    try {
      agent = await api("/assist/agent/status");
    } catch (err) {
      agent = { available: false };
    }

    const launch = el("button", "assist-launch", "Assistant");
    launch.setAttribute("aria-label", "Open the plant assistant");
    launch.addEventListener("click", showPanel);
    document.body.appendChild(launch);
    loadEntries();

    // People who may write documents may record walkthroughs; nobody else
    // pays for the script.
    if ((me.capabilities || []).includes("documents.write")) {
      const s = document.createElement("script");
      s.src = "/static/assist-record.js";
      document.body.appendChild(s);
    }

    // Resume a walk that crossed a screen boundary.
    const saved = sessionStorage.getItem(KEY);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (parsed.walk) {
          walk = { guide: parsed.walk, index: Math.min(parsed.index, parsed.walk.steps.length - 1) };
          launch.style.display = "none";
          setTimeout(showStep, 400);
          return;
        }
        const guide = await api(`/assist/guides/${parsed.id}`);
        if (!guide.error) {
          // The saved index is the step that needed this page: show it, do
          // not skip past it (an old off-by-one that only a last step hid).
          walk = { guide, index: Math.min(parsed.index, guide.steps.length - 1) };
          launch.style.display = "none";
          setTimeout(showStep, 400);
          return;
        }
      } catch (err) { /* fall through to a normal page */ }
      sessionStorage.removeItem(KEY);
    }

    // The panel was open on the last screen: it is open on this one too.
    let wasOpen = false;
    try { wasOpen = sessionStorage.getItem(OPEN_KEY) === "1"; } catch (e) { /* fine */ }
    if (wasOpen) showPanel();
  }

  /* What other scripts on the page may use: the recorder adds its button
     here and plays a draft through the same engine. */
  window.fsmesAssist = {
    el, api, say, beginWalk, showPanel, hidePanel,
    get me() { return me; },
    headButton(label, title, handler) {
      headButtons.push({ label, title, handler });
      const head = panel && panel.querySelector(".assist-head");
      if (head) {
        const b = el("button", null, label);
        b.title = title || "";
        b.addEventListener("click", handler);
        head.insertBefore(b, head.querySelector(".spacer").nextSibling);
      }
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
