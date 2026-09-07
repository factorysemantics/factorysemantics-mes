/* The line view, wired up.
 *
 * Boot: fetch the layout, build the scene, start the feed, then run a frame loop
 * that reads facts at 2 Hz and draws at whatever the display gives us. The two
 * rates are deliberately different — the plant produces at one or two hertz and
 * the eye wants sixty, so the renderer interpolates between facts. It never
 * invents them.
 */

import { LineScene } from "./scene.js";
import { LineFlow } from "./flow.js";
import { DemoFeed, LiveFeed } from "./feed.js";

const $ = (selector) => document.querySelector(selector);

const params = new URLSearchParams(location.search);
const DEMO = params.get("demo") === "1";
const LINE = params.get("line");
const DEMO_URL = "/static/demo/kepsim-hour.json";

function fatal(message) {
  $("#fatal-text").textContent = message;
  $("#fatal").classList.remove("hidden");
}

function setLive(ok, text) {
  $("#live-dot").className = `dot ${ok ? "ok" : "bad"}`;
  $("#live-text").textContent = text;
}

/* --------------------------------------------------------------- the chrome */

const STATES = ["running", "idle", "down", "setup", "unknown"];

function renderStations(stations, names) {
  const host = $("#stations");
  const cards = stations.map((station) => {
    const card = document.createElement("div");
    card.className = `station ${STATES.includes(station.state) ? station.state : "unknown"}`;

    const head = document.createElement("div");
    head.className = "station-head";
    const code = document.createElement("span");
    code.className = "station-code";
    code.textContent = station.code;
    const state = document.createElement("span");
    state.className = "station-state";
    state.textContent = station.state;
    head.append(code, state);

    const name = document.createElement("div");
    name.className = "station-name";
    name.textContent = names.get(station.code) || "";

    card.append(head, name);

    if (station.analog) {
      const analog = document.createElement("div");
      analog.className = "station-analog";
      analog.append(`${station.analog.name} `);
      const value = document.createElement("strong");
      value.textContent = Number(station.analog.value).toFixed(1);
      analog.append(value);
      card.append(analog);
    }
    return card;
  });
  host.replaceChildren(...cards);
}

function renderReadout(flow, stations) {
  const rate = flow.ratePerMinute;
  $("#stat-rate").innerHTML = rate === null
    ? "—<small>/min</small>"
    : `${Math.round(rate)}<small>/min</small>`;

  const order = stations.find((s) => s.order)?.order;
  $("#stat-order").textContent = order || "—";

  const last = stations[stations.length - 1];
  $("#stat-good").textContent = last ? Math.round(last.good).toLocaleString() : "—";
  $("#stat-scrap").textContent = Math.round(
    stations.reduce((sum, s) => sum + (s.scrap || 0), 0),
  ).toLocaleString();

  // Only mention unmatched consumption once the belts have filled and it means
  // something. Before that it is just the view catching up with a line that was
  // already running, which is not a discrepancy.
  const unmatched = flow.unmatched;
  $("#unmatched-note").classList.toggle("hidden", unmatched === 0);
  $("#stat-unmatched").textContent = unmatched.toLocaleString();
  $("#filling-note").classList.toggle("hidden", flow.warm);
}

/* ------------------------------------------------------------------- boot */

async function main() {
  const feed = DEMO ? new DemoFeed(DEMO_URL) : new LiveFeed(LINE);

  let layout;
  try {
    layout = await feed.start();
  } catch (error) {
    if (error.signedOut) {
      // The dashboard owns sign-in; there is no second login form to maintain.
      location.href = "/dashboard";
      return;
    }
    fatal(DEMO
      ? `${error.message} — run "mes make-demo-feed" to build the recorded hour.`
      : `${error.message}. Is the API running?`);
    return;
  }

  if (!layout.stations.length) {
    fatal("This line has no machines on it. Seed a plant first.");
    return;
  }

  $("#line-name").textContent = `${layout.line.name} · ${layout.stations.length} stations`;
  $("#demo-badge").classList.toggle("hidden", !DEMO);
  document.title = `MES-TWIN — ${layout.line.name}`;

  // Say where the data came from. "Is this really the Kepware line?" is the
  // first question anyone asks of a picture like this, and the picture should
  // answer it rather than the person who built it.
  const badge = $("#source-badge");
  const source = layout.source;
  if (DEMO) {
    badge.textContent = "no live machine layer";
    badge.title = "Playing a recorded hour from disk. Nothing is connected.";
    badge.classList.remove("hidden");
  } else if (source) {
    badge.textContent = `via ${source.name}`;
    badge.title = [
      `Endpoint  ${source.endpoint}`,
      `Tag map   ${source.tag_map}`,
      `Security  ${source.security || "unknown"}`,
      `Addressing ${source.addressing || "unknown"}`,
      `Connected ${new Date(source.connected_at + "Z").toLocaleString()}`,
    ].join("\n");
    badge.classList.toggle("kepware", source.name === "KEPServerEX");
    badge.classList.remove("hidden");
  } else {
    badge.textContent = "no machine layer has connected";
    badge.title = "No OPC agent has ever connected to this database.";
    badge.classList.add("warn");
    badge.classList.remove("hidden");
  }

  const scene = new LineScene($("#stage")).build(layout);
  const flow = new LineFlow(layout.stations, {
    capacity: layout.conveyor.capacity,
    spacings: scene.spacings,
  });

  // Which stations the robots take units from — the rest go straight on a belt.
  const handledByRobot = new Set();
  if (scene.robots.infeed) handledByRobot.add(layout.stations[0].code);
  if (scene.robots.outfeed) handledByRobot.add(layout.stations[layout.stations.length - 1].code);

  const names = new Map(layout.stations.map((s) => [s.code, s.name]));

  feed.onResync = () => {
    // Units we were not shown still happened; clearing is the honest response to
    // a gap, rather than animating a backlog that no longer reflects the line.
    for (const belt of flow.belts) belt.items.length = 0;
  };
  feed.onError = (error) => {
    if (error.signedOut) location.href = "/dashboard";
  };

  let previous = performance.now();
  let lastStations = [];

  function frame(now) {
    requestAnimationFrame(frame);
    // Clamp dt so a backgrounded tab does not resume by teleporting the line.
    const dt = Math.min(0.1, (now - previous) / 1000);
    previous = now;
    const seconds = now / 1000;

    if (feed.advance) feed.advance(dt, now);

    for (const unit of feed.due(now)) flow.book(unit, seconds);

    // Units from stations without a robot go straight onto the belt leaving
    // them; the scene drains the robot-fed ones on its own schedule.
    for (let i = 0; i < layout.stations.length; i += 1) {
      const station = layout.stations[i];
      if (handledByRobot.has(station.code)) continue;
      const made = flow.drain(station.code);
      if (made && i < flow.belts.length) flow.belts[i].spawn(made);
    }

    flow.update(dt, seconds);
    scene.update(dt, flow);
    scene.render();

    if (feed.stations !== lastStations) {
      lastStations = feed.stations;
      scene.applyStates(lastStations);
      renderStations(lastStations, names);
      renderReadout(flow, lastStations);
      setLive(feed.connected, DEMO ? "recorded" : "live");
    } else if (!feed.connected) {
      setLive(false, "connection lost");
    }
  }

  // ?debug=1 exposes the moving parts for the console. Tuning belt speeds and
  // robot timings from the outside is otherwise guesswork.
  if (params.get("debug") === "1") {
    window.__line = { scene, flow, feed, layout };
  }

  setLive(true, DEMO ? "recorded" : "live");
  requestAnimationFrame(frame);
}

main();
