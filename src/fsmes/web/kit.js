/* The screen kit: the charts and controls every object page draws.

   analysis.js and quality.js each grew their own SVG helpers; a third copy
   for the machine page would have made the drift permanent. These are the
   one set. Hand-drawn SVG, no library - a plant PC renders this for years
   without a toolchain (principle 5). Nothing here computes a number: a chart
   draws what the API measured, and draws "unknown" when it did not.

   The second half of this file is the CHART CONTRACT - `FS.kit.chart(kind,
   envelope, options)`, the four shapes an analysis exploration needs, drawn
   against the six rules of docs/design/agentic-harness.md SS9 M1 and the
   chart contract in docs/design/STYLE.md. It is here, and extended rather
   than duplicated, for the same reason the rest of this file is: `kit.js`
   exists so that two screens can never state coverage differently, and a
   second chart engine would make that drift permanent. */

(function () {
  "use strict";

  const FS = window.FS;
  const NS = "http://www.w3.org/2000/svg";

  const utc = (ts) => {
    const s = String(ts);
    return new Date(s + (s.endsWith("Z") ? "" : "Z"));
  };

  function duration(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const s = Math.round(seconds);
    if (s < 90) return `${s}s`;
    const m = Math.round(s / 60);
    if (m < 90) return `${m} min`;
    const h = Math.floor(m / 60);
    return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  }

  /* "3 s", "2 min", "1 h" - how long ago a tag last spoke. */
  function age(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    if (seconds < 60) return `${Math.round(seconds)} s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
    return `${(seconds / 3600).toFixed(1)} h`;
  }

  function svg(width, height) {
    const node = document.createElementNS(NS, "svg");
    node.setAttribute("viewBox", `0 0 ${width} ${height}`);
    node.setAttribute("width", width);
    node.setAttribute("height", height);
    return node;
  }

  function add(parent, tag, attrs, text) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    parent.appendChild(node);
    return node;
  }

  function empty(host, message) {
    host.replaceChildren();
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = message;
    host.appendChild(p);
  }

  /* Clock labels across a time axis, at a spacing the window can carry. */
  function timeTicks(g, start, end, width, height, left) {
    const span = (end - start) / 1000;
    const step = [60, 300, 900, 1800, 3600, 4 * 3600, 12 * 3600, 86400].find((s) => span / s <= 10) || 86400;
    for (let t = Math.ceil(start / 1000 / step) * step; t * 1000 <= end; t += step) {
      const x = left + ((t * 1000 - start) / (end - start)) * width;
      add(g, "line", { x1: x, y1: 0, x2: x, y2: height, class: "grid-line", opacity: 0.5 });
      add(g, "text", { x, y: height + 12, class: "axis", "text-anchor": "middle" },
          // The plant's clock: an axis in the reader's own zone would put a
          // night shift's trough in the middle of the afternoon.
          new Date(t * 1000).toLocaleTimeString([],
            FS.fmt.zone() ? { hour: "2-digit", minute: "2-digit", timeZone: FS.fmt.zone() }
                          : { hour: "2-digit", minute: "2-digit" }));
    }
  }

  /* ---------- a state timeline: one row per machine ----------
     data = /analysis/timeline: { window: {start, end}, machines: [{code,
     intervals: [{state, reason, start, end, seconds}]}] }

     Four screens draw this - analysis, the machine page, the line view and the
     schedule board - so it is one implementation, and since 2026-09-28 that
     implementation is the `states` shape of the chart contract below. It used
     to be a second copy of the same layout, which meant a disconnected
     interval was a solid grey block here and hatched nowhere: the same drift
     between two screens this file exists to prevent, one level down. */
  function timeline(host, data, options = {}) {
    host.replaceChildren();
    const machines = (data.machines || []).filter((m) => (m.intervals || []).length);
    /* A window in which nothing was recorded says so in words. The shape below
       would draw an empty grid with an honest total over it, which is right for
       an exploration and wrong for a screen whose panel is otherwise blank. */
    if (!machines.length) {
      return empty(host, options.emptyText || "No equipment states recorded in this window yet.");
    }
    return draw(host, "states", data, { rowHeight: 26, rows: machines, ...options });
  }

  /* ---------- a trend: mean line over a min/max band ----------
     data = /analysis/tag/{code}: { tag, window: {start, end}, points: [{t, mean, min, max}] }
     options.limits = {min, max} draws the declared bounds as dashed lines. */
  function trend(host, data, options = {}) {
    host.replaceChildren();
    if (!data.points || !data.points.length) {
      return empty(host, options.emptyText || `No history for ${data.tag || data.equipment} in this window.`);
    }
    const left = 48, right = 12, top = 12, bottom = 24;
    const width = Math.max(host.clientWidth || 900, 620);
    const height = options.height || 210;
    const plot = width - left - right;
    const plotH = height - top - bottom;
    const start = utc(data.window.start).getTime();
    const end = utc(data.window.end).getTime();
    const limits = options.limits || {};
    const lows = data.points.map((p) => p.min).concat(limits.min !== undefined && limits.min !== null ? [limits.min] : []);
    const highs = data.points.map((p) => p.max).concat(limits.max !== undefined && limits.max !== null ? [limits.max] : []);
    let lo = Math.min(...lows), hi = Math.max(...highs);
    if (hi === lo) { hi += 0.5; lo -= 0.5; }
    const pad = (hi - lo) * 0.08;
    lo -= pad; hi += pad;

    const x = (t) => left + ((utc(t).getTime() - start) / Math.max(end - start, 1)) * plot;
    const y = (v) => top + plotH - ((v - lo) / (hi - lo)) * plotH;

    const chart = svg(width, height);
    for (let i = 0; i <= 3; i++) {
      const value = lo + ((hi - lo) * i) / 3;
      add(chart, "line", { x1: left, y1: y(value), x2: left + plot, y2: y(value), class: "grid-line" });
      add(chart, "text", { x: left - 6, y: y(value) + 3, class: "axis", "text-anchor": "end" },
          value.toFixed(Math.abs(hi - lo) < 10 ? 1 : 0));
    }
    timeTicks(chart, start, end, plot, top + plotH, left);
    for (const [key, cls] of [["min", "limit-line"], ["max", "limit-line"]]) {
      if (limits[key] === undefined || limits[key] === null) continue;
      const ly = y(limits[key]);
      add(chart, "line", { x1: left, y1: ly, x2: left + plot, y2: ly, class: cls });
      add(chart, "text", { x: left + plot - 4, y: ly - 3, class: "axis", "text-anchor": "end" }, `${key} ${limits[key]}`);
    }
    // Band first, mean on top: the band is what stops a one-second
    // excursion disappearing into an average.
    const band = data.points.map((p) => `${x(p.t)},${y(p.max)}`)
      .concat(data.points.slice().reverse().map((p) => `${x(p.t)},${y(p.min)}`));
    add(chart, "polygon", { points: band.join(" "), class: "trend-band" });
    add(chart, "polyline", { points: data.points.map((p) => `${x(p.t)},${y(p.mean)}`).join(" "), class: "trend-line" });
    add(chart, "text", { x: left + 4, y: top + 10, class: "axis" }, options.label || data.tag || "");
    host.appendChild(chart);
  }

  /* ---------- how much of the window anybody watched ----------

     Drawn beside every OEE figure, never instead of one and never folded
     into one. A plain number and a short bar: 92% availability over forty
     observed minutes of an eight-hour window is not the same claim as 92%
     over the shift, and on a screen this row is the only thing that tells
     the two apart. */
  function coverageRow(oee) {
    const row = FS.el("div", "bar-row coverage");
    const bar = FS.el("div", "bar");
    const fill = FS.el("i", "cover");
    const value = oee.coverage;
    fill.style.width = `${Math.min(100, (value || 0) * 100)}%`;
    if (value === null || value === undefined) fill.classList.add("unknown");
    bar.append(fill);
    row.title = oee.coverage_note
      || "How much of this window the MES actually watched. Availability is run time over "
         + "the watched part of it, not over the window.";
    row.append(FS.el("span", null, "Watched"), bar, FS.el("span", "num", FS.fmt.pct(value)));
    return row;
  }

  /* The ledger as a short list: where the unwatched time went. Drawn in place
     of the figures when this plant's pack floor withholds them. Every list
     states its total, including this one. */
  function ledgerSummary(oee) {
    const box = FS.el("div", "ledger");
    if (oee.coverage_note) box.append(FS.el("p", "muted small", oee.coverage_note));
    const ledger = oee.ledger;
    if (!ledger) return box;
    const list = FS.el("ul", "ledger-causes");
    const causes = ledger.seconds_by_cause || {};
    const names = Object.keys(causes);
    for (const cause of names) {
      const li = FS.el("li");
      li.append(FS.el("span", "cause", cause.replace(/_/g, " ")),
                FS.el("span", "num", duration(causes[cause])));
      list.append(li);
    }
    if (!names.length) {
      list.append(FS.el("li", "muted", "nothing — every second of this window was watched"));
    }
    box.append(list);
    box.append(FS.el("p", "muted small",
      `${duration(ledger.observed_seconds)} watched of ${duration(ledger.window_seconds)}`));
    return box;
  }

  /* ---------- the OEE bars a machine card draws ---------- */
  function oeeBars(host, oee) {
    host.replaceChildren();
    // Below this plant's pack floor the figures are withheld and the ledger is
    // drawn in their place - not a smaller bar, which would read as a
    // measurement of a machine rather than of how little we saw of it.
    if (oee.coverage_note && oee.coverage_floor) {
      host.append(coverageRow(oee), ledgerSummary(oee));
      return;
    }
    [["Availability", oee.availability], ["Performance", oee.performance],
     ["Quality", oee.quality], ["OEE", oee.oee]].forEach(([label, value], index) => {
      const row = FS.el("div", `bar-row${index === 3 ? " total" : ""}`);
      const bar = FS.el("div", "bar");
      const fill = FS.el("i");
      // The bar cannot draw more than itself, so performance above rated fills
      // it; the number beside it is the true one, and the note says why.
      fill.style.width = `${Math.min(100, (value || 0) * 100)}%`;
      if (value === null || value === undefined) fill.classList.add("unknown");
      bar.append(fill);
      if (label === "Performance" && oee.performance_note) row.title = oee.performance_note;
      row.append(FS.el("span", null, label), bar, FS.el("span", "num", FS.fmt.pct(value)));
      host.append(row);
    });
    /* Counted work that will not fit inside the run time leaves Performance
       and OEE with no figure, and a dash with the reason in a tooltip is a
       dash nobody reads. The finding goes on the card in one line - the whole
       sentence is four on a card in a grid, which is a paragraph nobody reads
       either - and the sentence itself, with both its numbers, is the line's
       title. Same words as the analysis row, and no percentage in either:
       the figure this replaced read 881 % on a plant replaying at 10x. */
    if (oee.counts_outrun_run_time && oee.performance_note) {
      const units = (oee.good_qty || 0) + (oee.scrap_qty || 0);
      const said = FS.el("p", "muted small counts-outrun",
        `Counted work outruns the run time — ${units.toLocaleString()} units against `
        + `${duration(oee.runtime_seconds)} of running, so there is no performance figure`);
      said.title = oee.performance_note;
      host.append(said);
    }
    host.append(coverageRow(oee));
  }

  /* ================================================================
     THE CHART SHAPES AN EXPLORATION NEEDS

         FS.kit.chart(kind, envelope, options) -> <svg>
         FS.kit.draw(host, kind, envelope, options) -> <svg>

     `kind` is "line", "bars" (alias "pareto"), "states" or "histogram".
     `envelope` is the API's own payload — the same object a screen was given,
     with its coverage, its ledger and its totals still on it. `options` names
     which of the payload's fields to plot, and nothing else.

     Data in, markup out: no fetch, no page state, and no arithmetic beyond
     laying the numbers out. Every number drawn is a number the envelope
     carried, and it appears verbatim in a `data-value` attribute, so a test
     can assert exactly that. It is rule 1, and the other five hang off it: a
     chart that worked out its own figure would be stating something the MES
     never measured, in the most convincing medium this product has.

     The six rules — docs/design/agentic-harness.md §9 M1, and the chart
     contract in docs/design/STYLE.md:

     1. A chart draws what the API measured. `data-value` is the envelope's
        own number, unrounded and unscaled.
     2. Every figure carries its coverage. `data-coverage` is the envelope's
        figure — a number, "null" when it could not be computed, or "absent"
        when the payload has no such field, because those are three different
        facts. A row this plant's pack floor withholds is drawn withheld, at
        full width, with its ledger: never omitted, never averaged away, and
        never a shorter bar.
     3. Unknown is drawn as unknown. A null reading, an unwatched stretch and
        a disconnected interval are hatched, carry `data-unknown="true"`, and
        break the line rather than being joined through.
     4. Honest axes. A y-axis that does not start at zero says so on itself; a
        window the MES could not fill says what it truncated; a rate carries
        its denominator; a shape that depends on a bucket or a bin width
        states the width.
     5. Every chart states its total, in text on the chart and in
        `data-total`, including the rows nobody drew.
     6. Palette only, four themes. Nothing here names a colour: every mark
        carries a class or a palette variable from styles.css, so
        control-room, daylight, high-contrast and night-shift all follow
        without the page knowing a chart exists.

     Each chart also carries a `<title>` and a `<desc>` assembled from the
     same sentences as the visible footer, so what a screen reader is told and
     what a sighted reader sees cannot drift apart. */

  let serial = 0;

  /* The envelope's own number, as a string, unrounded — what a test reads. */
  const raw = (v) => (v === null || v === undefined ? "" : String(v));
  const has = (v) => v !== null && v !== undefined;
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const count = (v) => (has(v) ? Math.round(v).toLocaleString() : "—");
  const things = (n, one, many) => `${count(n)} ${n === 1 ? one : many || one + "s"}`;
  /* The first key an envelope actually carries, so one shape can be fed the
     payload of the analysis it belongs to without a mapping layer. */
  const keyOf = (row, wanted) => wanted.find((k) => row && k in row);

  /* The hatch every unknown mark is painted with. One pattern per chart:
     two charts on a page sharing an id is two charts fighting over it. */
  function hatch(node, id) {
    const defs = add(node, "defs", {});
    const pattern = add(defs, "pattern", {
      id, width: 6, height: 6,
      patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)",
    });
    add(pattern, "rect", { width: 6, height: 6, class: "chart-unknown-ground" });
    add(pattern, "line", { x1: 0, y1: 0, x2: 0, y2: 6, class: "chart-unknown-hatch" });
    return `url(#${id})`;
  }

  /* Rule 4, the window half. The MES cannot report time it was not watching,
     and a chart drawn over two hours of an eight-hour question has to say
     which of the two it drew. `clamped` is the envelope's own word for it. */
  function windowNote(window) {
    if (!window) return null;
    if (window.clamped) {
      return `Showing ${Number(window.hours).toFixed(2)} h of the `
           + `${window.requested_hours} h asked for — that is how long the MES has been `
           + `watching. Time before that is not downtime.`;
    }
    if (window.shift && window.shift.in_progress) {
      return `${window.shift.code} ${window.shift.day}, still running — `
           + `${Number(window.hours).toFixed(2)} h of ${window.shift.nominal_hours} h so far`;
    }
    return null;
  }

  /* Rule 2. The envelope either carries a coverage figure or it does not, and
     "nobody watched half of this", "coverage could not be computed" and "this
     payload has no coverage field" are three different facts a reader is
     entitled to tell apart. `data-coverage` says which. */
  function coverageOf(envelope) {
    if (!("coverage" in envelope)) {
      /* Some payloads state their blindness in seconds rather than as a
         share — the downtime pareto does, because a disconnection is not
         downtime and must never be sorted beside a reason. */
      if (has(envelope.unknown_share)) {
        return { attr: raw(envelope.unknown_share), kind: "unknown_share",
                 text: `${FS.fmt.pct(envelope.unknown_share)} of this window nobody was `
                       + `watching (${duration(envelope.unknown_seconds)})` };
      }
      if (has(envelope.unknown_seconds)) {
        return { attr: "absent", kind: "unknown_seconds",
                 text: `${duration(envelope.unknown_seconds)} of this window nobody was watching` };
      }
      return { attr: "absent", kind: "absent", text: null };
    }
    if (!has(envelope.coverage)) {
      return { attr: "null", kind: "null",
               text: envelope.coverage_note
                     || "How much of this window was watched is unknown." };
    }
    const withheld = Boolean(envelope.coverage_note && envelope.coverage_floor);
    return {
      attr: raw(envelope.coverage), kind: withheld ? "withheld" : "known",
      text: `Watched ${FS.fmt.pct(envelope.coverage)} of the window`
            + (withheld ? " — below this plant's floor, so the figures are withheld" : ""),
    };
  }

  /* Is this row one the plant's coverage floor withholds? The same test
     `oeeBars` already uses, so a station drawn withheld on the machine card
     is drawn withheld here too. */
  const isWithheld = (row) => Boolean(row && row.coverage_note && row.coverage_floor);

  /* What a withheld row says under the chart: why its figures are withheld,
     and its own ledger — where the unwatched seconds went. Rule 2 asks for the
     ledger, not just the word "withheld": "nobody watched it" is not a
     finding anybody can act on, and "never connected for 2h 03m" is. */
  function withheldNotes(row, label) {
    return [`${label}: ${row.coverage_note}`]
      .concat(ledgerNotes(row.ledger).map((note) => `${label}: ${note}`));
  }

  /* The ledger as a sentence: where the unwatched time went. Drawn wherever
     the envelope carries one, and it states its own total like every other
     list in this product. */
  function ledgerNotes(ledger) {
    if (!ledger) return [];
    const causes = ledger.seconds_by_cause || {};
    const names = Object.keys(causes);
    const where = names.length
      ? names.map((c) => `${c.replace(/_/g, " ")} ${duration(causes[c])}`).join(", ")
      : "nothing — every second of this window was watched";
    return [`${duration(ledger.observed_seconds)} watched of `
            + `${duration(ledger.window_seconds)}: ${where}`];
  }

  /* Rule 4, the axis half. A y-axis is zero-based unless the caller says
     otherwise, and one that is not says so on itself: a truncated axis turns
     a one per cent wobble into a cliff, and nothing else in the picture tells
     a reader which of the two they are looking at. */
  function yScale(values, options = {}) {
    const numbers = values.filter(isNum);
    let lo = numbers.length ? Math.min(...numbers) : 0;
    let hi = numbers.length ? Math.max(...numbers) : 1;
    const zero = options.zero !== false && lo >= 0;
    if (zero) lo = 0;
    if (has(options.min)) lo = options.min;
    if (has(options.max)) hi = options.max;
    if (hi === lo) { hi += 0.5; if (!zero) lo -= 0.5; }
    if (!zero) { const pad = (hi - lo) * 0.08; lo -= pad; hi += pad; }
    return {
      lo, hi, zero,
      note: zero ? null
        : `y starts at ${lo.toFixed(Math.abs(hi - lo) < 10 ? 1 : 0)}, not 0 — `
          + `read the spread, not the height`,
    };
  }

  /* Rule 4, the denominator half. A count per bucket is a rate, and a rate
     without its denominator is a number nobody can check. The envelope names
     the bucket it grouped into; the axis label says it out loud. */
  function axisLabel(envelope, options) {
    const label = (options.y && options.y.label) || null;
    const per = (options.y && options.y.per)
      || (has(envelope.bucket_seconds) ? `${duration(envelope.bucket_seconds)} bucket` : null)
      || (has(envelope.bin_width) ? `bin of ${envelope.bin_width}` : null);
    if (!label) return { text: per ? `per ${per}` : null, per };
    return { text: per ? `${label} per ${per}` : label, per };
  }

  /* ---------- the frame every shape is drawn in ----------
     The plot, then the footer: the total, any axis choice, the unknown the
     chart is carrying, the coverage, and the ledger when there is one. Those
     five sentences are assembled once and used twice — painted under the plot
     and concatenated into the `<desc>` — so the screen and the screen reader
     cannot come to disagree. */
  function frame(kind, envelope, options, shape) {
    const width = Math.max(options.width || 0, 360);
    const plan = shape(envelope, options, width);
    const cover = coverageOf(envelope);
    const notes = [];
    const push = (cls, text) => { if (text) notes.push({ cls, text }); };
    push("chart-total", plan.total);
    push("chart-axis-note", windowNote(envelope.window));
    for (const note of plan.axes || []) push("chart-axis-note", note);
    push("chart-unknown-note", plan.unknown);
    push("chart-coverage", cover.text);
    for (const note of ledgerNotes(envelope.ledger)) push("chart-ledger", note);
    for (const note of plan.notes || []) push("chart-withheld", note);

    const lineH = 14;
    const height = plan.height + (notes.length ? 8 + notes.length * lineH : 0);
    const id = `fs-chart-${++serial}`;
    const node = svg(width, height);
    node.setAttribute("class", "fs-chart");
    node.setAttribute("data-kind", kind);
    node.setAttribute("role", "img");
    node.setAttribute("aria-labelledby", `${id}-title ${id}-desc`);
    /* Rule 5 and rule 2 as machine-readable facts, so a test — or the AI tab,
       which has to say what it drew — reads them off the chart itself rather
       than off the payload it hopes the chart used. */
    node.setAttribute("data-total", plan.total || "");
    node.setAttribute("data-coverage", cover.attr);
    node.setAttribute("data-coverage-kind", cover.kind);
    if (plan.unknown) node.setAttribute("data-carries-unknown", "true");

    const title = plan.title || `${kind} chart`;
    add(node, "title", { id: `${id}-title` }, title);
    /* The same sentences as the footer, as prose: a screen reader is given
       the total, the axis choices, the unknown and the coverage in the order
       a sighted reader meets them. */
    add(node, "desc", { id: `${id}-desc` },
        [title].concat(notes.map((n) => n.text))
          .map((line) => (/[.!?—]$/.test(line) ? line : `${line}.`)).join(" "));

    const marks = add(node, "g", { class: "chart-plot" });
    plan.paint(marks, { hatch: hatch(node, `${id}-unknown`), width, id });

    notes.forEach((note, index) => {
      add(node, "text", { x: 4, y: plan.height + 8 + index * lineH + 10, class: note.cls },
          note.text);
    });
    return node;
  }

  /* ---------- a line series, with the unknown drawn as unknown ----------

     Fed `/analysis/tag/{code}` ({points: [{t, mean, min, max}]}) or
     `/analysis/production` ({points: [{t, good, scrap}], bucket_seconds}), or
     anything else shaped that way, with `options.value` naming the field to
     plot. The field is never guessed: picking one would be the chart choosing
     what the reader is looking at.

     A point whose value is null, and any stretch named in `envelope.unknown`,
     is hatched and breaks the line. That is the whole reason this shape
     exists rather than a polyline: joining two readings across an hour nobody
     watched draws a measurement of that hour, and there wasn't one. */
  function lineShape(envelope, options, width) {
    const series = envelope.series
      || [{ label: options.label || envelope.tag || envelope.equipment || "series",
            points: envelope.points || [],
            coverage_note: envelope.coverage_note, coverage_floor: envelope.coverage_floor }];
    const valueKey = options.value
      || keyOf((series[0].points || [])[0], ["value", "mean", "count"])
      /* Nothing to inspect and nothing to draw: an empty series still has to
         state its total, so it gets a name for a field that is not there. */
      || ((series[0].points || []).length ? null : "value");
    if (!valueKey) {
      throw new TypeError(
        "kit.chart('line'): options.value must name the field to plot — a chart "
        + "that picked one would be choosing what the reader is looking at");
    }
    const band = options.band || null;

    const left = 52, right = 14, top = 14, bottom = 26;
    const height = (options.height || 200);
    const plot = width - left - right;
    const plotH = height - top - bottom;
    const timed = Boolean(envelope.window && envelope.window.start);
    const start = timed ? utc(envelope.window.start).getTime() : 0;
    const end = timed ? utc(envelope.window.end).getTime() : 1;
    const span = Math.max(end - start, 1);

    const all = [];
    for (const s of series) {
      for (const p of s.points || []) {
        if (isNum(p[valueKey])) all.push(p[valueKey]);
        if (band) { if (isNum(p[band.low])) all.push(p[band.low]);
                    if (isNum(p[band.high])) all.push(p[band.high]); }
      }
    }
    const scale = yScale(all, options);
    const label = axisLabel(envelope, options);
    /* One x per point, worked out once: a lookup by object identity was
       O(n squared) and wrong the moment two buckets were equal. */
    const xsOf = (points) => points.map((p, index) => (timed
      ? left + ((utc(p.t).getTime() - start) / span) * plot
      : left + (points.length > 1 ? (index / (points.length - 1)) * plot : plot / 2)));
    const yAt = (v) => top + plotH - ((v - scale.lo) / (scale.hi - scale.lo)) * plotH;

    /* Every stretch the chart will hatch: a run of null readings, plus the
       spans the envelope itself declares unwatched. */
    const gaps = [];
    for (const s of series) {
      const points = s.points || [];
      const xs = xsOf(points);
      let run = null;
      points.forEach((p, index) => {
        if (isNum(p[valueKey])) {
          if (run) { gaps.push({ from: run.from, to: xs[index], cause: "no reading" }); run = null; }
        } else if (!run) {
          run = { from: xs[Math.max(index - 1, 0)] };
        }
      });
      if (run) gaps.push({ from: run.from, to: left + plot, cause: "no reading" });
    }
    for (const gap of envelope.unknown || []) {
      gaps.push({
        from: timed ? left + ((utc(gap.start).getTime() - start) / span) * plot : left,
        to: timed ? left + ((utc(gap.end).getTime() - start) / span) * plot : left + plot,
        cause: gap.cause || gap.reason || "nobody was watching",
      });
    }
    const withheld = series.filter(isWithheld);
    const drawn = series.length - withheld.length;
    /* Rule 5. Describing the picture, not measuring the plant: how many
       readings went on it, and how many of the envelope's points had no
       reading to draw. A line of four points over a window with a hole in it
       is not a line of five. */
    const readings = series.reduce((n, s) => n + (s.points || [])
      .filter((p) => isNum(p[valueKey])).length, 0);
    const blind = series.reduce((n, s) => n + (s.points || [])
      .filter((p) => !isNum(p[valueKey])).length, 0);

    return {
      height,
      title: `${label.text || "series"} over the window`,
      /* Rule 5: how many series were drawn, out of how many the envelope
         carried, and how many were withheld rather than drawn. */
      total: [`${things(readings, "reading")} drawn`,
              blind ? `${count(blind)} with no reading` : null,
              `${things(drawn, "series")} of ${count(series.length)}`,
              withheld.length ? `${count(withheld.length)} withheld below the coverage floor` : null,
             ].filter(Boolean).join(" · "),
      axes: [scale.note, label.text ? `y: ${label.text}` : null].filter(Boolean),
      unknown: gaps.length
        ? `${things(gaps.length, "stretch", "stretches")} hatched: the line is broken there `
          + `because there was no reading, not because the value was zero`
        : null,
      notes: withheld.flatMap((s) => withheldNotes(s, s.label)),
      paint(g, ctx) {
        for (let i = 0; i <= 3; i++) {
          const value = scale.lo + ((scale.hi - scale.lo) * i) / 3;
          add(g, "line", { x1: left, y1: yAt(value), x2: left + plot, y2: yAt(value), class: "grid-line" });
          add(g, "text", { x: left - 6, y: yAt(value) + 3, class: "axis", "text-anchor": "end" },
              value.toFixed(Math.abs(scale.hi - scale.lo) < 10 ? 1 : 0));
        }
        if (timed) timeTicks(g, start, end, plot, top + plotH, left);

        /* The hatched stretches go down first, so the marks on either side
           sit over them rather than under. */
        for (const gap of gaps) {
          const rect = add(g, "rect", {
            x: Math.min(gap.from, gap.to), y: top,
            width: Math.max(Math.abs(gap.to - gap.from), 2), height: plotH,
            fill: ctx.hatch, "data-unknown": "true", class: "chart-unknown",
          });
          add(rect, "title", {}, `unknown — ${gap.cause}`);
        }

        for (const s of series) {
          if (isWithheld(s)) {
            /* Withheld is not a smaller line. Full width, hatched, and the
               ledger prints under the chart (rule 2, decision 0033). */
            const rect = add(g, "rect", {
              x: left, y: top, width: plot, height: plotH, fill: ctx.hatch,
              "data-withheld": "true", "data-unknown": "true", class: "chart-unknown",
            });
            add(rect, "title", {}, s.coverage_note);
            continue;
          }
          const points = s.points || [];
          const xs = xsOf(points);
          if (band) {
            /* The band is what stops a one-second excursion disappearing into
               an average, so it is drawn first and the mean goes over it. */
            const upper = points.map((p, i) => [xs[i], p[band.high]]).filter(([, v]) => isNum(v));
            const lower = points.map((p, i) => [xs[i], p[band.low]]).filter(([, v]) => isNum(v)).reverse();
            if (upper.length && lower.length) {
              const outline = upper.concat(lower).map(([x, v]) => `${x},${yAt(v)}`);
              add(g, "polygon", { points: outline.join(" "), class: "trend-band" });
            }
          }
          /* One polyline per run of readings — never one through the gaps. */
          let run = [];
          const flush = () => {
            if (run.length > 1) add(g, "polyline", { points: run.join(" "), class: "trend-line" });
            else if (run.length === 1) {
              const [x, y] = run[0].split(",");
              add(g, "circle", { cx: x, cy: y, r: 2, class: "series-count" });
            }
            run = [];
          };
          points.forEach((p, index) => {
            const value = p[valueKey];
            const x = xs[index];
            if (!isNum(value)) { flush(); 
              add(g, "line", { x1: x, y1: top, x2: x, y2: top + plotH, class: "chart-break",
                               "data-unknown": "true", "data-t": raw(p.t) });
              return; }
            run.push(`${x},${yAt(value)}`);
            /* Rule 1: the envelope's own number, on the mark that drew it. */
            const dot = add(g, "circle", {
              cx: x, cy: yAt(value), r: 1.8, class: "series-count",
              "data-value": raw(value), "data-t": raw(p.t), "data-series": s.label,
            });
            add(dot, "title", {}, `${s.label} ${raw(value)}${p.t ? ` at ${FS.fmt.clock(p.t)}` : ""}`);
          });
          flush();
        }
      },
    };
  }

  /* ---------- bars, and the pareto that is bars plus a cumulative line ------

     Fed `/analysis/downtime` ({reasons: [{reason, seconds, share, cumulative,
     events}], total_seconds, unlabelled_share, machines_total}) or any list of
     rows with a label and a value.

     The bar length is the only thing laid out here; the cumulative line is the
     envelope's own `cumulative`, plotted, never re-derived. An unlabelled
     bucket is hatched, because unlabelled stops are not a reason and filing
     them under one is how a plant convinces itself it has data it hasn't got
     (house rule 3). */
  function barsShape(envelope, options, width) {
    const rows = options.rows || envelope.rows || envelope.reasons || envelope.stations || [];
    const labelKey = options.labelKey || keyOf(rows[0], ["label", "reason", "code", "name"]) || "label";
    const valueKey = options.valueKey || keyOf(rows[0], ["value", "seconds", "count"]) || "value";
    const asDuration = options.format
      ? options.format === "duration"
      : valueKey === "seconds" || valueKey.endsWith("_seconds");
    const say = (v) => (asDuration ? duration(v) : count(v));

    const left = options.labelWidth || 104, right = 44, top = 8, barH = 22, gap = 7;
    const plot = width - left - right;
    const height = Math.max(top + rows.length * (barH + gap) + 6, top + barH + 6);
    /* The longest bar is the scale. Laying out is allowed; the number beside
       each bar is still the envelope's own. */
    const biggest = Math.max(...rows.map((r) => (isNum(r[valueKey]) ? r[valueKey] : 0)), 0) || 1;
    /* Unlabelled is not a reason (house rule 3), a row with no figure is not a
       zero, and a withheld row is not a small one: all three are drawn in the
       hatch, and this is the one predicate that decides it — so the note under
       the chart and the marks on it can never disagree about how many. */
    const isUnlabelled = (r) => String(r[labelKey]).toLowerCase() === "unlabelled";
    const isUnknownRow = (r) => !isNum(r[valueKey]) || isWithheld(r) || isUnlabelled(r);
    const unlabelled = rows.filter(isUnlabelled);
    const withheld = rows.filter(isWithheld);
    const unknownRows = rows.filter(isUnknownRow);

    const totalText = has(envelope.total_seconds) ? `${duration(envelope.total_seconds)} in total`
      : has(envelope.total) ? `${count(envelope.total)} in total` : null;
    const noun = options.noun || (envelope.reasons ? "reason" : "row");
    const of = has(options.rowsTotal) ? options.rowsTotal
      : has(envelope.rows_total) ? envelope.rows_total : rows.length;

    return {
      height,
      title: options.title || (envelope.reasons ? "Downtime by reason, worst first" : "bars"),
      total: [totalText,
              `showing ${count(rows.length)} of ${things(of, noun)}`,
              unlabelled.length && has(envelope.unlabelled_share)
                ? `${FS.fmt.pct(envelope.unlabelled_share)} of it unlabelled`
                : null,
              has(envelope.machines_total) ? `across ${things(envelope.machines_total, "machine")}` : null,
             ].filter(Boolean).join(" · "),
      /* Rule 4: the bar lengths are a share of the longest row, which is a
         choice about the shape, so the chart says which row that was. */
      axes: [rows.length ? `x: bar length is a share of the longest, ${say(biggest)}` : null,
             options.cumulative && rows.some((r) => has(r.cumulative))
               ? "the dashed line is the envelope's own cumulative share of the total"
               : null,
             asDuration ? null : (axisLabel(envelope, options).text
                                  ? `x: ${axisLabel(envelope, options).text}` : null),
            ].filter(Boolean),
      unknown: unknownRows.length
        ? `${things(unknownRows.length, "row")} hatched: `
          + (unlabelled.length ? "unlabelled stops are reported as unlabelled, not filed under a reason"
                               : "no figure was measured for them")
        : null,
      notes: withheld.flatMap((r) => withheldNotes(r, String(r[labelKey]))),
      paint(g, ctx) {
        rows.forEach((row, index) => {
          const y = top + index * (barH + gap);
          const name = String(row[labelKey]);
          add(g, "text", { x: left - 8, y: y + barH / 2 + 4, class: "row-label", "text-anchor": "end" },
              name.length > 15 ? name.slice(0, 14) + "…" : name);
          const value = row[valueKey];
          const unknown = isUnknownRow(row);
          /* A row with no figure, and a row this plant's floor withholds, are
             drawn at FULL width in the hatch. A shorter bar would read as a
             smaller number, and what actually happened is that nobody saw it
             (rule 2). */
          const w = (!isNum(value) || isWithheld(row))
            ? plot
            : Math.max((value / biggest) * plot, 2);
          const rect = add(g, "rect", {
            x: left, y, width: w, height: barH, rx: 3,
            class: unknown ? "chart-unknown" : "bar-mark",
            ...(unknown ? { fill: ctx.hatch } : {}),
            opacity: unknown ? 1 : 0.85,
            "data-value": raw(value),
            "data-label": name,
            ...(unknown ? { "data-unknown": "true" } : {}),
            ...(isWithheld(row) ? { "data-withheld": "true" } : {}),
          });
          add(rect, "title", {},
              `${name}: ${isNum(value) ? say(value) : "unknown"}`
              + (has(row.events) ? ` over ${things(row.events, "stop")}` : "")
              + (row.coverage_note ? `\n${row.coverage_note}` : ""));
          add(g, "text", { x: left + w + 6, y: y + barH / 2 + 4, class: "bar-label" },
              isNum(value) ? say(value) : "unknown");
        });

        /* The pareto's cumulative share: the envelope's own numbers, plotted.
           Nothing here adds anything up. */
        if (options.cumulative && rows.length > 1) {
          const points = rows
            .map((r, i) => (has(r.cumulative)
              ? { x: left + r.cumulative * plot, y: top + i * (barH + gap) + barH / 2, v: r.cumulative }
              : null))
            .filter(Boolean);
          if (points.length > 1) {
            add(g, "polyline", { points: points.map((p) => `${p.x},${p.y}`).join(" "), class: "cum-line" });
            for (const p of points) {
              add(g, "circle", { cx: p.x, cy: p.y, r: 1.8, class: "cum-line",
                                 "data-cumulative": raw(p.v) });
            }
          }
        }
      },
    };
  }

  /* ---------- the state timeline, stacked along the clock ----------

     Fed `/analysis/timeline` ({machines: [{code, intervals: [{state, reason,
     start, end, seconds}]}], machines_shown, machines_total}). One row per
     machine, each row a stack of intervals in the order the clock put them.

     `disconnected` and `unknown` are hatched rather than coloured: a solid
     grey block in a Gantt is the same shape as a measurement, and this one
     means nobody was looking (rule 3, decision 0030). */
  function statesShape(envelope, options, width) {
    const rows = options.rows || envelope.machines || envelope.rows || [];
    if (!envelope.window || !envelope.window.start || !envelope.window.end) {
      throw new TypeError(
        "kit.chart('states'): needs envelope.window {start, end} — a timeline with no "
        + "window would be drawing intervals against an axis it invented");
    }
    const left = 82, right = 14, rowH = options.rowHeight || 24, gap = 6, top = 8;
    const plot = width - left - right;
    const height = Math.max(top + rows.length * (rowH + gap) + 22, top + rowH + 22);
    const start = utc(envelope.window.start).getTime();
    const end = utc(envelope.window.end).getTime();
    const span = Math.max(end - start, 1);
    const UNKNOWN = new Set(["disconnected", "unknown"]);
    const intervals = rows.flatMap((r) => r.intervals || []);
    const blind = intervals.filter((i) => UNKNOWN.has(i.state));
    const withheld = rows.filter(isWithheld);
    /* Three numbers, because they are three different facts: the rows on the
       picture, the rows the payload carried (a machine that reported nothing
       in this window is one of those, and a caller may leave it off), and the
       machines the line has. A Gantt of six machines on a line of eleven is
       not a picture of the line, and nothing in the picture says so. */
    const carried = (envelope.machines || envelope.rows || rows).length;
    const of = has(envelope.machines_total) ? envelope.machines_total : carried;

    return {
      height,
      title: "Equipment state over the window, one row per machine",
      /* Rule 5, and the reason the payload carries `machines_total` at all: a
         Gantt of six machines on a line of eleven is not a picture of the
         line, and a reader cannot tell from the picture. */
      total: [`${count(rows.length)} of ${things(of, "machine")} drawn`,
              carried > rows.length
                ? `${count(carried - rows.length)} reported nothing in this window`
                : null,
              things(intervals.length, "interval"),
              withheld.length
                ? `${count(withheld.length)} withheld below the coverage floor` : null,
             ].filter(Boolean).join(" · "),
      axes: [],
      unknown: blind.length
        ? `${things(blind.length, "stretch", "stretches")} hatched: the MES had lost sight of `
          + `the machine, which is not the same as the machine standing still`
        : null,
      notes: withheld.flatMap((r) => withheldNotes(r, r.code || r.label || "")),
      paint(g, ctx) {
        timeTicks(g, start, end, plot, top + rows.length * (rowH + gap), left);
        rows.forEach((row, index) => {
          const y = top + index * (rowH + gap);
          add(g, "text", { x: left - 8, y: y + rowH / 2 + 4, class: "row-label", "text-anchor": "end" },
              row.code || row.label || "");
          if (isWithheld(row)) {
            const rect = add(g, "rect", {
              x: left, y, width: plot, height: rowH, rx: 2, fill: ctx.hatch,
              class: "chart-unknown", "data-withheld": "true", "data-unknown": "true",
              "data-label": row.code || row.label || "",
            });
            add(rect, "title", {}, row.coverage_note);
            return;
          }
          for (const interval of row.intervals || []) {
            const x0 = left + ((utc(interval.start).getTime() - start) / span) * plot;
            const x1 = left + ((utc(interval.end).getTime() - start) / span) * plot;
            const unknown = UNKNOWN.has(interval.state);
            const rect = add(g, "rect", {
              x: x0, y, width: Math.max(x1 - x0, 1), height: rowH, rx: 2,
              class: unknown ? "chart-unknown" : "state-mark",
              fill: unknown ? ctx.hatch : `var(--${interval.state}, var(--unknown))`,
              opacity: unknown ? 1 : (interval.state === "running" ? 0.85 : 0.95),
              "data-state": interval.state,
              "data-value": raw(interval.seconds),
              "data-label": row.code || row.label || "",
              ...(unknown ? { "data-unknown": "true" } : {}),
            });
            add(rect, "title", {},
                `${row.code || row.label || ""} ${interval.state}`
                + `${interval.reason ? ` (${interval.reason})` : ""}\n`
                + `${duration(interval.seconds)} from ${FS.fmt.clock(interval.start)}`);
          }
        });
      },
    };
  }

  /* ---------- a histogram, on axes that admit their own choices ----------

     Fed `{bins: [{from, to, count}], bin_width, n, n_total, outside: {below,
     above}}`. It will NOT bin a series for you: a bin width is a choice that
     decides the shape of the picture, and a chart that made that choice
     silently would be drawing a distribution the MES never computed (rule 1,
     and rule 4's "a chart whose shape depends on a choice states the
     choice"). The bins come from the API, and the width is printed. */
  function histogramShape(envelope, options, width) {
    const bins = options.bins || envelope.bins;
    if (!Array.isArray(bins)) {
      throw new TypeError(
        "kit.chart('histogram'): needs envelope.bins — [{from, to, count}]. This kit "
        + "does not bin a series: the bin width decides the shape, so it is the "
        + "API's choice to make and to state, not the chart's");
    }
    const left = 46, right = 14, top = 14, bottom = 28;
    const height = options.height || 200;
    const plot = width - left - right;
    const plotH = height - top - bottom;
    /* A count axis always starts at zero. A truncated count axis is the one
       case rule 4 does not let a caller opt out of: half a bar is half a
       reading, and there is no such thing. */
    const scale = yScale(bins.map((b) => b.count), { zero: true, min: 0 });
    const lowEdge = bins.length ? bins[0].from : 0;
    const highEdge = bins.length ? bins[bins.length - 1].to : 1;
    const xSpan = Math.max(highEdge - lowEdge, 1e-9);
    const blind = bins.filter((b) => !isNum(b.count));
    const outside = envelope.outside || {};
    const unit = envelope.unit ? ` ${envelope.unit}` : "";
    const binWidth = has(envelope.bin_width) ? envelope.bin_width
      : (bins.length ? Number((bins[0].to - bins[0].from).toFixed(6)) : null);

    return {
      height,
      title: options.title || `Distribution of ${envelope.characteristic || "the readings"}`,
      total: [has(envelope.n)
                ? (has(envelope.n_total) && envelope.n_total !== envelope.n
                    ? `${count(envelope.n)} of ${things(envelope.n_total, "reading")}`
                    : things(envelope.n, "reading"))
                : things(bins.length, "bin"),
              `${things(bins.length, "bin")} drawn`,
              has(outside.below) || has(outside.above)
                ? `${count((outside.below || 0) + (outside.above || 0))} outside the axis `
                  + `(${count(outside.below || 0)} below, ${count(outside.above || 0)} above)`
                : null,
             ].filter(Boolean).join(" · "),
      /* The choice the shape depends on, stated (rule 4). */
      axes: [has(binWidth) ? `bins of ${binWidth}${unit} from ${lowEdge}${unit} to ${highEdge}${unit}` : null,
             `y: readings per bin, from 0`,
            ].filter(Boolean),
      unknown: blind.length
        ? `${things(blind.length, "bin")} hatched: no count was measured for them, which is `
          + `not a count of zero`
        : null,
      notes: [],
      paint(g, ctx) {
        for (let i = 0; i <= 2; i++) {
          const value = scale.lo + ((scale.hi - scale.lo) * i) / 2;
          const y = top + plotH - ((value - scale.lo) / (scale.hi - scale.lo)) * plotH;
          add(g, "line", { x1: left, y1: y, x2: left + plot, y2: y, class: "grid-line" });
          add(g, "text", { x: left - 6, y: y + 3, class: "axis", "text-anchor": "end" }, count(value));
        }
        for (const bin of bins) {
          const x0 = left + ((bin.from - lowEdge) / xSpan) * plot;
          const x1 = left + ((bin.to - lowEdge) / xSpan) * plot;
          const unknown = !isNum(bin.count);
          const h = unknown ? plotH
            : ((bin.count - scale.lo) / (scale.hi - scale.lo)) * plotH;
          const rect = add(g, "rect", {
            x: x0 + 0.5, y: top + plotH - h, width: Math.max(x1 - x0 - 1, 1), height: Math.max(h, 1),
            class: unknown ? "chart-unknown" : "series-count",
            ...(unknown ? { fill: ctx.hatch, "data-unknown": "true" } : {}),
            "data-value": raw(bin.count),
            "data-from": raw(bin.from), "data-to": raw(bin.to),
          });
          add(rect, "title", {},
              `${bin.from}${unit} to ${bin.to}${unit}: `
              + (unknown ? "no count measured" : things(bin.count, "reading")));
        }
        /* Both edges, and the spec limits when the envelope names them. */
        for (const [key, text] of [["lower_spec", "lower spec"], ["upper_spec", "upper spec"]]) {
          if (!has(envelope[key])) continue;
          const x = left + ((envelope[key] - lowEdge) / xSpan) * plot;
          add(g, "line", { x1: x, y1: top, x2: x, y2: top + plotH, class: "limit-line" });
          add(g, "text", { x, y: top - 3, class: "axis", "text-anchor": "middle" },
              `${text} ${envelope[key]}`);
        }
        for (const [x, value] of [[left, lowEdge], [left + plot, highEdge]]) {
          add(g, "text", { x, y: top + plotH + 14, class: "axis",
                           "text-anchor": x === left ? "start" : "end" }, `${value}${unit}`);
        }
      },
    };
  }

  const SHAPES = { line: lineShape, bars: barsShape, states: statesShape, histogram: histogramShape };

  /* The one entry point. `pareto` is `bars` with the cumulative line on. */
  function chart(kind, envelope, options = {}) {
    const name = kind === "pareto" ? "bars" : kind;
    const shape = SHAPES[name];
    if (!shape) {
      throw new TypeError(`kit.chart: no such chart kind: ${kind} `
                          + `(${Object.keys(SHAPES).join(", ")}, pareto)`);
    }
    if (!envelope || typeof envelope !== "object") {
      throw new TypeError("kit.chart: an envelope is required — this draws what the API "
                          + "measured and has nothing of its own to draw");
    }
    const opts = kind === "pareto" ? { cumulative: true, ...options } : options;
    return frame(name, envelope, opts, shape);
  }

  /* Into a host, the way a screen uses it: the width comes from the box the
     chart is going into, because these are laid out in pixels. */
  function draw(host, kind, envelope, options = {}) {
    host.replaceChildren();
    const node = chart(kind, envelope,
                       { width: Math.max(host.clientWidth || 0, 360), ...options });
    host.appendChild(node);
    return node;
  }

  FS.kit = { utc, duration, age, svg, add, empty, timeTicks, timeline, trend, oeeBars,
             coverageRow, ledgerSummary,
             /* The chart contract: one entry point, four shapes, six rules. */
             chart, draw };
})();
