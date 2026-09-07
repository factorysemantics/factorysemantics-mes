/* What is on the belts, and why.
 *
 * Deliberately free of three.js: this is bookkeeping, and keeping it separate is
 * what makes the rule below checkable by reading one short file.
 *
 * THE RULE. A unit appears on a belt only because the upstream station booked a
 * good unit, and disappears only because the downstream station booked one. The
 * renderer never adds a unit to make the belt look busy, and never removes one
 * to tidy up.
 *
 * What that costs, honestly: the number of units sitting on a belt is the
 * running difference of two machine counters, and counters do not conserve. A
 * PLC that resets (the KepSim hour stages exactly this at t=3000) loses the
 * units around the reset — the OPC agent refuses to invent them, which is
 * correct, and the consequence lands here as a belt whose contents no longer
 * match the steel. So this is NOT a work-in-progress measurement, and nothing in
 * the view may present it as one. When a station consumes with nothing tracked
 * in front of it, that is counted and shown rather than hidden.
 */

const ARRIVAL = 0.88; // fraction along the belt at which a unit is at the machine's mouth

export class Belt {
  /**
   * @param {number} length    metres of belt
   * @param {number} capacity  units it holds before it looks backed up
   * @param {number} rate      the upstream machine's rated units/second
   */
  constructor({ length, capacity, rate }) {
    this.length = length;
    this.capacity = Math.max(2, capacity);
    this.nominalRate = rate > 0 ? rate : 1;
    this.observedRate = this.nominalRate;
    // Front of the queue first: index 0 is the unit closest to the next machine.
    this.items = [];
    this.unmatched = 0;
    this.delivered = 0; // units that travelled the belt and were taken off the end
    this.overflowed = 0; // spawned into an already-full belt and not drawn
    this.nextId = 1;
    this.arrived = 0; // units spawned since the last update, for the rate estimate
    this.spacing = this.length / this.capacity;
  }

  /** How long a unit takes to travel this belt, in seconds.
   *
   *  Note this works out as capacity / rate and does not depend on the belt's
   *  length at all — which is right, and is the whole point of a buffer: a
   *  twenty-unit buffer running at two a second holds ten seconds of line. It
   *  is also why the view takes about a minute to fill from cold, exactly as
   *  the real line does.
   */
  get transitSeconds() {
    return this.length / this.speed;
  }

  /** Units per second, smoothed, so belt speed tracks a sped-up replay. */
  observe(count, dt) {
    if (dt <= 0) return;
    const instant = count / dt;
    const alpha = 1 - Math.exp(-dt / 6); // ~6 s memory
    this.observedRate += (instant - this.observedRate) * alpha;
  }

  /** Metres per second, chosen so units sit about one spacing apart at the
   *  rate they are actually arriving. Belt speed is a rendering decision and is
   *  the one thing here that is not read from the MES. */
  get speed() {
    const rate = Math.max(this.observedRate, this.nominalRate * 0.25, 0.1);
    return Math.min(6, Math.max(0.12, this.spacing * rate));
  }

  /** The upstream station booked `n` good units. */
  spawn(n) {
    for (let i = 0; i < n; i += 1) {
      this.items.push({ id: this.nextId++, s: 0 });
    }
    this.arrived += n;

    // A belt cannot hold more than a belt. On the real line the upstream
    // machine goes BLOCKED long before this; here it only happens when the view
    // has been catching up after a stall, and without a cap the queue would
    // grow without limit behind a machine that stopped consuming.
    const ceiling = this.capacity * 3;
    if (this.items.length > ceiling) {
      this.overflowed += this.items.length - ceiling;
      this.items.length = ceiling;
    }
  }

  /**
   * The downstream station booked `n` units (good or scrap — both consumed a
   * unit). Removes those that have reached the machine; anything it cannot
   * account for is counted, not conjured.
   *
   * @returns {number} how many were actually taken off the belt
   */
  consume(n) {
    let taken = 0;
    for (let i = 0; i < n; i += 1) {
      if (this.items.length && this.items[0].s >= ARRIVAL) {
        this.items.shift();
        this.delivered += 1;
        taken += 1;
      } else {
        this.unmatched += 1;
      }
    }
    return taken;
  }

  /** Advance the belt. Units queue up behind each other rather than overlapping,
   *  which is what makes a stopped machine downstream look like a jam. */
  update(dt) {
    // Belt speed follows the rate units are actually spawning at, so a replay
    // running at four times real time still looks like a belt rather than a
    // conveyor of stationary bottles.
    this.observe(this.arrived, dt);
    this.arrived = 0;

    const step = (this.speed / this.length) * dt;
    for (let i = 0; i < this.items.length; i += 1) {
      // Slot the i-th unit from the front can occupy without touching the one
      // ahead of it. Past capacity these collapse to zero and the surplus piles
      // at the belt head — a jam, drawn as a jam.
      const slot = Math.max(0, 1 - i / this.capacity);
      this.items[i].s = Math.min(this.items[i].s + step, slot);
    }
  }

  get backedUp() {
    return this.items.length >= this.capacity;
  }
}

/**
 * The whole line's belts, plus the running totals the readout shows.
 *
 * Stations are indexed in process order. Belt `i` runs from station `i` to
 * station `i + 1`, so a six-station line has five belts.
 */
export class LineFlow {
  constructor(stations, { capacity, spacings }) {
    this.stations = stations;
    this.belts = [];
    for (let i = 0; i < stations.length - 1; i += 1) {
      const cycle = stations[i].cycle_seconds;
      this.belts.push(new Belt({
        length: spacings[i],
        capacity,
        rate: cycle ? 1 / cycle : 1,
      }));
    }
    this.totals = { good: 0, scrap: 0 };
    this.arrivals = new Map(); // station code -> good units booked this frame
    this.byCode = new Map(stations.map((s, i) => [s.code, i]));
    // Rolling window of booking timestamps, for the line-rate readout.
    this.recent = [];
    // The view opens onto a line that is already running, so for the first
    // minute or so every machine is consuming work in progress that was on the
    // belt before we started watching. That is not a discrepancy worth
    // reporting — it is what starting to watch looks like. Once every belt has
    // delivered a unit end to end, the tally is reset and from then on it means
    // something: units consumed that we never saw made, which is what a counter
    // reset does to WIP accounting.
    this.warm = false;
    this.warmedAt = null;
  }

  /**
   * Apply one booking. `good` spawns onto the belt leaving this station;
   * `good + scrap` consumes from the belt feeding it, because scrap consumed a
   * unit too — it just did not survive the operation.
   *
   * The first station has no belt feeding it (its input arrives on a pallet)
   * and the last has no belt leaving it (its output leaves on a pallet), which
   * is exactly where the two robots stand.
   */
  book({ equipment, good = 0, scrap = 0 }, now) {
    const index = this.byCode.get(equipment);
    if (index === undefined) return;

    const consumed = Math.round(good + scrap);
    if (index > 0 && consumed > 0) {
      // The belt keeps its own unmatched tally; do not also count it here.
      this.belts[index - 1].consume(consumed);
    }

    const made = Math.round(good);
    if (made > 0) {
      // The last station's output does not go on a belt; the scene's palletiser
      // takes it from here instead.
      this.arrivals.set(equipment, (this.arrivals.get(equipment) || 0) + made);
      // Line rate is measured where finished goods leave, not summed across
      // stations — otherwise a six-station line reports six times its output.
      if (index === this.stations.length - 1) {
        for (let i = 0; i < made; i += 1) this.recent.push(now);
      }
    }

    this.totals.good += good;
    this.totals.scrap += scrap;
  }

  /** Units the given station made since the last drain. */
  drain(code) {
    const n = this.arrivals.get(code) || 0;
    this.arrivals.set(code, 0);
    return n;
  }

  update(dt, now) {
    for (const belt of this.belts) belt.update(dt);

    if (!this.warm && this.belts.every((belt) => belt.delivered > 0)) {
      this.warm = true;
      this.warmedAt = now;
      for (const belt of this.belts) belt.unmatched = 0;
    }

    // Keep a 20-second window for the rate figure.
    const cutoff = now - 20;
    while (this.recent.length && this.recent[0] < cutoff) this.recent.shift();
  }

  /** Units per minute across the line, measured at its last station. */
  get ratePerMinute() {
    if (this.recent.length < 2) return null;
    const span = this.recent[this.recent.length - 1] - this.recent[0];
    if (span <= 0) return null;
    return ((this.recent.length - 1) / span) * 60;
  }

  /** Units consumed with nothing tracked in front of them, counted only once
   *  the line has filled — before that it is just the view catching up. */
  get unmatched() {
    if (!this.warm) return 0;
    return this.belts.reduce((sum, belt) => sum + belt.unmatched, 0);
  }

  /** Roughly how long until the belts have filled end to end. */
  get fillSeconds() {
    return this.belts.reduce((sum, belt) => sum + belt.transitSeconds, 0);
  }
}
