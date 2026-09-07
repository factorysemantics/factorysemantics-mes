/* The walkthrough recorder.

   A supervisor presses Record, does the job on the real screens, and says
   why at each step. What comes out is a walkthrough document - the same
   shape the assistant already plays - which an approver puts in force.

   Only controls with a data-assist anchor are recordable. A click anywhere
   else is counted and shown, never turned into a brittle selector: the
   promise the built-in guides make (a renamed control breaks a walk loudly)
   holds for recorded ones too.

   The recording lives in sessionStorage so it survives the page changes a
   real task involves. Loaded only for people who may write documents. */

(function () {
  const REC_KEY = "fsmes-recording";
  const A = window.fsmesAssist;
  if (!A) return;
  const { el, api } = A;
  const $ = (s, root = document) => root.querySelector(s);

  let rec = null;      // { title, steps: [...], unkept: [...], tab, lastButton }
  let hud = null;
  let card = null;     // the edit card for the step just captured
  let review = null;
  let badges = [];
  let recognition = null;

  /* ---------- state ---------- */

  function load() {
    try { rec = JSON.parse(sessionStorage.getItem(REC_KEY) || "null"); } catch (e) { rec = null; }
    if (rec && !Array.isArray(rec.steps)) rec = null;
  }

  function save() {
    try {
      if (rec) sessionStorage.setItem(REC_KEY, JSON.stringify(rec));
      else sessionStorage.removeItem(REC_KEY);
    } catch (e) { /* private mode: the recording lives in memory only */ }
  }

  function start(title) {
    rec = { title: title.trim(), steps: [], unkept: [], tab: null, lastButton: null, started: Date.now() };
    save();
    mount();
    A.hidePanel(true);
  }

  function discard() {
    rec = null;
    save();
    unmount();
    A.showPanel();
    A.say("Recording discarded. Nothing was saved.", "bot");
  }

  /* ---------- capture ---------- */

  const CONTROL = "button, a, input, select, textarea, [role=tab], label";
  const OURS = ".rec-hud, .rec-coach, .rec-review, .assist-panel, .assist-coach, .assist-launch, .assist-ring";

  function isField(control) {
    if (control.tagName === "SELECT" || control.tagName === "TEXTAREA") return true;
    return control.tagName === "INPUT" && !["button", "submit", "reset", "checkbox", "radio"].includes(control.type);
  }

  function labelFor(control, anchored) {
    const byFor = control.id ? $(`label[for="${control.id}"]`) : null;
    const text = (byFor && byFor.textContent) || control.getAttribute("aria-label")
      || control.getAttribute("placeholder") || control.getAttribute("title") || control.name || "";
    const raw = text.trim().replace(/\s+/g, " ").slice(0, 60);
    const clean = raw.charAt(0).toLowerCase() + raw.slice(1);
    const own = (control.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40);
    if (control.tagName === "BUTTON" || control.type === "submit" || control.type === "button") {
      return `Press ${own || clean || anchored.dataset.assist}`;
    }
    if (control.type === "checkbox" || control.type === "radio") return `Tick ${clean || own || anchored.dataset.assist}`;
    if (control.tagName === "SELECT") return `Choose ${clean || anchored.dataset.assist}`;
    if (control.tagName === "A") return `Open ${own || clean}`;
    if (control.tagName === "LABEL") return `${own || clean}`;
    return `Enter ${clean || anchored.dataset.assist}`;
  }

  function displayValue(control) {
    if (control.tagName === "SELECT") {
      const o = control.selectedOptions && control.selectedOptions[0];
      return o ? o.textContent.trim() : control.value;
    }
    if (control.type === "checkbox") return control.checked ? "ticked" : "unticked";
    return control.value;
  }

  function rawValue(control) {
    if (control.type === "checkbox") return control.checked ? "true" : "false";
    return control.value;
  }

  function openerFor(control, anchor) {
    // A control inside a popover or a reveal-on-demand box was reached by a
    // button; the walk has to press that button first.
    const box = control.closest(".popover, .reason");
    if (!box || !rec.lastButton) return null;
    if (rec.lastButton.page !== window.location.pathname || rec.lastButton.anchor === anchor) return null;
    return rec.lastButton.anchor;
  }

  function onClick(e) {
    if (!rec) return;
    const t = e.target;
    if (!(t instanceof Element) || t.closest(OURS)) return;
    const control = t.closest(CONTROL);
    if (!control) return;
    if (control.dataset.tab) { rec.tab = control.dataset.tab; save(); return; }
    const anchored = control.closest("[data-assist]");
    if (!anchored) { unkept(control); return; }
    if (isField(control)) return;  // the change event makes the step
    if (control.tagName === "LABEL") return;
    capture(anchored, control, false);
  }

  function onChange(e) {
    if (!rec) return;
    const control = e.target;
    if (!(control instanceof Element) || control.closest(OURS)) return;
    if (!control.matches("input, select, textarea")) return;
    const anchored = control.closest("[data-assist]");
    if (!anchored) { unkept(control); return; }
    capture(anchored, control, true);
  }

  function unkept(control) {
    const text = (control.textContent || control.getAttribute("placeholder") || control.getAttribute("aria-label")
      || control.name || control.tagName).trim().replace(/\s+/g, " ").slice(0, 60);
    rec.unkept.push({ page: window.location.pathname, text });
    save();
    flash(`Not recordable: "${text}" has no anchor yet`);
    renderHud();
  }

  function capture(anchored, control, withValue) {
    const page = window.location.pathname;
    const anchor = anchored.dataset.assist;
    // The same control twice in a row is one step: a field fires change when
    // it loses focus to the card, a select when it is picked - never two
    // steps. It is the *control* that must match, not only the anchor: a form
    // with one anchor for all its fields still gets one step per field.
    const sig = `${control.tagName}#${control.id || ""}[${control.name || ""}]`;
    let step = rec.steps[rec.steps.length - 1];
    const same = step && step.page === page && step.anchor === anchor && step.sig === sig;
    if (!same) {
      keepOpenCard();
      step = { page, anchor, sig, title: labelFor(control, anchored), body: "", fresh: true };
      if (rec.tab) step.tab = rec.tab;
      const opener = openerFor(control, anchor);
      if (opener) step.open = opener;
      rec.steps.push(step);
    }
    if (withValue) {
      step.value = rawValue(control);
      step.display = displayValue(control);
      if (control.type === "checkbox") step.title = labelFor(control, anchored).replace(/^Tick/, step.value === "true" ? "Tick" : "Untick");
    }
    const isButton = control.tagName === "BUTTON" || control.type === "submit" || control.type === "button";
    if (isButton) rec.lastButton = { page, anchor };
    save();
    layoutBadges();
    const coarse = anchored !== control && !anchored.matches("button, a, input, select, textarea, label");
    if (!(same && card && card.dataset.anchor === anchor)) showCard(step, anchored, coarse);
    else refreshCard(step);
  }

  function refreshCard(step) {
    // A value arrived for the step whose card is already open: offer the prefill.
    if (!card || step.value === undefined || card.querySelector(".rec-prefill")) return;
    const row1 = card.querySelector(".row.opts") || card.insertBefore(el("div", "row opts"), card.querySelector(".row:last-child"));
    const lab = el("label", "rec-prefill");
    const box = el("input");
    box.type = "checkbox";
    box.addEventListener("change", () => { step.prefill = box.checked; save(); });
    lab.appendChild(box);
    lab.appendChild(document.createTextNode(` Prefill "${step.display}" for the trainee`));
    row1.appendChild(lab);
  }

  /* ---------- the edit card ---------- */

  function showCard(step, anchored, coarse) {
    closeCard();
    card = el("div", "assist-coach rec-coach");
    card.dataset.anchor = step.anchor;
    const n = rec.steps.indexOf(step) + 1;
    card.appendChild(el("div", "step", `Recording · step ${n} · ${step.anchor}${coarse ? " (whole panel)" : ""}`));
    const title = el("input", "rec-title");
    title.value = step.title;
    title.setAttribute("aria-label", "Step title");
    title.addEventListener("input", () => { step.title = title.value; save(); });
    card.appendChild(title);

    const why = el("textarea", "rec-why");
    why.placeholder = "Why this step? Say it the way you would to a new starter.";
    why.rows = 3;
    why.value = step.body || "";
    why.addEventListener("input", () => { step.body = why.value; save(); });
    card.appendChild(why);

    const row1 = el("div", "row opts");
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SR) {
      const mic = el("button", "ghost rec-mic", "🎤 Speak");
      mic.type = "button";
      mic.addEventListener("click", () => toggleMic(mic, why, step));
      row1.appendChild(mic);
    }
    if (step.value !== undefined) {
      const lab = el("label", "rec-prefill");
      const box = el("input");
      box.type = "checkbox";
      box.checked = Boolean(step.prefill);
      box.addEventListener("change", () => { step.prefill = box.checked; save(); });
      lab.appendChild(box);
      lab.appendChild(document.createTextNode(` Prefill "${step.display}" for the trainee`));
      row1.appendChild(lab);
    }
    if (row1.childElementCount) card.appendChild(row1);

    const row = el("div", "row");
    const drop = el("button", "ghost", "Drop step");
    drop.type = "button";
    drop.addEventListener("click", () => { rec.steps.splice(rec.steps.indexOf(step), 1); save(); closeCard(); layoutBadges(); renderHud(); });
    row.appendChild(drop);
    row.appendChild(el("span", "spacer"));
    const keep = el("button", null, "Keep");
    keep.type = "button";
    keep.addEventListener("click", () => { keepStep(step); closeCard(); });
    row.appendChild(keep);
    card.appendChild(row);
    document.body.appendChild(card);
    place(card, anchored);
    renderHud();
    // Take the keyboard only when the person is not already typing somewhere:
    // a field that fired change because they clicked into the next field must
    // not have its successor's text land in this card.
    // The check waits: change fires *between* blur and the next focus, so
    // the field the person clicked is not active yet when the card opens.
    setTimeout(() => {
      const active = document.activeElement;
      const typing = active && active.matches("input, select, textarea") && !active.closest(OURS);
      if (!typing && card) why.focus();
    }, 120);
  }

  function keepStep(step) {
    delete step.fresh;
    if (step.value !== undefined) {
      if (step.prefill) step.fill = { value: step.value };
      delete step.value; delete step.display; delete step.prefill;
    }
    save();
  }

  function keepOpenCard() {
    // Clicking on with the card still open means "keep it as it stands".
    for (const s of rec.steps) if (s.fresh) keepStep(s);
    closeCard();
  }

  function closeCard() {
    if (recognition) { try { recognition.stop(); } catch (e) { /* fine */ } recognition = null; }
    if (card) { card.remove(); card = null; }
  }

  function toggleMic(button, textarea, step) {
    if (recognition) { recognition.stop(); return; }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SR();
    recognition.lang = navigator.language || "en-US";
    recognition.interimResults = false;
    recognition.continuous = true;
    button.textContent = "■ Stop";
    recognition.onresult = (ev) => {
      let heard = "";
      for (let i = ev.resultIndex; i < ev.results.length; i += 1) {
        if (ev.results[i].isFinal) heard += ev.results[i][0].transcript;
      }
      if (!heard) return;
      textarea.value = (textarea.value ? textarea.value.trimEnd() + " " : "") + heard.trim();
      step.body = textarea.value;
      save();
    };
    recognition.onend = () => { recognition = null; button.textContent = "🎤 Speak"; };
    recognition.onerror = () => { button.textContent = "🎤 Speak"; };
    try { recognition.start(); } catch (e) { recognition = null; button.textContent = "🎤 Speak"; }
  }

  function place(box, target) {
    const cr = box.getBoundingClientRect();
    const r = target.getBoundingClientRect();
    const below = r.bottom + 14;
    const fits = below + cr.height < window.innerHeight - 10;
    box.style.top = fits ? `${below}px` : `${Math.max(10, r.top - cr.height - 14)}px`;
    box.style.left = `${Math.min(Math.max(10, r.left), window.innerWidth - cr.width - 10)}px`;
  }

  /* ---------- badges and the HUD ---------- */

  function layoutBadges() {
    for (const b of badges) b.remove();
    badges = [];
    if (!rec) return;
    rec.steps.forEach((step, i) => {
      if (step.page !== window.location.pathname) return;
      const target = document.querySelector(`[data-assist="${step.anchor}"]`);
      if (!target || target.offsetParent === null) return;
      const r = target.getBoundingClientRect();
      const b = el("div", "rec-badge", String(i + 1));
      b.style.top = `${Math.max(0, r.top - 10)}px`;
      b.style.left = `${Math.max(0, r.right - 12)}px`;
      document.body.appendChild(b);
      badges.push(b);
    });
  }

  function renderHud() {
    if (!hud) {
      hud = el("div", "rec-hud");
      document.body.appendChild(hud);
    }
    hud.textContent = "";
    hud.appendChild(el("span", "dot"));
    hud.appendChild(el("strong", null, `Recording — ${rec.title}`));
    const n = rec.steps.length;
    hud.appendChild(el("span", "count", `${n} step${n === 1 ? "" : "s"}`));
    if (rec.unkept.length) hud.appendChild(el("span", "count bad", `${rec.unkept.length} not recordable`));
    const stop = el("button", null, "Stop & review");
    stop.type = "button";
    stop.addEventListener("click", () => { keepOpenCard(); openReview(); });
    hud.appendChild(stop);
    const bin = el("button", "ghost", "Discard");
    bin.type = "button";
    bin.addEventListener("click", () => { if (window.confirm("Discard this recording?")) discard(); });
    hud.appendChild(bin);
  }

  let flashTimer = null;
  function flash(text) {
    if (!hud) return;
    let note = hud.querySelector(".flash");
    if (!note) { note = el("span", "flash"); hud.appendChild(note); }
    note.textContent = text;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { if (note) note.remove(); }, 3500);
  }

  /* ---------- the review ---------- */

  function slug(text) {
    return text.toUpperCase().replace(/[^A-Z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 32).replace(/-$/, "") || "WALK";
  }

  function cleanSteps() {
    return rec.steps.map((s) => {
      const out = { page: s.page, anchor: s.anchor, title: s.title, body: s.body || "" };
      if (s.fill) out.fill = { value: s.fill.value };
      if (s.tab) out.tab = s.tab;
      if (s.open) out.open = s.open;
      return out;
    });
  }

  function openReview() {
    closeReview();
    review = el("div", "rec-review");
    const box = el("div", "rec-review-box");
    review.appendChild(box);
    box.appendChild(el("h3", null, "Review the walkthrough"));
    box.appendChild(el("p", "muted", "Check every step reads the way you would say it. Reorder or drop steps, then save it as a draft. An approver puts it in force on the Instructions screen."));

    const meta = el("div", "rec-meta");
    const title = field(meta, "Title", "input", rec.title);
    title.addEventListener("input", () => { rec.title = title.value; save(); });
    const code = field(meta, "Document code", "input", rec.code || `SWI-${slug(rec.title)}`);
    code.addEventListener("input", () => { rec.code = code.value; save(); });
    const when = field(meta, "When would someone ask for this?", "textarea", rec.when || "");
    when.placeholder = "e.g. changing over the filler, running the first bottles after a changeover";
    when.addEventListener("input", () => { rec.when = when.value; save(); });
    const needs = el("select");
    for (const cap of (A.me && A.me.capabilities) || ["plant.read"]) {
      const o = new Option(cap, cap);
      needs.appendChild(o);
    }
    needs.value = rec.needs || (needs.querySelector('option[value="plant.read"]') ? "plant.read" : needs.value);
    const needsWrap = el("label", "field");
    needsWrap.appendChild(el("span", null, "Who may follow it (needs the capability)"));
    needsWrap.appendChild(needs);
    meta.appendChild(needsWrap);
    needs.addEventListener("change", () => { rec.needs = needs.value; save(); });
    box.appendChild(meta);

    const list = el("ol", "rec-steps");
    box.appendChild(list);
    const renderList = () => {
      list.textContent = "";
      rec.steps.forEach((s, i) => {
        const li = el("li");
        const head = el("div", "head");
        head.appendChild(el("code", null, `${s.page} · ${s.anchor}${s.tab ? ` · tab ${s.tab}` : ""}${s.open ? ` · opens via ${s.open}` : ""}`));
        head.appendChild(el("span", "spacer"));
        const up = el("button", "ghost", "↑"); up.type = "button"; up.disabled = i === 0;
        up.addEventListener("click", () => { rec.steps.splice(i - 1, 0, rec.steps.splice(i, 1)[0]); save(); renderList(); });
        const down = el("button", "ghost", "↓"); down.type = "button"; down.disabled = i === rec.steps.length - 1;
        down.addEventListener("click", () => { rec.steps.splice(i + 1, 0, rec.steps.splice(i, 1)[0]); save(); renderList(); });
        const del = el("button", "ghost danger", "✕"); del.type = "button";
        del.addEventListener("click", () => { rec.steps.splice(i, 1); save(); renderList(); });
        head.append(up, down, del);
        li.appendChild(head);
        const t = el("input"); t.value = s.title; t.setAttribute("aria-label", "Step title");
        t.addEventListener("input", () => { s.title = t.value; save(); });
        li.appendChild(t);
        const w = el("textarea"); w.rows = 2; w.value = s.body || ""; w.placeholder = "Why this step?";
        w.addEventListener("input", () => { s.body = w.value; save(); });
        li.appendChild(w);
        if (s.fill) {
          const pf = el("label", "rec-prefill");
          const cb = el("input"); cb.type = "checkbox"; cb.checked = true;
          cb.addEventListener("change", () => { if (!cb.checked) { delete s.fill; save(); renderList(); } });
          pf.appendChild(cb);
          pf.appendChild(document.createTextNode(` Prefill "${s.fill.value}"`));
          li.appendChild(pf);
        }
        list.appendChild(li);
      });
      if (!rec.steps.length) list.appendChild(el("li", "muted", "No steps yet. Go back and do the task; every click on a recordable control becomes a step."));
    };
    renderList();

    if (rec.unkept.length) {
      const warn = el("div", "rec-unkept");
      warn.appendChild(el("strong", null, `${rec.unkept.length} click${rec.unkept.length === 1 ? "" : "s"} could not be recorded`));
      warn.appendChild(el("p", "muted", "These controls have no anchor yet, so the walk cannot point at them. Say them in words in the nearest step, or ask for the anchor to be added."));
      const ul = el("ul");
      for (const u of rec.unkept.slice(0, 12)) ul.appendChild(el("li", null, `${u.page}: "${u.text}"`));
      warn.appendChild(ul);
      box.appendChild(warn);
    }

    const err = el("p", "rec-error hidden");
    box.appendChild(err);

    const row = el("div", "row");
    const back = el("button", "ghost", "Keep recording"); back.type = "button";
    back.addEventListener("click", closeReview);
    row.appendChild(back);
    const play = el("button", "ghost", "Play it"); play.type = "button";
    play.disabled = !rec.steps.length;
    play.addEventListener("click", () => { closeReview(); A.beginWalk({ title: rec.title, steps: cleanSteps(), recorded_by: A.me && A.me.code, revision: "draft" }); });
    row.appendChild(play);
    row.appendChild(el("span", "spacer"));
    const bin = el("button", "ghost danger", "Discard"); bin.type = "button";
    bin.addEventListener("click", () => { if (window.confirm("Discard this recording?")) { closeReview(); discard(); } });
    row.appendChild(bin);
    const saveBtn = el("button", null, "Save as draft"); saveBtn.type = "button";
    saveBtn.disabled = !rec.steps.length;
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      err.classList.add("hidden");
      try {
        const made = await api("/documents", {
          method: "POST",
          body: { code: code.value.trim(), title: rec.title.trim(), body: (rec.when || "").trim(),
                  kind: "walkthrough", steps: cleanSteps(), needs: needs.value },
        });
        const saved = made.code;
        rec = null; save(); unmount(); closeReview();
        A.showPanel();
        A.say(`Saved "${made.title}" as draft ${saved}, revision ${made.revision}. `
          + "It is not offered to anyone until an approver puts it in force on the Instructions screen.", "bot");
      } catch (e) {
        err.textContent = e.message || "That did not save.";
        err.classList.remove("hidden");
        saveBtn.disabled = false;
      }
    });
    row.appendChild(saveBtn);
    box.appendChild(row);
    document.body.appendChild(review);
  }

  function field(parent, label, tag, value) {
    const wrap = el("label", "field");
    wrap.appendChild(el("span", null, label));
    const input = el(tag);
    input.value = value || "";
    wrap.appendChild(input);
    parent.appendChild(wrap);
    return input;
  }

  function closeReview() {
    if (review) { review.remove(); review = null; }
  }

  /* ---------- mount / unmount ---------- */

  function mount() {
    for (const s of rec.steps) if (s.fresh) keepStep(s);   // a card left open on the last screen
    document.addEventListener("click", onClick, true);
    document.addEventListener("change", onChange, true);
    window.addEventListener("scroll", layoutBadges, { passive: true });
    window.addEventListener("resize", layoutBadges);
    renderHud();
    setTimeout(layoutBadges, 600);
  }

  function unmount() {
    document.removeEventListener("click", onClick, true);
    document.removeEventListener("change", onChange, true);
    window.removeEventListener("scroll", layoutBadges);
    window.removeEventListener("resize", layoutBadges);
    closeCard();
    for (const b of badges) b.remove();
    badges = [];
    if (hud) { hud.remove(); hud = null; }
  }

  /* ---------- the way in: a Record button on the panel ---------- */

  A.headButton("Record", "Record a walkthrough: do the task, say why at each step", () => {
    const log = $("#assist-log");
    if (!log) return;
    const form = el("form", "rec-start");
    const input = el("input");
    input.placeholder = "Name the walkthrough, e.g. Change over the filler";
    input.setAttribute("aria-label", "Walkthrough title");
    const go = el("button", null, "Start recording");
    form.append(input, go);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (!input.value.trim()) return;
      form.remove();
      A.say(`Recording "${input.value.trim()}". Do the task as you normally would; after each click I will ask why. `
        + "Press Stop & review at the top when you are done.", "bot");
      start(input.value);
    });
    log.appendChild(form);
    log.scrollTop = log.scrollHeight;
    input.focus();
  });

  load();
  if (rec) mount();
})();
