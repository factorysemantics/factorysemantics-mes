/* An articulated arm that picks something up and puts it somewhere else.
 *
 * Two links and a wrist, solved analytically. Keyframing joint angles would have
 * been less code, but then the arm would move the same way regardless of where
 * the pallet is and it would visibly miss when a layer gets taller. Solving for
 * the target means the hand always lands on the thing.
 *
 * Motion is driven by moving the *target point* along a path and re-solving each
 * frame, rather than by animating joints. That is why the arm arcs the way a
 * real one does — lift clear, traverse, descend — without any per-joint work.
 */

import * as THREE from "three";
import { MATERIALS } from "./parts.js";

const UPPER = 1.65; // shoulder -> elbow
const FORE = 1.45; // elbow -> wrist
const SHOULDER_Y = 1.35;
const REACH = UPPER + FORE;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
// Smootherstep: zero velocity *and* zero acceleration at both ends, which is
// what stops the payload looking like it is being flung.
const ease = (t) => t * t * t * (t * (t * 6 - 15) + 10);

const box = (w, h, d, material) => new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
const cyl = (r, h, material, seg = 20) =>
  new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, seg), material);

export class RobotArm {
  /**
   * @param {THREE.Vector3} base  where the arm is bolted to the floor
   * @param {number} homeYaw      resting direction, radians
   */
  constructor(base, homeYaw = 0) {
    this.group = new THREE.Group();
    this.group.position.copy(base);
    this.base = base.clone();

    const pedestal = cyl(0.42, 0.3, MATERIALS.steelDark, 24);
    pedestal.position.y = 0.15;
    this.group.add(pedestal);

    this.yaw = new THREE.Group();
    this.yaw.position.y = 0.3;
    this.group.add(this.yaw);

    const column = cyl(0.3, SHOULDER_Y - 0.3, MATERIALS.paint, 20);
    column.position.y = (SHOULDER_Y - 0.3) / 2;
    this.yaw.add(column);

    const collar = cyl(0.24, 0.34, MATERIALS.steel, 20);
    collar.rotation.x = Math.PI / 2;
    collar.position.y = SHOULDER_Y - 0.3;
    this.yaw.add(collar);

    this.shoulder = new THREE.Group();
    this.shoulder.position.y = SHOULDER_Y - 0.3;
    this.yaw.add(this.shoulder);

    const upper = box(UPPER, 0.26, 0.22, MATERIALS.paint);
    upper.position.x = UPPER / 2;
    this.shoulder.add(upper);

    this.elbow = new THREE.Group();
    this.elbow.position.x = UPPER;
    this.shoulder.add(this.elbow);

    const elbowCap = cyl(0.17, 0.28, MATERIALS.steel, 16);
    elbowCap.rotation.x = Math.PI / 2;
    this.elbow.add(elbowCap);

    const fore = box(FORE, 0.19, 0.17, MATERIALS.steel);
    fore.position.x = FORE / 2;
    this.elbow.add(fore);

    this.wrist = new THREE.Group();
    this.wrist.position.x = FORE;
    this.elbow.add(this.wrist);

    // A vacuum head: the plate plus its suction cups. This is the part the eye
    // tracks, so it gets the detail.
    const plate = box(0.56, 0.07, 0.4, MATERIALS.steelDark);
    plate.position.y = -0.04;
    this.wrist.add(plate);
    for (const dx of [-0.19, 0.19]) {
      for (const dz of [-0.13, 0.13]) {
        const cup = cyl(0.05, 0.09, MATERIALS.rubber, 10);
        cup.position.set(dx, -0.12, dz);
        this.wrist.add(cup);
      }
    }

    // Whatever is being carried hangs here, so the payload inherits the hand's
    // motion for free.
    this.hand = new THREE.Group();
    this.hand.position.y = -0.17;
    this.wrist.add(this.hand);

    this.group.traverse((node) => {
      if (node.isMesh) {
        node.castShadow = true;
        node.receiveShadow = true;
      }
    });

    this.homeYaw = homeYaw;
    this.homeTarget = new THREE.Vector3(
      base.x + Math.cos(homeYaw) * 1.8,
      SHOULDER_Y + 0.4,
      base.z - Math.sin(homeYaw) * 1.8,
    );
    this.target = this.homeTarget.clone();
    this.path = null;
    this.t = 0;
    this.job = null;
    this.solve(this.target);
  }

  get busy() {
    return this.path !== null;
  }

  /**
   * Start a pick-and-place. `onPick` fires when the hand closes on the source,
   * `onPlace` when it lets go — that is where the caller attaches and detaches
   * the payload, so the two never drift out of sync with the animation.
   */
  moveItem(from, to, { duration = 2.6, lift = 0.85, onPick, onPlace } = {}) {
    const above = (p, h) => new THREE.Vector3(p.x, p.y + h, p.z);
    this.path = [
      { at: 0.0, point: above(from, lift) },
      { at: 0.18, point: from.clone(), event: onPick },
      { at: 0.34, point: above(from, lift) },
      { at: 0.62, point: above(to, lift) },
      { at: 0.84, point: to.clone(), event: onPlace },
      { at: 1.0, point: above(to, lift * 0.8) },
    ];
    this.duration = duration;
    this.t = 0;
    this.fired = new Set();
  }

  update(dt) {
    if (this.path) {
      this.t = Math.min(1, this.t + dt / this.duration);
      const eased = ease(this.t);

      for (let i = 0; i < this.path.length - 1; i += 1) {
        const a = this.path[i];
        const b = this.path[i + 1];
        if (eased >= a.at && eased <= b.at) {
          const span = b.at - a.at || 1;
          this.target.lerpVectors(a.point, b.point, (eased - a.at) / span);
          break;
        }
      }
      // Waypoint events fire once, on the way past.
      for (const step of this.path) {
        if (step.event && eased >= step.at && !this.fired.has(step)) {
          this.fired.add(step);
          step.event();
        }
      }
      if (this.t >= 1) {
        this.path = null;
        this.idleFrom = this.target.clone();
        this.idleT = 0;
      }
    } else if (this.idleFrom) {
      // Drift back to rest rather than snapping, so a stopped line still looks
      // like a machine waiting instead of a machine switched off.
      this.idleT = Math.min(1, this.idleT + dt / 1.4);
      this.target.lerpVectors(this.idleFrom, this.homeTarget, ease(this.idleT));
      if (this.idleT >= 1) this.idleFrom = null;
    }
    this.solve(this.target);
  }

  /** Two-link IK in the arm's own plane, plus a yaw to face the target. */
  solve(target) {
    const dx = target.x - this.base.x;
    const dz = target.z - this.base.z;
    // The yaw pivot turns local +X toward (cos y, 0, -sin y).
    const yaw = Math.atan2(-dz, dx);
    this.yaw.rotation.y = yaw;

    const planar = Math.hypot(dx, dz);
    const dy = target.y - (this.base.y + SHOULDER_Y);
    // Keep the target inside the working envelope; an unreachable point would
    // otherwise produce NaN angles and the arm would vanish.
    const distance = clamp(Math.hypot(planar, dy), 0.35, REACH - 0.02);

    const toTarget = Math.atan2(dy, planar);
    const shoulderOffset = Math.acos(
      clamp((UPPER * UPPER + distance * distance - FORE * FORE) / (2 * UPPER * distance), -1, 1),
    );
    const included = Math.acos(
      clamp((UPPER * UPPER + FORE * FORE - distance * distance) / (2 * UPPER * FORE), -1, 1),
    );

    const shoulderAngle = toTarget + shoulderOffset;
    const elbowAngle = Math.PI - included;

    this.shoulder.rotation.z = shoulderAngle;
    this.elbow.rotation.z = -elbowAngle;
    // Counter-rotate the wrist so the payload stays level all the way round.
    this.wrist.rotation.z = -(shoulderAngle - elbowAngle);
  }

  /** Take an object into the hand, preserving where it appears on screen. */
  grasp(object) {
    this.hand.add(object);
    object.position.set(0, 0, 0);
    object.rotation.set(0, 0, 0);
  }

  /** Hand an object over to another parent at a given world position. */
  release(object, parent, position) {
    parent.add(object);
    object.position.copy(position);
    object.rotation.set(0, 0, 0);
  }
}
