/* The scene: a plant bay with a line running through it.
 *
 * Built entirely from the layout the API returns, so pointing this at a real
 * plant is the same change as pointing the agent at real PLCs — data, not code.
 * A line nobody has drawn still renders: stations come back in routing order
 * with a shape inferred from each machine's name.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

import {
  BELT_HEIGHT,
  LABEL_ASPECT,
  MATERIALS,
  PALLET_DECK,
  STATE_COLOURS,
  bottleGeometry,
  drawsBottles,
  footprint,
  makeCase,
  makeConveyor,
  makeLabel,
  makeMachine,
  makePallet,
  makeTray,
  sizeLabel,
  slabGeometry,
} from "./parts.js";
import { dressWithModel } from "./models.js";
import { RobotArm } from "./robot.js";

const BELT_TOP = BELT_HEIGHT + 0.025;
const TRAY_LINGER = 0.45; // seconds a placed tray stays before the denester takes it
const CASES_PER_LAYER = 4;
const LAYERS_PER_PALLET = 4;
// A floor plan draws each machine this many times its line-view size: a 3 m
// cabinet becomes about the 7-10 m footprint of a building on a real floor.
const PLAN_SCALE = 2.5;
// The view looks down at this angle, and from the front-left at whichever
// of these bearings (degrees off square-on) draws the line biggest.
const ELEVATION = THREE.MathUtils.degToRad(30);
const BEARINGS = [10, 20, 30, 40, 50, 60].map((d) => THREE.MathUtils.degToRad(d));
const LABEL_LIFT = 1.15; // metres between a machine's top and its label

/** Half a station's reach along a direction `angle` (radians, as rotation.y):
 *  where a belt leaving that way meets the machine. At rot 0 along the line
 *  it is half the machine's length, at rot 90 half its width. */
function halfAlong(station, angle) {
  const { length, width } = footprint(station.kind);
  const a = THREE.MathUtils.degToRad(station.rot || 0) - angle;
  return Math.abs(Math.cos(a)) * length / 2 + Math.abs(Math.sin(a)) * width / 2;
}

export class LineScene {
  constructor(canvas) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0e1116);
    // Fog does the work a real bay's air does: it tells you the far end of the
    // line is far away, which a flat dark background never manages. Its range
    // is set from the line's own length in build(), because fogging a two-machine
    // line with the same numbers as a six-station one washes it out entirely.
    this.scene.fog = new THREE.Fog(0x0e1116, 60, 260);

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 400);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;
    this.controls.maxPolarAngle = Math.PI * 0.49; // never go under the floor
    this.controls.minDistance = 6;
    this.controls.maxDistance = 160;
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = 0.28;
    // A wall display should drift; a person driving it should not be fought.
    this.controls.addEventListener("start", () => {
      this.controls.autoRotate = false;
      this.touched = true;
    });

    this.lights();
    this.machines = new Map();
    this.beltViews = [];
    this.robots = {};
    this.labels = [];
    this.clock = 0;
    // What a person cannot see the floor through: the header, the station
    // cards, the footer. app.js measures them; the fit keeps labels clear.
    this.insets = () => ({ top: 0, right: 0, bottom: 0, left: 0 });
    this.fitted = null;

    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  lights() {
    this.scene.add(new THREE.HemisphereLight(0x9dc0ff, 0x161a21, 1.1));

    const key = new THREE.DirectionalLight(0xffffff, 2.4);
    key.position.set(-24, 40, 26);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0006;
    key.shadow.normalBias = 0.02;
    this.key = key;
    this.scene.add(key);
    this.scene.add(key.target);

    const fill = new THREE.DirectionalLight(0x7fa8d8, 0.55);
    fill.position.set(30, 22, -30);
    this.scene.add(fill);
  }

  floor(extent, mid = new THREE.Vector3(extent / 2, 0, 0), depth = 0) {
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(extent + 140, depth + 150),
      new THREE.MeshStandardMaterial({ color: 0x151a22, roughness: 0.96, metalness: 0.02 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(mid.x, 0, mid.z);
    floor.receiveShadow = true;
    this.scene.add(floor);

    const gridSize = Math.max(220, Math.ceil((Math.max(extent, depth) + 60) / 2) * 2);
    const grid = new THREE.GridHelper(gridSize, gridSize / 2, 0x243044, 0x1a212b);
    grid.position.set(mid.x, 0.008, mid.z);
    grid.material.transparent = true;
    grid.material.opacity = 0.5;
    this.scene.add(grid);

    // Painted lane markings. Two yellow lines either side of the line is the
    // cheapest thing on screen and does more for "this is a factory" than any
    // amount of machine detail.
    const paint = new THREE.MeshStandardMaterial({ color: 0xd8a72a, roughness: 0.85, metalness: 0 });
    // Only along a straight run: across a drawn floor they would cut through it.
    if (depth > 0.5) return;
    for (const z of [-3.6, 3.6]) {
      const stripe = new THREE.Mesh(new THREE.PlaneGeometry(extent + 16, 0.14), paint);
      stripe.rotation.x = -Math.PI / 2;
      stripe.position.set(mid.x, 0.012, mid.z + z);
      this.scene.add(stripe);
    }
  }

  /** A lighter patch of floor under a station, edged, so a dark machine on
   *  a dark floor still reads as an object standing on something. */
  pad(station, x) {
    const { length, width } = footprint(station.kind);
    const group = new THREE.Group();
    const w = length + 1.6, d = width + 1.6;
    const patch = new THREE.Mesh(
      new THREE.PlaneGeometry(w, d),
      new THREE.MeshStandardMaterial({ color: 0x2c3644, roughness: 0.9, metalness: 0.02 }),
    );
    patch.rotation.x = -Math.PI / 2;
    patch.position.y = 0.01;
    patch.receiveShadow = true;
    group.add(patch);
    const edge = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-w / 2, 0.015, -d / 2), new THREE.Vector3(w / 2, 0.015, -d / 2),
        new THREE.Vector3(w / 2, 0.015, d / 2), new THREE.Vector3(-w / 2, 0.015, d / 2),
      ]),
      new THREE.LineBasicMaterial({ color: 0x6b7b92 }),
    );
    group.add(edge);
    group.position.set(x, 0, station.z);
    group.rotation.y = THREE.MathUtils.degToRad(station.rot || 0);
    this.scene.add(group);
  }

  /** Build the whole line from a `/line/layout` payload. */
  build(layout) {
    if (layout.plan) return this.buildPlan(layout);
    this.layout = layout;
    const stations = layout.stations;
    this.origin = stations[0].x;
    const xs = stations.map((s) => s.x - this.origin);
    const zs = stations.map((s) => s.z);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minZ = Math.min(...zs), maxZ = Math.max(...zs);
    const extent = stations.length > 1 ? Math.max(1, maxX - minX) : 12;

    this.floor(extent, new THREE.Vector3((minX + maxX) / 2, 0, (minZ + maxZ) / 2), maxZ - minZ);

    // Machines and labels.
    stations.forEach((station) => {
      const x = station.x - this.origin;
      this.pad(station, x);
      const machine = makeMachine(station.kind);
      machine.group.position.set(x, 0, station.z);
      machine.group.rotation.y = THREE.MathUtils.degToRad(station.rot || 0);
      this.scene.add(machine.group);

      const label = makeLabel(station.code, station.name);
      label.position.set(x, machine.height + LABEL_LIFT, station.z);
      this.scene.add(label);
      this.labels.push(label);

      const entry = { ...machine, station, state: "unknown" };
      this.machines.set(station.code, entry);
      // The plant's own model, if its pack has one: drawn over this shape
      // when it arrives, and this shape stays if it does not.
      dressWithModel(entry);
    });

    // Conveyors, each from one station to the next whichever way that is,
    // and one instanced pool of units per belt.
    const bottles = drawsBottles(layout.unit && layout.unit.label);
    const fillerIndex = bottles
      ? stations.findIndex((s) => s.kind === "filler" || s.kind === "mixer") : -1;
    const runs = [];
    for (let i = 0; i < stations.length - 1; i += 1) {
      const from = stations[i];
      const to = stations[i + 1];
      const ax = from.x - this.origin, bx = to.x - this.origin;
      const dx = bx - ax, dz = to.z - from.z;
      const dist = Math.hypot(dx, dz) || 1e-6;
      const ux = dx / dist, uz = dz / dist;
      const angle = Math.atan2(-uz, ux);
      const out = halfAlong(from, angle);
      const length = Math.max(1.5, dist - out - halfAlong(to, angle));
      runs.push({ startX: ax + ux * out, z: from.z + uz * out, ux, uz, angle, length });
    }
    // A slab as long as a car floor pan, unless a full belt's worth of them
    // would not fit end to end on the shortest belt: then shorter, to the pitch.
    const shortest = runs.length ? Math.min(...runs.map((r) => r.length)) : 5;
    const pitch = shortest / Math.max(2, (layout.conveyor && layout.conveyor.capacity) || 3);
    const unitGeometry = bottles ? bottleGeometry() : slabGeometry(Math.max(0.8, Math.min(4, pitch * 0.85)));
    this.bottles = bottles;
    this.spacings = [];
    runs.forEach((run, i) => {
      this.spacings.push(run.length);
      const conveyor = makeConveyor(run.length);
      conveyor.position.set(run.startX, 0, run.z);
      conveyor.rotation.y = run.angle;
      this.scene.add(conveyor);

      const filled = fillerIndex >= 0 && i >= fillerIndex;
      const material = !bottles ? MATERIALS.bodyInWhite : filled ? MATERIALS.glassFull : MATERIALS.glassEmpty;
      const pool = new THREE.InstancedMesh(unitGeometry, material, 160);
      pool.castShadow = true;
      pool.receiveShadow = true;
      pool.count = 0;
      pool.frustumCulled = false;
      this.scene.add(pool);

      this.beltViews.push({ pool, ...run, dummy: new THREE.Object3D() });
    });

    this.buildRobots(stations);
    this.fitPoints = this.lineFitPoints(stations);
    this.frame();
    return this;
  }

  /** The points the first view must hold in shot: each station's footprint
   *  corners at floor and roof height, the line's ends half a machine
   *  further out, and the label anchors (kept clear by a label's size). */
  lineFitPoints(stations) {
    const corners = [];
    const anchors = [];
    let reach = 0;
    for (const station of stations) {
      const { length, width, height } = footprint(station.kind);
      reach = Math.max(reach, length / 2);
      const x = station.x - this.origin;
      const r = THREE.MathUtils.degToRad(station.rot || 0);
      const c = Math.cos(r), s = Math.sin(r);
      for (const [lx, lz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
        const px = lx * length / 2, pz = lz * width / 2;
        // rotation.y turns +x toward -z
        const wx = x + px * c + pz * s, wz = station.z - px * s + pz * c;
        corners.push(new THREE.Vector3(wx, 0, wz), new THREE.Vector3(wx, height, wz));
      }
      anchors.push(new THREE.Vector3(x, height + LABEL_LIFT, station.z));
    }
    const box = new THREE.Box3().setFromPoints(corners);
    box.min.x -= reach; box.max.x += reach;
    box.min.z -= reach; box.max.z += reach;
    const ends = [];
    for (const x of [box.min.x, box.max.x]) {
      for (const z of [box.min.z, box.max.z]) {
        for (const y of [0, box.max.y]) ends.push(new THREE.Vector3(x, y, z));
      }
    }
    return { points: ends, anchors, box };
  }

  /** A floor plan: every machine where the MES holds it to stand.
   *
   *  No belts and no robots. The MES knows where a machine stands, not what
   *  runs between it and the next one, and a conveyor drawn from routing
   *  order across a real floor would cross it in straight lines that are
   *  not there. The machines are drawn at PLAN_SCALE because a plan this
   *  size shows real buildings, not 3 m cabinets; the height a machine
   *  stands at is in its label ("+34 m" above the lowest), never in where
   *  it is drawn.
   */
  buildPlan(layout) {
    this.layout = layout;
    this.origin = 0;
    const stations = layout.stations;
    // The camera frames the machines that stand somewhere. Those with no
    // known position wait in a row past the edge, a turn of the view away.
    const placed = stations.filter((s) => s.placed !== "routing");
    const framed = placed.length ? placed : stations;
    const xs = framed.map((s) => s.x);
    const zs = framed.map((s) => s.z);
    const allZ = stations.map((s) => s.z);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minZ = Math.min(...zs), maxZ = Math.max(...zs);
    const width = Math.max(12, maxX - minX);
    const depth = Math.max(12, maxZ - minZ);
    const extent = Math.max(width, depth);
    const mid = new THREE.Vector3((minX + maxX) / 2, 0, (minZ + maxZ) / 2);

    // The floor and grid reach the waiting row as well.
    const floorDepth = Math.max(...allZ) - Math.min(...allZ, minZ) + 80;
    const floorMidZ = (Math.min(...allZ, minZ) + Math.max(...allZ)) / 2;
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(width + 80, Math.max(depth + 80, floorDepth)),
      new THREE.MeshStandardMaterial({ color: 0x151a22, roughness: 0.96, metalness: 0.02 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(mid.x, 0, floorMidZ);
    floor.receiveShadow = true;
    this.scene.add(floor);

    // One cell is 10 m, so the grid is a scale bar as well as a floor.
    const size = Math.ceil((Math.max(extent + 80, floorDepth)) / 10) * 10;
    const grid = new THREE.GridHelper(size, size / 10, 0x2c3a52, 0x1d2530);
    grid.position.set(mid.x, 0.008, floorMidZ);
    grid.material.transparent = true;
    grid.material.opacity = 0.6;
    this.scene.add(grid);

    const heights = stations.map((s) => s.height).filter((h) => typeof h === "number");
    const ground = heights.length ? Math.min(...heights) : null;

    stations.forEach((station) => {
      const machine = makeMachine(station.kind);
      machine.group.scale.setScalar(PLAN_SCALE);
      machine.group.position.set(station.x, 0, station.z);
      machine.group.rotation.y = THREE.MathUtils.degToRad(station.rot || 0);
      this.scene.add(machine.group);

      const rise = ground !== null && typeof station.height === "number"
        ? Math.round(station.height - ground) : 0;
      const note = station.placed === "routing" ? "position unknown" : rise >= 1 ? `+${rise} m` : "";
      // The label's second line holds 30 characters; the note is the part
      // that must not be cut off.
      const name = station.name || "";
      const label = makeLabel(station.code, note ? `${name.slice(0, Math.max(0, 27 - note.length))} · ${note}` : name);
      label.position.set(station.x, machine.height * PLAN_SCALE + 3.2, station.z);
      this.scene.add(label);
      this.labels.push(label);

      const entry = { ...machine, station, state: "unknown" };
      this.machines.set(station.code, entry);
      // The plant's own model, if its pack has one: drawn over this shape
      // when it arrives, and this shape stays if it does not.
      dressWithModel(entry);
    });

    this.spacings = [];
    this.framePlan(mid, extent);
    return this;
  }

  /** The whole plan in shot, from one corner, high enough to see past the
   *  near buildings to the far ones. */
  framePlan(mid, extent) {
    for (const label of this.labels) sizeLabel(label, this.camera, window.innerHeight, this.labelPixels());
    const centre = new THREE.Vector3(mid.x, 2, mid.z);
    this.controls.target.copy(centre);
    const distance = Math.max(30, extent * 0.62);
    this.camera.far = Math.max(400, distance * 6);
    this.camera.updateProjectionMatrix();
    this.controls.maxDistance = Math.max(160, distance * 3);
    this.camera.position.set(centre.x - distance * 0.45, distance * 0.62, centre.z + distance * 0.72);
    this.camera.lookAt(centre);
    this.key.target.position.copy(centre);

    this.scene.fog.near = Math.max(60, distance * 1.4);
    this.scene.fog.far = Math.max(260, distance * 4);

    const reach = extent * 0.7 + 30;
    Object.assign(this.key.shadow.camera, { left: -reach, right: reach, top: reach, bottom: -reach, near: 1, far: reach * 4 });
    this.key.position.set(centre.x - reach * 0.6, reach * 1.2, centre.z + reach * 0.7);
    this.key.shadow.camera.updateProjectionMatrix();

    this.controls.update();
  }

  buildRobots(stations) {
    const first = stations[0];
    const last = stations[stations.length - 1];

    if (first.kind === "depalletiser" && this.beltViews.length) {
      const belt = this.beltViews[0];
      const arm = new RobotArm(new THREE.Vector3(first.x - this.origin - 0.4, 0, first.z - 2.5), -Math.PI / 2);
      this.scene.add(arm.group);

      const pallet = makePallet();
      pallet.position.set(first.x - this.origin - 1.4, 0, first.z - 4.6);
      this.scene.add(pallet);

      this.robots.infeed = {
        arm,
        pallet,
        pending: 0,
        stack: [],
        placeAt: new THREE.Vector3(belt.startX + 0.55, BELT_TOP + 0.05, belt.z),
        linger: [],
      };
      this.restockInfeed();
    }

    if (last.kind === "palletiser" && this.beltViews.length) {
      const belt = this.beltViews[this.beltViews.length - 1];
      const arm = new RobotArm(new THREE.Vector3(last.x - this.origin - 0.3, 0, last.z + 2.5), Math.PI / 2);
      this.scene.add(arm.group);

      const pallet = makePallet();
      pallet.position.set(last.x - this.origin + 1.3, 0, last.z + 4.3);
      this.scene.add(pallet);

      this.robots.outfeed = {
        arm,
        pallet,
        pending: 0,
        cases: 0,
        shipping: null,
        pickAt: new THREE.Vector3(belt.startX + belt.length - 0.5, BELT_TOP + 0.08, belt.z),
      };
    }
  }

  /** Refill the depalletiser's infeed with a fresh stack of trays. */
  restockInfeed() {
    const infeed = this.robots.infeed;
    if (!infeed) return;
    const perTray = this.layout.unit.per_tray;
    for (let layer = 0; layer < 5; layer += 1) {
      const tray = makeTray(perTray);
      tray.position.set(
        infeed.pallet.position.x,
        PALLET_DECK + layer * 0.16,
        infeed.pallet.position.z,
      );
      this.scene.add(tray);
      infeed.stack.push(tray);
    }
  }

  /** The label height on screen, in pixels: big enough to read on a phone,
   *  never so big that a line of eight overlaps itself on a laptop. */
  labelPixels() {
    const width = window.innerWidth, height = window.innerHeight;
    return Math.round(Math.min(44, Math.max(30, Math.min(width, height) * 0.045)));
  }

  /** Where on screen the line may be drawn, in normalised device units,
   *  after the chrome over the canvas and a margin are taken off. */
  safeArea() {
    const width = window.innerWidth, height = window.innerHeight;
    const inset = this.insets();
    const margin = 0.04;
    const toX = (px) => (px / width) * 2 - 1;
    const toY = (px) => 1 - (px / height) * 2;
    return {
      left: toX(inset.left) + margin,
      right: toX(width - inset.right) - margin,
      top: toY(inset.top) - margin,
      bottom: toY(height - inset.bottom) + margin,
    };
  }

  /** Does everything in `fit` land inside `area` from the camera as it is? */
  holds(fit, area, labelW, labelH) {
    this.camera.updateMatrixWorld();
    const v = new THREE.Vector3();
    // In front of the camera, then on screen; how far away is the far
    // plane's business, set from the distance this finds.
    const ahead = (p) => v.copy(p).applyMatrix4(this.camera.matrixWorldInverse).z < -this.camera.near
      && Boolean(v.copy(p).project(this.camera));
    for (const p of fit.points) {
      if (!ahead(p)) return false;
      if (v.x < area.left || v.x > area.right || v.y < area.bottom || v.y > area.top) return false;
    }
    for (const p of fit.anchors) {
      if (!ahead(p)) return false;
      if (v.x - labelW / 2 < area.left || v.x + labelW / 2 > area.right) return false;
      if (v.y - labelH / 2 < area.bottom || v.y + labelH / 2 > area.top) return false;
    }
    return true;
  }

  /** The nearest distance along `dir` from `target` at which `fit` is held. */
  distanceFor(fit, target, dir, area, labelW, labelH) {
    let lo = 1, hi = 4000;
    const place = (d) => {
      this.camera.position.copy(target).addScaledVector(dir, d);
      this.camera.lookAt(target);
    };
    place(hi);
    if (!this.holds(fit, area, labelW, labelH)) return hi;
    for (let i = 0; i < 40; i += 1) {
      const mid = (lo + hi) / 2;
      place(mid);
      if (this.holds(fit, area, labelW, labelH)) hi = mid; else lo = mid;
    }
    return hi;
  }

  /** Aim the camera so the whole line is in shot, labels and all.
   *
   *  The camera looks at the middle of the machines from the front-left,
   *  thirty degrees down, from whichever bearing draws the line biggest in
   *  this viewport - nearly square-on for a long line on a wide screen,
   *  more along it on a phone held upright - and as close as it can come
   *  with every machine and every label inside the part of the screen the
   *  cards and the header leave free. Seen from the floor at one end a line
   *  is a factory; the far stations may be smaller, their labels are not.
   */
  frame() {
    const fit = this.fitPoints;
    this.camera.updateProjectionMatrix();
    const area = this.safeArea();
    const height = window.innerHeight, width = window.innerWidth;
    const px = this.labelPixels();
    const labelH = (2 * px) / height;
    const labelW = (2 * px * LABEL_ASPECT) / width;
    for (const label of this.labels) sizeLabel(label, this.camera, height, px);

    const centre = fit.box.getCenter(new THREE.Vector3());
    centre.y = Math.min(1.4, fit.box.max.y / 2);
    let best = null;
    for (const bearing of BEARINGS) {
      const dir = new THREE.Vector3(
        -Math.sin(bearing) * Math.cos(ELEVATION), Math.sin(ELEVATION), Math.cos(bearing) * Math.cos(ELEVATION));
      // Twice: the second time with the target moved so the line sits in the
      // middle of the free area rather than the middle of the window.
      let target = centre.clone();
      let distance = this.distanceFor(fit, target, dir, area, labelW, labelH);
      for (let pass = 0; pass < 2; pass += 1) {
        this.camera.position.copy(target).addScaledVector(dir, distance);
        this.camera.lookAt(target);
        this.camera.updateMatrixWorld();
        const shot = fit.points.concat(fit.anchors).map((p) => p.clone().project(this.camera));
        const midX = (Math.min(...shot.map((v) => v.x)) + Math.max(...shot.map((v) => v.x))) / 2;
        const midY = (Math.min(...shot.map((v) => v.y)) + Math.max(...shot.map((v) => v.y))) / 2;
        const wantX = (area.left + area.right) / 2, wantY = (area.top + area.bottom) / 2;
        const half = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * distance;
        const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
        const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 1);
        target = target.clone()
          .addScaledVector(right, (midX - wantX) * half * this.camera.aspect)
          .addScaledVector(up, (midY - wantY) * half);
        distance = this.distanceFor(fit, target, dir, area, labelW, labelH);
      }
      if (!best || distance < best.distance * 0.97) best = { distance, dir, target };
    }

    const { distance, dir, target } = best;
    this.camera.position.copy(target).addScaledVector(dir, distance);
    this.camera.lookAt(target);
    this.controls.target.copy(target);
    this.camera.far = Math.max(400, distance * 6);
    this.camera.updateProjectionMatrix();
    this.controls.maxDistance = Math.max(160, distance * 2.5);
    this.key.target.position.copy(target);
    this.fitted = { width, height };

    // Fog starts past the far end of the line, so it gives depth and hides nothing.
    const size = fit.box.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.z);
    this.scene.fog.near = Math.max(30, distance + span * 0.6);
    this.scene.fog.far = Math.max(140, this.scene.fog.near + span * 3);

    // Shadow frustum has to cover the line, or half of it loses its shadows.
    const reach = span * 0.75 + 20;
    Object.assign(this.key.shadow.camera, { left: -reach, right: reach, top: reach, bottom: -reach, near: 1, far: 200 });
    this.key.position.set(target.x - 26, 44, target.z + 30);
    this.key.shadow.camera.updateProjectionMatrix();

    this.controls.update();
  }

  /** Recolour machines from live MES state. */
  applyStates(stations) {
    for (const station of stations) {
      const machine = this.machines.get(station.code);
      if (!machine) continue;
      machine.state = station.state;
      const colour = STATE_COLOURS[station.state] ?? STATE_COLOURS.unknown;
      for (const band of machine.bands) {
        band.material.color.setHex(colour);
        band.material.emissive.setHex(colour);
      }
    }
  }

  update(dt, flow) {
    this.clock += dt;

    for (const machine of this.machines.values()) {
      const running = machine.state === "running";
      if (machine.spin && running) machine.spin.rotation.y += dt * 0.9;
      // A stopped machine's beacon breathes. Static red reads as a rendering
      // artefact; a pulse reads as an alarm, which is what it is.
      const pulse = machine.state === "down" ? 1.1 + Math.sin(this.clock * 4.5) * 0.75 : 1.25;
      for (const band of machine.bands) band.material.emissiveIntensity = pulse;
    }

    this.updateBelts(flow);
    this.updateInfeed(dt, flow);
    this.updateOutfeed(dt, flow);
  }

  updateBelts(flow) {
    for (let i = 0; i < this.beltViews.length; i += 1) {
      const view = this.beltViews[i];
      const belt = flow.belts[i];
      const { pool, dummy } = view;
      const count = Math.min(belt.items.length, pool.instanceMatrix.count);
      for (let k = 0; k < count; k += 1) {
        const item = belt.items[k];
        // A deterministic wobble off the centre line, so a queue of identical
        // bottles does not read as a single extruded shape.
        // A body rides square on its carrier.
        const wobble = this.bottles ? ((item.id * 37) % 100) / 100 - 0.5 : 0;
        const along = item.s * view.length;
        dummy.position.set(
          view.startX + view.ux * along - view.uz * wobble * 0.08,
          BELT_TOP,
          view.z + view.uz * along + view.ux * wobble * 0.08,
        );
        dummy.rotation.y = this.bottles ? ((item.id * 53) % 100) / 100 * Math.PI * 2 : view.angle;
        dummy.updateMatrix();
        pool.setMatrixAt(k, dummy.matrix);
      }
      pool.count = count;
      pool.instanceMatrix.needsUpdate = true;
    }
  }

  /** The depalletiser: hold this station's booked units back until there are a
   *  tray's worth, then have the arm carry them onto the belt. The bottles that
   *  ride away are exactly the ones the MES counted. */
  updateInfeed(dt, flow) {
    const infeed = this.robots.infeed;
    if (!infeed) return;
    infeed.arm.update(dt);

    infeed.pending += flow.drain(this.layout.stations[0].code);

    for (const entry of infeed.linger) entry.left -= dt;
    for (const entry of infeed.linger.filter((e) => e.left <= 0)) {
      this.scene.remove(entry.tray);
      infeed.linger.splice(infeed.linger.indexOf(entry), 1);
    }

    const perTray = this.layout.unit.per_tray;
    if (infeed.arm.busy || infeed.pending < perTray) return;
    if (!infeed.stack.length) this.restockInfeed();

    const tray = infeed.stack.pop();
    const from = new THREE.Vector3();
    tray.getWorldPosition(from);
    infeed.pending -= perTray;

    infeed.arm.moveItem(from, infeed.placeAt, {
      duration: 2.4,
      onPick: () => infeed.arm.grasp(tray),
      onPlace: () => {
        infeed.arm.release(tray, this.scene, infeed.placeAt);
        // The denester lifts the bottles out; the tray goes back empty.
        flow.belts[0].spawn(perTray);
        infeed.linger.push({ tray, left: TRAY_LINGER });
      },
    });
  }

  /** The palletiser: every tray's worth of units it books becomes a case, lifted
   *  off the belt onto a pallet that grows layer by layer and ships when full. */
  updateOutfeed(dt, flow) {
    const outfeed = this.robots.outfeed;
    if (!outfeed) return;
    outfeed.arm.update(dt);

    const stations = this.layout.stations;
    outfeed.pending += flow.drain(stations[stations.length - 1].code);

    if (outfeed.shipping) {
      outfeed.shipping.travelled += dt * 1.6;
      outfeed.shipping.group.position.z += dt * 1.6;
      if (outfeed.shipping.travelled > 9) {
        this.scene.remove(outfeed.shipping.group);
        outfeed.shipping = null;
      }
    }

    const perTray = this.layout.unit.per_tray;
    if (outfeed.arm.busy || outfeed.pending < perTray) return;
    outfeed.pending -= perTray;

    const unit = makeCase();
    const slot = outfeed.cases % CASES_PER_LAYER;
    const layer = Math.floor(outfeed.cases / CASES_PER_LAYER);
    const to = new THREE.Vector3(
      outfeed.pallet.position.x + (slot % 2 === 0 ? -0.27 : 0.27),
      PALLET_DECK + layer * 0.28,
      outfeed.pallet.position.z + (slot < 2 ? -0.19 : 0.19),
    );

    outfeed.arm.moveItem(outfeed.pickAt, to, {
      duration: 2.2,
      onPick: () => {
        this.scene.add(unit);
        unit.position.copy(outfeed.pickAt);
        outfeed.arm.grasp(unit);
      },
      onPlace: () => {
        outfeed.arm.release(unit, outfeed.pallet, new THREE.Vector3(
          to.x - outfeed.pallet.position.x,
          to.y,
          to.z - outfeed.pallet.position.z,
        ));
        outfeed.cases += 1;
        if (outfeed.cases >= CASES_PER_LAYER * LAYERS_PER_PALLET) {
          outfeed.shipping = { group: outfeed.pallet, travelled: 0 };
          outfeed.cases = 0;
          const fresh = makePallet();
          fresh.position.copy(outfeed.shipping.group.position);
          fresh.position.z = outfeed.shipping.group.position.z;
          this.scene.add(fresh);
          outfeed.pallet = fresh;
        }
      },
    });
  }

  resize() {
    const width = window.innerWidth;
    const height = window.innerHeight;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    if (!this.labels) return;
    // A window turned or resized before anyone moved the view gets the
    // whole line again; once somebody has, the view is theirs.
    if (this.fitPoints && !this.touched && this.fitted
        && (this.fitted.width !== width || this.fitted.height !== height)) {
      this.frame();
    } else {
      for (const label of this.labels) sizeLabel(label, this.camera, height, this.labelPixels());
    }
  }

  render() {
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
