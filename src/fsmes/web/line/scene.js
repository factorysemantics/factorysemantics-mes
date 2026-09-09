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
  MATERIALS,
  PALLET_DECK,
  STATE_COLOURS,
  bottleGeometry,
  machineLength,
  makeCase,
  makeConveyor,
  makeLabel,
  makeMachine,
  makePallet,
  makeTray,
} from "./parts.js";
import { RobotArm } from "./robot.js";

const BELT_TOP = BELT_HEIGHT + 0.025;
const TRAY_LINGER = 0.45; // seconds a placed tray stays before the denester takes it
const CASES_PER_LAYER = 4;
const LAYERS_PER_PALLET = 4;

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
    this.controls.addEventListener("start", () => { this.controls.autoRotate = false; });

    this.lights();
    this.machines = new Map();
    this.beltViews = [];
    this.robots = {};
    this.clock = 0;

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

  floor(extent) {
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(extent + 140, 150),
      new THREE.MeshStandardMaterial({ color: 0x151a22, roughness: 0.96, metalness: 0.02 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(extent / 2, 0, 0);
    floor.receiveShadow = true;
    this.scene.add(floor);

    const grid = new THREE.GridHelper(220, 110, 0x243044, 0x1a212b);
    grid.position.set(extent / 2, 0.008, 0);
    grid.material.transparent = true;
    grid.material.opacity = 0.5;
    this.scene.add(grid);

    // Painted lane markings. Two yellow lines either side of the line is the
    // cheapest thing on screen and does more for "this is a factory" than any
    // amount of machine detail.
    const paint = new THREE.MeshStandardMaterial({ color: 0xd8a72a, roughness: 0.85, metalness: 0 });
    for (const z of [-3.6, 3.6]) {
      const stripe = new THREE.Mesh(new THREE.PlaneGeometry(extent + 16, 0.14), paint);
      stripe.rotation.x = -Math.PI / 2;
      stripe.position.set(extent / 2, 0.012, z);
      this.scene.add(stripe);
    }
  }

  /** Build the whole line from a `/line/layout` payload. */
  build(layout) {
    this.layout = layout;
    const stations = layout.stations;
    const extent = stations.length > 1 ? stations[stations.length - 1].x - stations[0].x : 12;
    this.origin = stations[0].x;

    this.floor(extent);

    // Machines and labels.
    stations.forEach((station) => {
      const machine = makeMachine(station.kind);
      machine.group.position.set(station.x - this.origin, 0, station.z);
      machine.group.rotation.y = THREE.MathUtils.degToRad(station.rot || 0);
      this.scene.add(machine.group);

      const label = makeLabel(station.code, station.name);
      label.position.set(station.x - this.origin, machine.height + 1.15, station.z);
      this.scene.add(label);

      this.machines.set(station.code, { ...machine, station, state: "unknown" });
    });

    // Conveyors, and one instanced pool of bottles per belt.
    const fillerIndex = stations.findIndex((s) => s.kind === "filler" || s.kind === "mixer");
    this.spacings = [];
    for (let i = 0; i < stations.length - 1; i += 1) {
      const from = stations[i];
      const to = stations[i + 1];
      const startX = from.x - this.origin + machineLength(from.kind) / 2;
      const endX = to.x - this.origin - machineLength(to.kind) / 2;
      const length = Math.max(1.5, endX - startX);
      this.spacings.push(length);

      const conveyor = makeConveyor(length);
      conveyor.position.set(startX, 0, from.z);
      this.scene.add(conveyor);

      const filled = fillerIndex >= 0 && i >= fillerIndex;
      const pool = new THREE.InstancedMesh(
        bottleGeometry(),
        filled ? MATERIALS.glassFull : MATERIALS.glassEmpty,
        160,
      );
      pool.castShadow = true;
      pool.receiveShadow = true;
      pool.count = 0;
      pool.frustumCulled = false;
      this.scene.add(pool);

      this.beltViews.push({ pool, startX, length, z: from.z, dummy: new THREE.Object3D() });
    }

    this.buildRobots(stations);
    this.frame(extent);
    return this;
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

  /** Aim the camera so the whole line is in shot.
   *
   *  Low and three-quarters-on rather than high and square: a long line seen
   *  from above is a diagram, and seen from the floor at one end it is a
   *  factory. The far stations are meant to be small.
   */
  frame(extent) {
    const centre = new THREE.Vector3(extent / 2, 1.4, 0);
    this.controls.target.copy(centre);

    const distance = Math.max(22, extent * 0.62);
    this.camera.position.set(centre.x - extent * 0.5, distance * 0.30, distance * 0.78);
    this.camera.lookAt(centre);
    this.key.target.position.copy(centre);

    // Fog scaled to the line, so a short line is not swallowed by it.
    this.scene.fog.near = Math.max(30, extent * 1.1);
    this.scene.fog.far = Math.max(140, extent * 4.5);

    // Shadow frustum has to cover the line, or half of it loses its shadows.
    const reach = extent * 0.75 + 20;
    Object.assign(this.key.shadow.camera, { left: -reach, right: reach, top: reach, bottom: -reach, near: 1, far: 200 });
    this.key.position.set(centre.x - 26, 44, 30);
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
        const wobble = ((item.id * 37) % 100) / 100 - 0.5;
        dummy.position.set(
          view.startX + item.s * view.length,
          BELT_TOP,
          view.z + wobble * 0.08,
        );
        dummy.rotation.y = ((item.id * 53) % 100) / 100 * Math.PI * 2;
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
  }

  render() {
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
