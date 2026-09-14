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

    const state = cell(row, pill(plant.state, plant.state));
    if (!plant.answered && plant.why) {
      const reason = document.createElement("span");
      reason.className = "why";
      reason.textContent = plant.why;
      state.appendChild(reason);
    }

    cell(row, known(plant.profile));
    cell(row, known(plant.timezone));
    cell(row, plant.answered ? pill(plant.shadow ? "shadow" : "live",
                                    plant.shadow ? "shadow" : "") : "unknown");
    cell(row, packCell(plant));
    cell(row, plant.answered
      ? known(plant.schema_revision) + (plant.schema_at_head === false ? " (behind head)" : "")
      : "unknown");
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
