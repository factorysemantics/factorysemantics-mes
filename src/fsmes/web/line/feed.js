/* Where the view gets its facts.
 *
 * Two implementations, one interface, on purpose: the recorded demo has to run
 * through exactly the same code path as the live plant, or it stops being a
 * demonstration of the real thing and becomes a cartoon that happens to sit in
 * the same repository.
 *
 * Both emit the same shape the API returns:
 *   { cursor, truncated, stations: [...], units: [{equipment, good, scrap}] }
 *
 * Units are handed to the renderer spread across the poll interval rather than
 * all at once. The MES books in bursts — the OPC subscription publishes every
 * 500 ms and a sped-up replay lands several seconds of line in one notification
 * — and dropping a whole burst onto a belt in one frame reads as a stutter. The
 * jitter buffer changes when a unit is *drawn*, never whether it existed.
 */

const POLL_MS = 500;
const SPREAD_MS = 500; // how long a poll's units are released over
const MAX_CATCHUP_TICKS = 120; // recorded seconds we will replay in one frame before jumping

class Jitter {
  constructor() {
    this.queue = [];
    this.releaseAt = 0;
    this.step = 0;
  }

  /** Take a poll's worth of units and schedule them across the next interval. */
  push(units, now) {
    const expanded = [];
    for (const unit of units) {
      // A booking of "3 good" is three units; the renderer wants them one by one.
      const good = Math.round(unit.good || 0);
      const scrap = Math.round(unit.scrap || 0);
      for (let i = 0; i < good; i += 1) expanded.push({ equipment: unit.equipment, good: 1, scrap: 0 });
      for (let i = 0; i < scrap; i += 1) expanded.push({ equipment: unit.equipment, good: 0, scrap: 1 });
    }
    if (!expanded.length) return;
    this.queue.push(...expanded);
    this.step = SPREAD_MS / this.queue.length;
    this.releaseAt = Math.max(this.releaseAt, now);
  }

  /** Units due by `now`. Anything still queued when the next poll lands simply
   *  joins it — the buffer drains faster rather than falling behind. */
  due(now) {
    if (!this.queue.length) return [];
    const out = [];
    while (this.queue.length && this.releaseAt <= now) {
      out.push(this.queue.shift());
      this.releaseAt += this.step;
    }
    if (!this.queue.length) this.releaseAt = now;
    return out;
  }
}

/** The plant, polled. */
export class LiveFeed {
  constructor(line) {
    this.line = line;
    this.cursor = -1;
    this.jitter = new Jitter();
    this.stations = [];
    this.connected = false;
    this.onError = null;
    this.onResync = null;
    this.stopped = false;
  }

  async start() {
    const layout = await this.fetch(`/line/layout${this.line ? `?line=${encodeURIComponent(this.line)}` : ""}`);
    this.line = layout.line.code;
    this.poll();
    this.timer = setInterval(() => this.poll(), POLL_MS);
    return layout;
  }

  stop() {
    this.stopped = true;
    clearInterval(this.timer);
  }

  async fetch(path) {
    const response = await fetch(path, { credentials: "same-origin" });
    if (response.status === 401) {
      const error = new Error("signed out");
      error.signedOut = true;
      throw error;
    }
    if (!response.ok) throw new Error(`${path} returned ${response.status}`);
    return response.json();
  }

  async poll() {
    if (this.stopped) return;
    try {
      const params = new URLSearchParams({ line: this.line, since: String(this.cursor) });
      const data = await this.fetch(`/line/events?${params}`);
      this.cursor = data.cursor;
      this.stations = data.stations;
      this.connected = true;
      if (data.truncated && this.onResync) {
        // We were away long enough that the feed capped what it would hand over.
        // Those units happened; we simply cannot show them arriving, so say so
        // by clearing the belts rather than animating a false backlog.
        this.onResync();
      }
      this.jitter.push(data.units, performance.now());
    } catch (error) {
      this.connected = false;
      if (this.onError) this.onError(error);
    }
  }

  due(now) {
    return this.jitter.due(now);
  }
}

/**
 * The recorded hour, played back.
 *
 * The file holds one row per simulated second per station — the same counters
 * the replay server publishes — so this turns counter deltas into unit bookings
 * the same way the OPC agent does, including its one rule: a counter that fell
 * is a reset, and the units around a reset are unknowable, so none are claimed.
 */
export class DemoFeed {
  constructor(url, { speed = 1 } = {}) {
    this.url = url;
    this.speed = speed;
    this.jitter = new Jitter();
    this.stations = [];
    this.connected = true;
    this.tick = 0;
    this.elapsed = 0;
    this.last = new Map();
    this.onError = null;
    this.onResync = null;
  }

  async start() {
    const response = await fetch(this.url, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`the recorded hour is not on disk (${response.status})`);
    this.data = await response.json();
    this.applyTick(0);
    return this.data.layout;
  }

  stop() {
    this.stopped = true;
  }

  /**
   * Advance playback.
   *
   * Driven by wall-clock, not by accumulated frame deltas. Summing `dt` would
   * tie the recording's speed to the frame rate — and because `dt` has to be
   * clamped so a backgrounded tab does not resume by teleporting, a slow frame
   * rate would quietly play the hour in slow motion. The recorded line must run
   * at the same speed as the real one, on any machine.
   */
  advance(dt, now) {
    if (this.stopped || !this.data) return;
    if (this.startedAt === undefined) this.startedAt = now;

    this.elapsed = ((now - this.startedAt) / 1000) * this.speed;
    let target = Math.floor(this.elapsed / this.data.tick_seconds);

    if (target - this.tick > MAX_CATCHUP_TICKS) {
      // Away long enough that catching up tick by tick would stall a frame.
      // Jump, and re-baseline the counters rather than booking the whole gap as
      // production that arrived at once.
      this.tick = target - 1;
      this.last.clear();
      if (this.onResync) this.onResync();
    }

    while (this.tick < target) {
      this.tick += 1;
      if (this.tick >= this.data.ticks) {
        // The hour wraps, and every counter snaps back to zero — the recording's
        // own free counter-reset drill. Re-baseline exactly as the agent would.
        this.tick = 0;
        this.startedAt = now;
        this.elapsed = 0;
        target = 0;
        this.last.clear();
        if (this.onResync) this.onResync();
      }
      this.applyTick(this.tick, now);
    }
  }

  applyTick(tick, now = performance.now()) {
    const units = [];
    this.stations = this.data.stations.map((station) => {
      const row = station.rows[tick];
      const [state, good, scrap, analog] = row;
      const previous = this.last.get(station.code);
      if (previous) {
        // Never invent production: a counter that fell is a reset, and becomes
        // the new baseline without booking the units around it.
        const dGood = good - previous.good;
        const dScrap = scrap - previous.scrap;
        if (dGood > 0 || dScrap > 0) {
          units.push({ equipment: station.code, good: Math.max(0, dGood), scrap: Math.max(0, dScrap) });
        }
      }
      this.last.set(station.code, { good, scrap });
      return {
        code: station.code,
        state,
        reason: null,
        since: null,
        analog: analog === null ? null : { name: station.analog, value: analog },
        order: this.data.order || null,
        operation: null,
        good,
        scrap,
      };
    });
    if (units.length) this.jitter.push(units, now);
  }

  due(now) {
    return this.jitter.due(now);
  }
}
