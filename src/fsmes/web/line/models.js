/* A plant's own machine models, drawn over the built-in shapes.
 *
 * parts.js builds every machine from primitives, and that stays the default:
 * this file only ever adds to it. When the layout names a model for a station
 * — a GLB file from `models/` in the plant's own pack, served by
 * /line/models/<name> — it is loaded here and, once it arrives, the built-in
 * body is hidden and the model stands in its place, at its heading. Nothing
 * comes from the internet; the files are the plant's own.
 *
 * The state colour follows the convention the models are drawn to: every mesh
 * whose material is named `signal` (a model's status bands and beacon) is
 * recoloured exactly as the built-in bands are, and a node named `spin` turns
 * while the machine runs. A model with no `signal` material gets a lit ring on
 * the floor around it instead, because painting someone's model green would
 * hide the machine it shows. Either way they are the machine's `bands`, so
 * applyStates and the down-pulse in scene.js treat them as before.
 *
 * A model that fails to load, or takes longer than MODEL_TIMEOUT_MS after the
 * view's first frame, leaves the built-in shape where it is with one
 * console.warn: the view is never blank and never waits on a download to show
 * state.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

import { STATE_COLOURS } from "./parts.js";

export const MODEL_TIMEOUT_MS = 3000;

const loader = new GLTFLoader();
const loads = new Map(); // url -> Promise of the loaded scene, shared by every station using it
const warned = new Set();

function load(url) {
  if (!loads.has(url)) loads.set(url, loader.loadAsync(url).then((gltf) => gltf.scene));
  return loads.get(url);
}

/* The time limit is for the model's download, not for the view's own start:
 * the first frame compiles every shader in the scene, and on a slow plant PC
 * (or software WebGL) that alone can take seconds. So the download starts at
 * once and the clock starts when the first frame has been drawn - two
 * animation frames from build(), the second of them after the first render. */
const firstFrame = () => new Promise((resolve) => {
  requestAnimationFrame(() => requestAnimationFrame(resolve));
});

function inTime(promise, ms) {
  let timer;
  const late = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`took longer than ${ms / 1000} s`)), ms);
  });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

/** A flat lit ring just outside the model's footprint, in the state colour. */
function floorRing(bounds, state) {
  const reach = Math.max(
    Math.abs(bounds.min.x), Math.abs(bounds.max.x),
    Math.abs(bounds.min.z), Math.abs(bounds.max.z), 0.5,
  );
  const colour = STATE_COLOURS[state] ?? STATE_COLOURS.unknown;
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(reach + 0.15, reach + 0.55, 48),
    new THREE.MeshStandardMaterial({
      color: colour, emissive: colour, emissiveIntensity: 1.25,
      metalness: 0.0, roughness: 0.4, side: THREE.DoubleSide,
    }),
  );
  ring.name = "state-ring";
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.02;
  return ring;
}

/**
 * Put the station's model in place of its built-in body, if it arrives in time.
 * `machine` is the scene's own entry for the station; it gains `modelState`
 * ("loading", then "loaded" or "failed") so a test or the console can tell.
 */
export function dressWithModel(machine) {
  const url = machine.station.model;
  if (!url) return Promise.resolve(machine);
  machine.modelState = "loading";
  const loading = load(url);
  return firstFrame().then(() => inTime(loading, MODEL_TIMEOUT_MS)).then((source) => {
    const model = source.clone(true);
    model.name = `model:${url}`;
    model.traverse((node) => {
      if (node.isMesh) {
        node.castShadow = true;
        node.receiveShadow = true;
      }
    });
    // A clone shares its materials with every other station using this file;
    // each station's signals get their own, or two fillers would share a colour.
    const colour = STATE_COLOURS[machine.state] ?? STATE_COLOURS.unknown;
    const signals = [];
    model.traverse((node) => {
      if (node.isMesh && node.material && node.material.name === "signal") {
        node.material = node.material.clone();
        node.material.color.setHex(colour);
        if (node.material.emissive) node.material.emissive.setHex(colour);
        signals.push(node);
      }
    });
    const bounds = new THREE.Box3().setFromObject(model);
    for (const child of machine.group.children) child.visible = false;
    machine.group.add(model);
    if (signals.length) {
      machine.bands = signals;
    } else {
      const ring = floorRing(bounds, machine.state);
      machine.group.add(ring);
      machine.bands = [ring];
    }
    machine.spin = model.getObjectByName("spin") || null;
    machine.model = model;
    machine.modelState = "loaded";
    return machine;
  }).catch((err) => {
    machine.modelState = "failed";
    if (!warned.has(url)) {
      warned.add(url);
      console.warn(`line view: ${machine.station.code} keeps its built-in shape; ` +
        `model ${url} did not load (${err && err.message ? err.message : err})`);
    }
    return machine;
  });
}
