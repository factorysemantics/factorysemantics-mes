/* Everything the line is made of, built from primitives.
 *
 * No meshes are downloaded and no textures are shipped: every machine, belt,
 * bottle and pallet here is boxes, cylinders and lathes with honest materials on
 * them. That is not a limitation we are working around — it is what keeps the
 * whole view a handful of readable files that will still open in 2036, which is
 * the same reason the dashboard has no build step.
 *
 * Dimensions are metres, and they are roughly real: a 500 ml bottle is 22 cm
 * tall, a belt is waist height, a pallet is 1.2 x 0.8. Getting the scale right
 * is most of what makes a synthetic scene read as a factory instead of a toy.
 */

import * as THREE from "three";

// Matches styles.css, because a machine that is amber on the dashboard must be
// amber here. Operators learn one colour language, not two.
export const STATE_COLOURS = {
  running: 0x3fb950,
  idle: 0xd29922,
  down: 0xf85149,
  setup: 0xa371f7,
  unknown: 0x6e7681,
};

const STEEL = 0x8894a4;
const STEEL_DARK = 0x4a5563;
const PAINT = 0x2f3947;
const GUARD = 0x1b222c;
const RUBBER = 0x14181f;
const CARDBOARD = 0xb08653;
const TIMBER = 0xa07a4a;
const GLASS_EMPTY = 0x7fd6c0;
const GLASS_FULL = 0xe0a23c;

const mat = (color, opts = {}) =>
  new THREE.MeshStandardMaterial({ color, metalness: 0.55, roughness: 0.5, ...opts });

export const MATERIALS = {
  steel: mat(STEEL, { metalness: 0.75, roughness: 0.35 }),
  steelDark: mat(STEEL_DARK, { metalness: 0.7, roughness: 0.45 }),
  paint: mat(PAINT, { metalness: 0.3, roughness: 0.65 }),
  guard: mat(GUARD, { metalness: 0.2, roughness: 0.8 }),
  rubber: mat(RUBBER, { metalness: 0.05, roughness: 0.95 }),
  cardboard: mat(CARDBOARD, { metalness: 0.0, roughness: 0.9 }),
  timber: mat(TIMBER, { metalness: 0.0, roughness: 0.95 }),
  glassEmpty: mat(GLASS_EMPTY, { metalness: 0.1, roughness: 0.15, transparent: true, opacity: 0.55 }),
  glassFull: mat(GLASS_FULL, { metalness: 0.15, roughness: 0.2, transparent: true, opacity: 0.85 }),
};

export const BELT_HEIGHT = 1.0;
export const BELT_WIDTH = 0.7;
export const MACHINE_LENGTH = 3.0; // how much of the line a station occupies

const box = (w, h, d, material) => new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
const cyl = (r, h, material, segments = 24) =>
  new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, segments), material);

function shadowed(object) {
  object.traverse((node) => {
    if (node.isMesh) {
      node.castShadow = true;
      node.receiveShadow = true;
    }
  });
  return object;
}

/* ------------------------------------------------------------------ bottles */

/** A 500 ml bottle: a lathe profile, because a bottle silhouette is the one
 *  shape here that a plain cylinder would give away as fake. */
export function bottleGeometry() {
  const profile = [
    [0.0, 0.0], [0.045, 0.0], [0.045, 0.005], [0.046, 0.02],
    [0.046, 0.135], [0.038, 0.155], [0.021, 0.175], [0.019, 0.2],
    [0.022, 0.205], [0.022, 0.215], [0.0, 0.215],
  ].map(([x, y]) => new THREE.Vector2(x, y));
  const geometry = new THREE.LatheGeometry(profile, 14);
  geometry.computeVertexNormals();
  return geometry;
}

export const BOTTLE_HEIGHT = 0.215;

/* ---------------------------------------------------------------- conveyors */

/** A belt segment running along +X, its near end at the origin.
 *  `filled` picks the bottle colour: everything downstream of the filler is
 *  carrying product, everything upstream is carrying empties. */
export function makeConveyor(length, { width = BELT_WIDTH } = {}) {
  const group = new THREE.Group();

  const belt = box(length, 0.05, width, MATERIALS.rubber);
  belt.position.set(length / 2, BELT_HEIGHT, 0);
  group.add(belt);

  // Side rails, which is what actually reads as "conveyor" at a distance.
  for (const side of [-1, 1]) {
    const rail = box(length, 0.09, 0.035, MATERIALS.steel);
    rail.position.set(length / 2, BELT_HEIGHT + 0.06, side * (width / 2 + 0.02));
    group.add(rail);
  }

  // Legs every couple of metres, and rollers at each end.
  const legs = Math.max(2, Math.round(length / 2.2));
  for (let i = 0; i < legs; i += 1) {
    const x = length * ((i + 0.5) / legs);
    for (const side of [-1, 1]) {
      const leg = box(0.06, BELT_HEIGHT - 0.05, 0.06, MATERIALS.steelDark);
      leg.position.set(x, (BELT_HEIGHT - 0.05) / 2, side * (width / 2 - 0.05));
      group.add(leg);
    }
  }
  for (const x of [0, length]) {
    const roller = cyl(0.06, width + 0.02, MATERIALS.steelDark, 16);
    roller.rotation.x = Math.PI / 2;
    roller.position.set(x, BELT_HEIGHT, 0);
    group.add(roller);
  }

  return shadowed(group);
}

/* ----------------------------------------------------------------- machines */

// Per-kind chassis dimensions. Only what differs from the default is here; the
// scene reads `length` back to work out how much belt fits between stations.
const SHAPES = {
  depalletiser: { length: 2.6, width: 2.4, height: 1.4 },
  denester: { length: 2.6, width: 2.0, height: 1.5 },
  washer: { length: 3.4, width: 2.2, height: 1.7 },
  inspector: { length: 2.2, width: 1.6, height: 1.4 },
  filler: { length: 2.8, width: 2.2, height: 1.6 },
  palletiser: { length: 2.6, width: 2.6, height: 1.4 },
  mixer: { length: 2.6, width: 2.2, height: 1.6 },
  packer: { length: 2.8, width: 2.0, height: 1.8 },
  generic: { length: MACHINE_LENGTH, width: 2.0, height: 1.6 },
};

/** How much of the line a station of this kind occupies, in metres. */
export const machineLength = (kind) => (SHAPES[kind] || SHAPES.generic).length;

/** Shared chassis: plinth, body, guard frame, and the status band that carries
 *  the machine's MES state. Every silhouette is this plus its own top half. */
function chassis({ length = MACHINE_LENGTH, width = 2.0, height = 2.2 } = {}) {
  const group = new THREE.Group();
  const bands = [];

  const plinth = box(length, 0.25, width, MATERIALS.steelDark);
  plinth.position.y = 0.125;
  group.add(plinth);

  const body = box(length * 0.86, height - 0.25, width * 0.8, MATERIALS.paint);
  body.position.y = 0.25 + (height - 0.25) / 2;
  group.add(body);

  // The status band wraps the machine at eye height. It is emissive so it reads
  // as a signal light rather than as paint, and it is the only thing on the
  // machine that ever changes colour.
  for (const side of [-1, 1]) {
    const band = box(length * 0.87, 0.13, 0.04, new THREE.MeshStandardMaterial({
      color: STATE_COLOURS.unknown,
      emissive: STATE_COLOURS.unknown,
      emissiveIntensity: 1.2,
      metalness: 0.0,
      roughness: 0.4,
    }));
    band.position.set(0, height * 0.72, side * (width * 0.4 + 0.02));
    group.add(band);
    bands.push(band);
  }

  // A stack light on top — the thing you actually look for across a plant.
  const beacon = cyl(0.07, 0.22, new THREE.MeshStandardMaterial({
    color: STATE_COLOURS.unknown,
    emissive: STATE_COLOURS.unknown,
    emissiveIntensity: 1.6,
    metalness: 0.0,
    roughness: 0.3,
  }), 12);
  beacon.position.set(-length * 0.32, height + 0.11, 0);
  group.add(beacon);
  bands.push(beacon);

  const mast = cyl(0.02, 0.2, MATERIALS.steelDark, 8);
  mast.position.set(-length * 0.32, height, 0);
  group.add(mast);

  return { group, bands, height };
}

/** A tunnel the belt passes through — the washer's defining shape. */
function tunnel(group, { length, width, height }) {
  const shell = new THREE.Group();
  const wallThickness = 0.12;
  for (const side of [-1, 1]) {
    const wall = box(length, height * 0.62, wallThickness, MATERIALS.steel);
    wall.position.set(0, BELT_HEIGHT + height * 0.31, side * (width * 0.32));
    shell.add(wall);
  }
  const roof = box(length, wallThickness, width * 0.64 + wallThickness, MATERIALS.steel);
  roof.position.set(0, BELT_HEIGHT + height * 0.62, 0);
  shell.add(roof);
  group.add(shell);
}

/**
 * Build a machine of the given kind.
 *
 * Returns the group plus the pieces the renderer needs to keep touching: the
 * status bands (recoloured from MES state) and an optional `spin` node that
 * turns only while the machine is running, because a carousel that keeps
 * turning through a breakdown is a lie a plant manager spots instantly.
 */
export function makeMachine(kind) {
  const shape = SHAPES[kind] || SHAPES.generic;
  const { group, bands, height } = chassis(shape);
  let spin = null;

  switch (kind) {
    case "depalletiser": {
      // A heavy cell: the arm is added separately, this is what it stands on.
      const deck = box(2.4, 0.16, 2.4, MATERIALS.steelDark);
      deck.position.set(0, 0.33, 0);
      group.add(deck);
      break;
    }
    case "denester": {
      // A rotary table that lifts bottles out of trays, single file.
      const table = cyl(1.05, 0.18, MATERIALS.steel, 32);
      table.position.set(0, height + 0.09, 0);
      const arms = new THREE.Group();
      for (let i = 0; i < 8; i += 1) {
        const arm = box(0.95, 0.06, 0.1, MATERIALS.steelDark);
        arm.position.set(0.5, 0.12, 0);
        const pivot = new THREE.Group();
        pivot.rotation.y = (i / 8) * Math.PI * 2;
        pivot.add(arm);
        arms.add(pivot);
      }
      table.add(arms);
      group.add(table);
      spin = table;
      break;
    }
    case "washer": {
      tunnel(group, { length: MACHINE_LENGTH * 1.15, width: 2.2, height: 1.5 });
      // Wash headers along the top — the pipework is the tell.
      for (let i = 0; i < 4; i += 1) {
        const pipe = cyl(0.05, 1.5, MATERIALS.steel, 10);
        pipe.rotation.x = Math.PI / 2;
        pipe.position.set(-1.1 + i * 0.75, height + 0.18, 0);
        group.add(pipe);
      }
      break;
    }
    case "inspector": {
      // A gantry arch over the belt with a scanning light bar underneath.
      const arch = new THREE.Group();
      for (const side of [-1, 1]) {
        const post = box(0.16, 1.5, 0.16, MATERIALS.steel);
        post.position.set(0, BELT_HEIGHT + 0.75, side * 0.85);
        arch.add(post);
      }
      const beam = box(0.3, 0.18, 1.9, MATERIALS.steel);
      beam.position.set(0, BELT_HEIGHT + 1.5, 0);
      arch.add(beam);
      const scanner = box(0.08, 0.06, 1.4, new THREE.MeshStandardMaterial({
        color: 0x4a9eff, emissive: 0x4a9eff, emissiveIntensity: 2.0, roughness: 0.3,
      }));
      scanner.position.set(0, BELT_HEIGHT + 1.38, 0);
      arch.add(scanner);
      group.add(arch);
      break;
    }
    case "filler": {
      // A rotary filler: the carousel of nozzles is the signature.
      const carousel = new THREE.Group();
      const drum = cyl(1.15, 0.5, MATERIALS.steel, 32);
      drum.position.y = 0.25;
      carousel.add(drum);
      for (let i = 0; i < 12; i += 1) {
        const angle = (i / 12) * Math.PI * 2;
        const nozzle = cyl(0.05, 0.4, MATERIALS.steelDark, 8);
        nozzle.position.set(Math.cos(angle) * 0.95, 0.6, Math.sin(angle) * 0.95);
        carousel.add(nozzle);
      }
      carousel.position.set(0, height, 0);
      group.add(carousel);
      spin = carousel;
      break;
    }
    case "palletiser": {
      // A fenced cell. The arm and the pallet are added by the scene.
      const deck = box(2.6, 0.16, 2.6, MATERIALS.steelDark);
      deck.position.set(0, 0.33, 0);
      group.add(deck);
      for (const side of [-1, 1]) {
        const fence = box(2.8, 1.8, 0.05, new THREE.MeshStandardMaterial({
          color: GUARD, metalness: 0.4, roughness: 0.7, transparent: true, opacity: 0.35,
        }));
        fence.position.set(0, 0.9, side * 2.0);
        group.add(fence);
      }
      break;
    }
    case "mixer": {
      const tank = cyl(1.1, 2.2, MATERIALS.steel, 28);
      tank.position.y = height + 0.4;
      group.add(tank);
      const motor = cyl(0.28, 0.5, MATERIALS.steelDark, 16);
      motor.position.y = height + 1.75;
      group.add(motor);
      spin = motor;
      break;
    }
    case "packer": {
      const hopper = new THREE.Mesh(
        new THREE.CylinderGeometry(0.35, 1.1, 1.1, 4),
        MATERIALS.steel,
      );
      hopper.rotation.y = Math.PI / 4;
      hopper.position.y = height + 0.55;
      group.add(hopper);
      break;
    }
    default: {
      const cap = box(MACHINE_LENGTH * 0.7, 0.3, 1.4, MATERIALS.steel);
      cap.position.y = height + 0.15;
      group.add(cap);
    }
  }

  shadowed(group);
  return { group, bands, spin, height, shape };
}

/* -------------------------------------------------------- trays and pallets */

/** An open tray of empties: what the depalletiser lifts. */
export function makeTray(count = 12) {
  const group = new THREE.Group();
  const w = 0.52;
  const d = 0.36;

  const base = box(w, 0.03, d, MATERIALS.cardboard);
  group.add(base);
  for (const [dx, dz, sw, sd] of [[w / 2, 0, 0.02, d], [-w / 2, 0, 0.02, d], [0, d / 2, w, 0.02], [0, -d / 2, w, 0.02]]) {
    const wall = box(sw, 0.1, sd, MATERIALS.cardboard);
    wall.position.set(dx, 0.05, dz);
    group.add(wall);
  }

  // The bottles inside are what sells it as a tray of something rather than a
  // brown box, so they are real geometry, laid out on a grid.
  const cols = Math.min(4, count);
  const rows = Math.max(1, Math.ceil(count / cols));
  const geometry = bottleGeometry();
  for (let i = 0; i < count; i += 1) {
    const bottle = new THREE.Mesh(geometry, MATERIALS.glassEmpty);
    bottle.position.set(
      ((i % cols) - (cols - 1) / 2) * (w / cols),
      0.015,
      (Math.floor(i / cols) - (rows - 1) / 2) * (d / rows),
    );
    group.add(bottle);
  }

  return shadowed(group);
}

/** A closed case of product: what the palletiser stacks. */
export function makeCase() {
  const group = new THREE.Group();
  const shell = box(0.5, 0.28, 0.34, MATERIALS.cardboard);
  shell.position.y = 0.14;
  group.add(shell);
  const tape = box(0.5, 0.012, 0.06, new THREE.MeshStandardMaterial({ color: 0xd8c9a8, roughness: 0.9 }));
  tape.position.y = 0.281;
  group.add(tape);
  return shadowed(group);
}

/** A 1200 x 800 europallet, built the way one actually is. */
export function makePallet() {
  const group = new THREE.Group();
  for (const z of [-0.35, 0, 0.35]) {
    const bearer = box(1.2, 0.09, 0.1, MATERIALS.timber);
    bearer.position.set(0, 0.045, z);
    group.add(bearer);
  }
  for (let i = 0; i < 5; i += 1) {
    const board = box(0.16, 0.022, 0.8, MATERIALS.timber);
    board.position.set(-0.52 + i * 0.26, 0.10, 0);
    group.add(board);
  }
  return shadowed(group);
}

export const PALLET_DECK = 0.112;

/* ------------------------------------------------------------------- labels */

/** A station label drawn to a canvas — no font files to ship, and it stays
 *  legible at any camera distance because sprites do not foreshorten. */
export function makeLabel(code, name) {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");

  ctx.fillStyle = "rgba(14, 17, 22, 0.82)";
  ctx.strokeStyle = "rgba(38, 48, 64, 1)";
  ctx.lineWidth = 3;
  if (ctx.roundRect) {
    ctx.beginPath();
    ctx.roundRect(6, 6, 500, 116, 14);
    ctx.fill();
    ctx.stroke();
  } else {
    ctx.fillRect(6, 6, 500, 116);
    ctx.strokeRect(6, 6, 500, 116);
  }

  ctx.fillStyle = "#e6edf3";
  ctx.font = "600 52px ui-monospace, Consolas, monospace";
  ctx.textAlign = "center";
  ctx.fillText(code, 256, 62);
  ctx.fillStyle = "#8b98a5";
  ctx.font = "400 30px system-ui, Segoe UI, sans-serif";
  ctx.fillText((name || "").slice(0, 30), 256, 102);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;

  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }));
  sprite.scale.set(2.4, 0.6, 1);
  sprite.renderOrder = 10;
  return sprite;
}
