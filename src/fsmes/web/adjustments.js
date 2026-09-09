/* Engineering › Adjustments: the recommendation queue.

   Analysis - an agent, a trigger, a person - proposes a setpoint change
   with a rationale; an engineer approves or rejects here; the OPC agent
   writes; the process value is read back and the outcome recorded. This is
   the only path to a PLC, and every step of it is on this screen. */

const { $, el, api, fmt, toast } = window.FS;

const REFRESH_MS = 5000;
let setpoints = [];   // writable tags from the fabric
let timer = null;

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

async function loadSetpoints() {
  const browse = await api("/equipment/tags");
  setpoints = browse.rows.filter((r) => r.writable);
  const machines = [...new Set(setpoints.map((r) => r.equipment))].sort();
  const m = $("#p-machine");
  m.replaceChildren(...machines.map((c) => new Option(c, c)));
  if (!machines.length) m.appendChild(new Option("no writable setpoints published", ""));
  fillTags();
}

function fillTags() {
  const machine = $("#p-machine").value;
  const mine = setpoints.filter((r) => r.equipment === machine);
  const t = $("#p-tag");
  t.replaceChildren(...mine.map((r) => new Option(`${r.tag} (now ${fmt.qty(r.value)}${r.unit ? " " + r.unit : ""})`, r.tag)));
  showBounds();
}

function showBounds() {
  const r = setpoints.find((s) => s.equipment === $("#p-machine").value && s.tag === $("#p-tag").value);
  $("#p-bounds").textContent = r ? `bounds ${r.min} – ${r.max}${r.unit ? " " + r.unit : ""}, drives ${r.drives || "?"}` : "";
}

function statusPill(s) {
  const cls = { proposed: "planned", approved: "released", written: "released", verified: "running", failed: "down", rejected: "" }[s] || "";
  return el("span", `pill ${cls}`, s);
}

function outcome(r) {
  if (r.status === "verified" || (r.status === "failed" && r.verification && r.verification.followed !== undefined)) {
    const v = r.verification || {};
    return `${r.drives || "PV"} ${fmt.qty(v.pv_before)} → ${fmt.qty(v.pv_after)} ${v.followed ? "followed" : "did not follow"}`;
  }
  if (r.status === "failed") return (r.verification && r.verification.error) || "failed";
  if (r.status === "written") return `written ${fmt.clock(r.written_at)}, verifying in ${Math.round(r.verify_after_seconds)} s`;
  if (r.status === "rejected") return r.decision_note ? `rejected: ${r.decision_note}` : "rejected";
  if (r.status === "approved") return `approved by ${r.decided_by}; the agent writes next`;
  return "";
}

const QUEUE_PAGE = 50;
let queueOffset = 0;

async function loadQueue() {
  // The server's page: recommendations accumulate for as long as an agent
  // watches the plant, and the queue must say how deep it is.
  const params = new URLSearchParams({ limit: String(QUEUE_PAGE), offset: String(queueOffset) });
  if ($("#q-status").value) params.set("status", $("#q-status").value);
  if ($("#q-machine").value) params.set("equipment", $("#q-machine").value);
  const page = await api(`/adjustments?${params}`);
  queueOffset = page.offset;
  const rows = page.items;
  const body = $("#queue-table tbody");
  body.replaceChildren();
  if (!rows.length) { const tr = el("tr"); const td = el("td", "muted", page.total ? "No recommendation matches." : "No recommendations yet."); td.colSpan = 10; tr.append(td); body.append(tr); }
  for (const r of rows) {
    const tr = el("tr");
    tr.append(el("td", "code", r.code));
    const where = el("td"); where.append(FS.link("machine", r.equipment)); tr.append(where);
    tr.append(el("td", "code", r.tag), el("td", "num", fmt.qty(r.current_value)), el("td", "num", fmt.qty(r.proposed_value)));
    const why = el("td", "small", r.rationale);
    if (r.evidence && Object.keys(r.evidence).length) why.title = JSON.stringify(r.evidence, null, 1);
    tr.append(why, el("td", "muted small", r.proposed_by));
    const st = el("td"); st.append(statusPill(r.status)); tr.append(st);
    tr.append(el("td", "small", outcome(r)));
    const actions = el("td");
    if (r.status === "proposed" && FS.can("adjustments.approve")) {
      const ok = el("button", "small", "Approve"); ok.type = "button";
      ok.addEventListener("click", async () => {
        try { await api(`/adjustments/${r.code}/approve`, { method: "POST", body: { note: window.prompt("Note (optional):") || null } }); toast(`${r.code} approved — the agent writes it now`); await loadQueue(); }
        catch (err) { toast(err.message, "bad"); }
      });
      const no = el("button", "ghost small", "Reject"); no.type = "button";
      no.addEventListener("click", async () => {
        try { await api(`/adjustments/${r.code}/reject`, { method: "POST", body: { note: window.prompt("Why?") || null } }); toast(`${r.code} rejected`); await loadQueue(); }
        catch (err) { toast(err.message, "bad"); }
      });
      actions.append(ok, " ", no);
    }
    tr.append(actions);
    body.append(tr);
  }
  const count = (s) => rows.filter((r) => r.status === s).length;
  $("#queue-count").textContent = `— ${rows.length} of ${page.total.toLocaleString()}`;
  FS.pager($("#q-pager"), page, (offset) => { queueOffset = offset; loadQueue().catch(fail); });
  $("#kpi-proposed").textContent = count("proposed");
  $("#kpi-approved").textContent = count("approved") + count("written");
  $("#kpi-verified").textContent = count("verified");
  $("#kpi-failed").textContent = count("failed");
  window.__fsmesPageData = rows;
}

function wire() {
  $("#p-machine").addEventListener("change", fillTags);
  $("#p-tag").addEventListener("change", showBounds);
  $("#propose-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const out = await api("/adjustments", { method: "POST", body: {
        equipment: $("#p-machine").value, tag: $("#p-tag").value, value: Number($("#p-value").value),
        rationale: $("#p-rationale").value.trim() } });
      toast(`${out.code} proposed — awaiting approval`);
      $("#p-value").value = ""; $("#p-rationale").value = "";
      await loadQueue();
    } catch (err) { toast(err.message, "bad"); }
  });
}

(async function boot() {
  await FS.whoami().catch(() => {});
  FS.applyCapGates();
  wire();
  for (const id of ["q-status", "q-machine"]) $(`#${id}`).addEventListener("change", () => { queueOffset = 0; loadQueue().catch(fail); });
  await loadSetpoints().catch(fail);
  const run = () => loadQueue().then(() => live(true)).catch(fail);
  await run();
  timer = setInterval(run, REFRESH_MS);
})().catch(fail);
