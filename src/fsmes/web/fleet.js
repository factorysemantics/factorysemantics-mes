/* The fleet console's script.

   It fetches one document from its own server and draws a table. There is
   no other request in this file, no form, no button and no method other
   than GET - which is the property a reviewer should check by reading it,
   and which tests/test_fleet_console.py checks by parsing it. */

const EVERY = 5000;

function pill(text, kind) {
  const span = document.createElement("span");
  span.className = "pill " + (kind || "");
  span.textContent = text;
  return span;
}

function cell(row, node) {
  const td = document.createElement("td");
  if (node instanceof Node) td.appendChild(node);
  else td.textContent = node;
  row.appendChild(td);
  return td;
}

/* Unknown is printed, never blanked: an empty cell reads as "nothing to
   report" and what is meant is "nobody knows". */
function known(value, suffix) {
  if (value === null || value === undefined || value === "") return "unknown";
  return suffix ? value + suffix : String(value);
}

function packCell(plant) {
  if (!plant.answered) return "unknown";
  if (!plant.pack) return "unknown";
  const wrap = document.createDocumentFragment();
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = plant.pack;
  wrap.appendChild(name);
  const drift = document.createElement("span");
  drift.className = "why";
  if (plant.drifted === null || plant.drifted === undefined) {
    drift.textContent = "never applied, or the pack is not on that machine";
  } else if (plant.drifted) {
    drift.textContent = "drifted from the pack it was given";
  } else {
    drift.textContent = "no drift";
  }
  wrap.appendChild(drift);
  return wrap;
}

function modulesCell(plant) {
  if (!plant.answered || !plant.modules_total) return "unknown";
  const wrap = document.createDocumentFragment();
  const count = document.createElement("span");
  count.textContent = plant.modules_on.length + " on of " + plant.modules_total;
  wrap.appendChild(count);
  if (plant.modules_off && plant.modules_off.length) {
    const off = document.createElement("span");
    off.className = "why";
    off.textContent = "off: " + plant.modules_off.join(", ");
    wrap.appendChild(off);
  }
  return wrap;
}

/* How many machines this plant has. A plant that could not count them says
   unknown; a plant that counted none says none, and says it in the same
   words the row's state pill does. */
function lineCell(plant) {
  if (!plant.answered || plant.line_answered === null
      || plant.line_answered === undefined) return "unknown";
  if (!plant.line_answered) return "unknown";
  if (plant.line_equipment === 0) return "no machines";
  return plant.line_equipment + " machines";
}

/* What that plant can currently see of its own machines. Three ways to be
   unable to say so, and none of them is "0 disconnected": the plant did not
   answer, the plant answered from a build with no such block, or the plant
   has never had a connection reported for any machine. */
function watchingCell(plant) {
  const w = plant.watching || {};
  if (!plant.answered || w.machines === undefined || w.machines === null) return "unknown";
  if (!w.machines) return "no machines";
  const node = document.createElement("span");
  if (w.disconnected) {
    node.appendChild(pill(w.disconnected + " of " + w.machines + " disconnected", "unknown"));
  } else {
    node.appendChild(document.createTextNode(w.connected + " of " + w.machines + " connected"));
  }
  if (w.unknown) {
    const why = document.createElement("span");
    why.className = "why";
    // Not a fault: nothing has ever reported a connection for these, which
    // is what a plant fed by hand or over MQTT looks like.
    why.textContent = w.unknown + " with no connection reported";
    node.appendChild(why);
  }
  return node;
}

function draw(fleet) {
  document.getElementById("totals").textContent = fleet.says;
  document.getElementById("ownership").textContent = fleet.ownership_says;
  document.getElementById("asked").textContent = "asked at " + fleet.asked_at;
  document.getElementById("empty").hidden = fleet.plants.length > 0;

  const body = document.getElementById("rows");
  body.textContent = "";
  for (const plant of fleet.plants) {
    const row = document.createElement("tr");

    const first = document.createDocumentFragment();
    const link = document.createElement("a");
    link.className = "name";
    link.href = plant.base + "/dashboard";
    link.textContent = plant.name;
    first.appendChild(link);
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = plant.label || plant.about;
    first.appendChild(label);
    cell(row, first);

    const owned = cell(row, pill(plant.owned, plant.owned));
    const why = document.createElement("span");
    why.className = "why";
    why.textContent = plant.ownership;
    owned.appendChild(why);

    /* Three states. "empty" is a plant that answered and said it has no
       schema or no machines: not down, not unknown, and not something a
       person should have to work out from a dashboard with nothing on it.
       The reason under the pill is the plant's own words, never inferred. */
    const state = cell(row, pill(plant.state === "empty" ? "answered, empty" : plant.state,
                                 plant.state));
    const reason = document.createElement("span");
    reason.className = "why";
    if (!plant.answered && plant.why) reason.textContent = plant.why;
    else if (plant.state === "empty") reason.textContent = plant.empty_because;
    if (reason.textContent) state.appendChild(reason);

    cell(row, known(plant.profile));
    cell(row, known(plant.timezone));
    cell(row, plant.answered ? pill(plant.shadow ? "shadow" : "live",
                                    plant.shadow ? "shadow" : "") : "unknown");
    cell(row, packCell(plant));
    cell(row, plant.answered
      ? known(plant.schema_revision) + (plant.schema_at_head === false ? " (behind head)" : "")
      : "unknown");
    cell(row, lineCell(plant));
    cell(row, watchingCell(plant));
    cell(row, modulesCell(plant));
    cell(row, known(plant.last_answered));

    body.appendChild(row);
  }
}

async function poll() {
  try {
    const response = await fetch("/fleet.json", { method: "GET" });
    if (response.ok) draw(await response.json());
  } catch (e) {
    /* The console being unreachable is the console's problem, not a plant's:
       leaving the last table on screen with its own timestamp is honest,
       and rewriting every row as unknown would blame the plants. */
  }
}

poll();
setInterval(poll, EVERY);
