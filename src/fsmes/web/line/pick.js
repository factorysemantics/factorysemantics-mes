/* Pointing at a machine's parts on the line view, and moving them.
 *
 * Each part the MES holds under a machine (a mould's heater bands, its melt
 * sensor) gets a small marker: a ring for a band, a ball for anything else,
 * where the part is held to sit. Red while a repair order is open on it.
 * Click a marker, or a machine, and a panel says what it is, where, who pays
 * for it and from where, and the open repair order.
 *
 * Someone who may write master data can press Move, click the spot on the
 * machine, and save; the place and the cost center go through Setup's own
 * route, so the change is audited there and refused there to anyone else.
 * Moving works where machines are drawn at their true size (`layout.movable`)
 * and on a machine whose frame the MES knows (`station.frame`): a click on a
 * shape drawn bigger than life, or on a machine nobody placed, would land in
 * metres that are not on the plant.
 *
 * The numbers come from the API. All this does is turn the view's metres
 * into the plant's and back, with the frame the API sent.
 */

import * as THREE from "three";

import { footprint } from "./parts.js";

const PLAN_SCALE = 2.5; // as scene.js draws a floor plan
const CLICK_SLOP = 6; // pixels a pointer may wander and still be a click
const COLOURS = { ok: 0x4cc3ff, repair: 0xff3b3b };

const $ = (selector) => document.querySelector(selector);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}

const metres = (v) => (typeof v === "number" ? `${v.toFixed(2)} m` : "not said");

/** Plant metres from a point in the view: the inverse of where the API put
 *  the part (`at` = plant place minus the frame, less the machine's centre). */
function toPlant(station, offset) {
  const f = station.frame;
  return {
    x: +(station.x + offset.x + f.x0).toFixed(2),
    y: +((station.z + offset.z) * f.y_sign + f.y0).toFixed(2),
    z: +(offset.y + f.z0).toFixed(2),
  };
}

export class Picker {
  constructor(scene, layout, { canMove = false } = {}) {
    this.scene = scene;
    this.layout = layout;
    this.canMove = canMove;
    this.scale = layout.plan ? PLAN_SCALE : 1;
    this.markers = new Map(); // part code -> { mesh, hit, part, station }
    this.repairs = new Map(); // equipment code -> [repair]
    this.picked = null; // { kind: "part"|"machine", code }
    this.moving = null; // { code, place } while a move waits for its click
    this.ray = new THREE.Raycaster();
    for (const station of layout.stations) {
      for (const repair of station.repairs || []) this.note(repair);
      for (const part of station.parts || []) for (const repair of part.repairs || []) this.note(repair);
    }
    this.build();
    this.wire();
    this.list();
  }

  note(repair) {
    if (!this.repairs.has(repair.equipment)) this.repairs.set(repair.equipment, []);
    this.repairs.get(repair.equipment).push(repair);
  }

  stationOf(code) {
    return this.layout.stations.find((s) => s.code === code);
  }

  /* ------------------------------------------------------------ markers */

  build() {
    const ring = new THREE.TorusGeometry(0.19, 0.045, 10, 28);
    const ball = new THREE.SphereGeometry(0.11, 16, 12);
    const hit = new THREE.SphereGeometry(0.3, 8, 6);
    const hitMaterial = new THREE.MeshBasicMaterial({ visible: false });
    for (const station of this.layout.stations) {
      const machine = this.scene.machines.get(station.code);
      if (!machine) continue;
      machine.group.userData.station = station.code;
      const parts = station.parts || [];
      const spots = parts.filter((p) => !p.at).length;
      let spot = 0;
      for (const part of parts) {
        const band = /band|heater/i.test(part.name);
        const mesh = new THREE.Mesh(band ? ring : ball, new THREE.MeshStandardMaterial({
          color: COLOURS.ok, emissive: COLOURS.ok, emissiveIntensity: 0.9, roughness: 0.4,
        }));
        mesh.renderOrder = 2;
        // A band rings the barrel, which runs along the machine.
        if (band) mesh.rotation.y = Math.PI / 2 + THREE.MathUtils.degToRad(station.rot || 0);
        const target = new THREE.Mesh(hit, hitMaterial);
        target.userData.part = part.code;
        mesh.add(target);
        mesh.scale.setScalar(this.scale);
        this.scene.scene.add(mesh);
        const marker = { mesh, part, station, spot: part.at ? null : spot++, spots };
        this.markers.set(part.code, marker);
        this.place(marker);
      }
    }
    this.colour();
  }

  /** The view's offset from the machine's centre a marker sits at: the
   *  part's own, or, for a part with no place on this view, a spot of its
   *  own in a row across the machine's top. */
  offsetOf(marker) {
    const { part, station } = marker;
    const height = footprint(station.kind).height;
    if (part.at) return { x: part.at.x, y: part.at.y ?? height + 0.3, z: part.at.z };
    return { x: (marker.spot - (marker.spots - 1) / 2) * 0.55, y: height + 0.35, z: 0 };
  }

  place(marker, offset = this.offsetOf(marker)) {
    const machine = this.scene.machines.get(marker.station.code);
    const at = machine.group.position;
    marker.mesh.position.set(at.x + offset.x * this.scale, offset.y * this.scale, at.z + offset.z * this.scale);
  }

  colour() {
    for (const [code, marker] of this.markers) {
      // Red says a repair is open, whether or not it is the one picked;
      // the one picked is drawn bigger instead.
      const hex = this.repairs.has(code) ? COLOURS.repair : COLOURS.ok;
      const picked = Boolean(this.picked && this.picked.code === code);
      marker.mesh.material.color.setHex(hex);
      marker.mesh.material.emissive.setHex(hex);
      marker.mesh.scale.setScalar(this.scale * (picked ? 1.6 : 1));
      marker.mesh.userData.repair = this.repairs.has(code);
      marker.mesh.userData.picked = picked;
    }
  }

  /** The feed's open repairs, twice a second: a band that has just failed
   *  turns red without a reload, and one closed turns back. */
  applyRepairs(repairs) {
    if (!Array.isArray(repairs)) return;
    const before = JSON.stringify([...this.repairs.entries()]);
    this.repairs = new Map();
    for (const repair of repairs) this.note(repair);
    if (JSON.stringify([...this.repairs.entries()]) === before) return;
    this.colour();
    this.list();
    if (this.picked && !this.moving) this.open(this.picked.kind, this.picked.code);
  }

  /* -------------------------------------------------------------- clicks */

  wire() {
    const canvas = this.scene.renderer.domElement;
    let down = null;
    canvas.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; });
    canvas.addEventListener("pointerup", (e) => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > CLICK_SLOP) return;
      down = null;
      this.click(e.clientX, e.clientY);
    });
    $("#pick-close").addEventListener("click", () => this.close());
    $("#pick-list-toggle").addEventListener("click", () => {
      $("#pick-list").classList.toggle("hidden");
    });
  }

  cast(x, y, objects, deep = true) {
    const box = this.scene.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((x - box.left) / box.width) * 2 - 1, -((y - box.top) / box.height) * 2 + 1);
    this.ray.setFromCamera(ndc, this.scene.camera);
    // What is drawn, only: a machine dressed in its plant's model keeps the
    // built-in shape under it, hidden, and a click must land on the model.
    const shown = (node) => { for (; node; node = node.parent) if (!node.visible) return false; return true; };
    return this.ray.intersectObjects(objects, deep).filter((hit) => shown(hit.object));
  }

  click(x, y) {
    if (this.moving) return this.moveTo(x, y);
    // The marker drawn under the pointer first; only then the larger, unseen
    // target round each one, which makes a small marker easy to hit but would
    // let a sensor tucked inside a band take the band's click.
    const meshes = [...this.markers.values()].map((m) => m.mesh);
    const [onMarker] = this.cast(x, y, meshes, false).concat(this.cast(x, y, meshes));
    if (onMarker) {
      let node = onMarker.object;
      while (node && !node.userData.part) node = node.children.find((c) => c.userData.part) || node.parent;
      const code = node ? node.userData.part : null;
      if (code) return this.open("part", code);
    }
    const groups = [...this.scene.machines.values()].map((m) => m.group);
    const [onMachine] = this.cast(x, y, groups);
    if (!onMachine) return;
    let node = onMachine.object;
    while (node && !node.userData.station) node = node.parent;
    if (node) this.open("machine", node.userData.station);
  }

  /* --------------------------------------------------------------- panel */

  open(kind, code) {
    this.picked = { kind, code };
    this.colour();
    const panel = $("#pick");
    const body = $("#pick-body");
    if (kind === "part") {
      const { part, station } = this.markers.get(code);
      body.replaceChildren(...this.partView(part, station));
    } else {
      body.replaceChildren(...this.machineView(this.stationOf(code)));
    }
    $("#pick-title").textContent = kind === "part" ? this.markers.get(code).part.name : this.stationOf(code).name;
    panel.dataset.code = code;
    panel.dataset.kind = kind;
    panel.classList.remove("hidden");
    $("#pick-list").classList.add("hidden");
  }

  close() {
    this.picked = null;
    this.cancelMove();
    $("#pick").classList.add("hidden");
    this.colour();
  }

  row(label, ...value) {
    return el("div", { class: "pick-row" }, el("span", { class: "pick-label" }, label), el("span", {}, ...value));
  }

  costCentre(node) {
    if (!node.cost_center) return "none said, here or above";
    return node.cost_center_from ? `${node.cost_center} · from ${node.cost_center_from}` : `${node.cost_center} · its own`;
  }

  repairsView(repairs, machine) {
    if (!repairs.length) return [this.row("Repair", "none open")];
    const href = `/dashboard/maintenance?equipment=${encodeURIComponent(machine)}#work`;
    return [this.row(`Repair${repairs.length > 1 ? `s (${repairs.length})` : ""}`,
      ...repairs.map((r) => el("a", { class: "pick-repair", href, target: "_top" },
        `${r.order} · ${r.summary} · ${r.status.replace("_", " ")}`)))];
  }

  placeText(part) {
    if (!part.place) return "no place held — drawn on a spot of its own on the machine";
    const text = `x ${metres(part.place.x)}, y ${metres(part.place.y)}, z ${metres(part.place.z)}`;
    return part.placed === "default" ? `${text} — drawn on a spot of its own: this view cannot put plant metres on this machine` : text;
  }

  partView(part, station) {
    const out = [
      this.row("What", part.what),
      this.row("Code", part.code),
      this.row("Place", el("span", { id: "pick-place" }, this.placeText(part))),
      this.row("Pays", this.costCentre(part)),
      ...this.repairsView(this.repairs.get(part.code) || [], station.code),
    ];
    if (!this.canMove) return out;
    const why = !this.layout.movable
      ? "Move works on the one-line view, where machines are drawn at their true size."
      : !station.frame ? "This machine has no place on the plant, so a click cannot be turned into metres. Type them in Setup." : null;
    const cc = el("input", { id: "pick-cc", type: "text", maxlength: "40", autocomplete: "off",
      placeholder: part.cost_center_from ? `${part.cost_center} from ${part.cost_center_from}` : "" });
    cc.value = part.own_cost_center || "";
    const move = el("button", { id: "pick-move", type: "button" }, "Move");
    const save = el("button", { id: "pick-save", type: "button", class: "primary" }, "Save");
    const cancel = el("button", { id: "pick-cancel", type: "button" }, "Cancel");
    const note = el("p", { id: "pick-note", class: "pick-note" }, why || "");
    if (why) move.disabled = true;
    move.addEventListener("click", () => this.startMove(part.code));
    cancel.addEventListener("click", () => { this.cancelMove(); this.open("part", part.code); });
    save.addEventListener("click", () => this.save(part.code));
    out.push(el("div", { class: "pick-edit" },
      el("label", { for: "pick-cc" }, "Own cost center (empty: inherit)"), cc,
      el("div", { class: "pick-buttons" }, move, save, cancel), note));
    return out;
  }

  machineView(station) {
    const parts = station.parts || [];
    const own = this.repairs.get(station.code) || [];
    const onParts = parts.reduce((n, p) => n + (this.repairs.get(p.code) || []).length, 0);
    const out = [
      this.row("What", station.what || station.name),
      this.row("Code", station.code),
      this.row("Place", station.place
        ? `x ${metres(station.place.x)}, y ${metres(station.place.y)}`
        : station.placed === "drawn" ? "where the plant's drawing puts it" : "not held; drawn in routing order"),
      this.row("Pays", this.costCentre(station)),
      ...this.repairsView(own, station.code),
    ];
    if (onParts) out.push(this.row("On its parts", `${onParts} repair${onParts > 1 ? "s" : ""} open`));
    out.push(this.row(`Parts (${parts.length})`, ...(parts.length ? parts.map((p) => {
      const b = el("button", { type: "button", class: "pick-part" }, p.name);
      b.addEventListener("click", () => this.open("part", p.code));
      return b;
    }) : ["none held"])));
    return out;
  }

  /* ---------------------------------------------------------------- move */

  startMove(code) {
    this.moving = { code, place: null };
    $("#pick-note").textContent = "Click the spot on the machine where it really is.";
    document.body.classList.add("pick-moving");
  }

  cancelMove() {
    if (this.moving) {
      const marker = this.markers.get(this.moving.code);
      if (marker) this.place(marker);
    }
    this.moving = null;
    document.body.classList.remove("pick-moving");
  }

  moveTo(x, y) {
    const marker = this.markers.get(this.moving.code);
    const machine = this.scene.machines.get(marker.station.code);
    const [hit] = this.cast(x, y, [machine.group]);
    if (!hit) {
      $("#pick-note").textContent = `That missed ${marker.station.name}. Click on the machine itself.`;
      return;
    }
    const at = machine.group.position;
    const offset = { x: (hit.point.x - at.x) / this.scale, y: hit.point.y / this.scale, z: (hit.point.z - at.z) / this.scale };
    this.moving.place = toPlant(marker.station, offset);
    this.moving.offset = offset;
    this.place(marker, offset);
    const p = this.moving.place;
    $("#pick-place").textContent = `x ${metres(p.x)}, y ${metres(p.y)}, z ${metres(p.z)} — not saved yet`;
    $("#pick-note").textContent = "Save to keep it, or click again to put it somewhere else.";
  }

  async save(code) {
    const marker = this.markers.get(code);
    const { part } = marker;
    const body = {};
    const cc = $("#pick-cc").value.trim();
    if (cc !== (part.own_cost_center || "")) body.cost_center = cc;
    if (this.moving && this.moving.code === code && this.moving.place) Object.assign(body, this.moving.place);
    if (!Object.keys(body).length) {
      $("#pick-note").textContent = "Nothing has changed.";
      return;
    }
    try {
      const saved = await window.FS.api(`/masterdata/equipment/${encodeURIComponent(code)}`, { method: "PUT", body });
      part.own_cost_center = saved.cost_center;
      part.cost_center = saved.bills_to;
      part.cost_center_from = saved.bills_from;
      if (body.x !== undefined) {
        part.place = { x: saved.x, y: saved.y, z: saved.z };
        part.at = this.moving.offset;
        part.placed = "held";
      }
      this.moving = null;
      document.body.classList.remove("pick-moving");
      this.place(marker);
      window.FS.toast(saved.changed.length ? `${part.name}: saved` : `${part.name}: already so`);
      this.open("part", code);
    } catch (error) {
      $("#pick-note").textContent = `Not saved: ${error.message || error}`;
    }
  }

  /* -------------------------------------------- the list, for a phone */

  list() {
    const stations = this.layout.stations.filter((s) => (s.parts || []).length);
    const total = stations.reduce((n, s) => n + s.parts.length, 0);
    const toggle = $("#pick-list-toggle");
    toggle.textContent = `Parts (${total})`;
    toggle.classList.toggle("hidden", total === 0);
    const host = $("#pick-list");
    const head = el("p", { class: "pick-list-total" },
      `${total} part${total === 1 ? "" : "s"} on ${stations.length} of ${this.layout.stations.length} machines`);
    const groups = stations.map((station) => {
      const machine = el("button", { type: "button", class: "pick-list-machine" }, station.name);
      machine.addEventListener("click", () => this.open("machine", station.code));
      const items = station.parts.map((part) => {
        const b = el("button", { type: "button", class: "pick-list-part", "data-code": part.code }, part.name);
        if (this.repairs.has(part.code)) b.classList.add("repair");
        b.addEventListener("click", () => this.open("part", part.code));
        return el("li", {}, b);
      });
      return el("div", { class: "pick-list-group" }, machine, el("ul", {}, ...items));
    });
    host.replaceChildren(head, ...groups);
  }
}
