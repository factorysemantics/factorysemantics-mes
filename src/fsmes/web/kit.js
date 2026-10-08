/* The screen kit: the charts and controls every object page draws.

   analysis.js and quality.js each grew their own SVG helpers; a third copy
   for the machine page would have made the drift permanent. These are the
   one set. Hand-drawn SVG, no library - a plant PC renders this for years
   without a toolchain (principle 5). Nothing here computes a number: a chart
   draws what the API measured, and draws "unknown" when it did not.

   The second half of this file is the CHART CONTRACT - `FS.kit.chart(kind,
   envelope, options)`, the five shapes an analysis exploration needs, drawn
   against the six rules of docs/design/agentic-harness.md SS9 M1 and the
   chart contract in docs/design/STYLE.md. It is here, and extended rather
   than duplicated, for the same reason the rest of this file is: `kit.js`
   exists so that two screens can never state coverage differently, and a
   second chart engine would make that drift permanent.

   That is also why the network graph of docs/design/deep-analysis.md SS7 is a
   fifth SHAPE rather than a vendored chart engine. The six rules are
   structural here: `frame()` writes data-total, data-coverage, the <title>
   and the <desc> and the footer for EVERY shape before the shape draws
   anything, so a new shape cannot forget them. A 4.8 MB engine would have
   inherited none of it, and would not have drawn the graph either - it has no
   force layout. The layout is 90 hand-written lines below, and nothing new is
   vendored. */

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

     `kind` is "line", "bars" (alias "pareto"), "states", "histogram" or
     "graph".
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
     what a sighted reader sees cannot drift apart.

     AND THE READER CAN TOUCH IT (2026-09-29). Hovering a mark says what that
     mark's own `data-value` is and how much of the window anybody watched;
     the legend switches a kind out of the picture; the threshold moves; the
     time axis brushes. **Every one of those re-states the total**, in
     `data-total` and in the footer, because a filtered chart that kept the
     old total is a list that reads complete. That is the seventh thing the
     frame does for every shape, so a new shape cannot forget it either. */

  let serial = 0;

  /* A footer sentence longer than the chart is wide. Looking at the graph on
     2026-09-29 found its unknown note running off the right-hand edge — the
     sentence that names the holes is the longest one this kit writes, and a
     coverage sentence a reader cannot finish is the one failure mode the
     footer exists to prevent. Wrapped onto `<tspan>`s of one `<text>`, so the
     element still holds the whole sentence and `textContent` still equals
     `data-total`. */
  function wrapNote(text, width) {
    const per = Math.max(Math.floor((width - 10) / 5.6), 24);
    if (text.length <= per) return [text];
    const lines = [];
    let line = "";
    for (const word of String(text).split(" ")) {
      if (line && line.length + 1 + word.length > per) { lines.push(line); line = word; }
      else line = line ? `${line} ${word}` : word;
    }
    if (line) lines.push(line);
    return lines;
  }

  /* The envelope's own number, as a string, unrounded — what a test reads. */
  const raw = (v) => (v === null || v === undefined ? "" : String(v));
  /* What the reader has narrowed the picture to, as a shape sees it. A shape
     that is handed no view draws the whole envelope, which is what every
     caller from before the charts became interactive gets. */
  const viewOf = (options) => (options && options.view) || {};
  const hiddenIn = (options) => viewOf(options).hidden || new Set();
  /* A clock label from milliseconds, in the plant's own zone — the same
     `FS.fmt.clock` the rest of the kit uses, fed an instant rather than a row's
     timestamp string. */
  const clockAt = (ms) => FS.fmt.clock(new Date(ms).toISOString());
  const has = (v) => v !== null && v !== undefined;
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const count = (v) => (has(v) ? Math.round(v).toLocaleString() : "—");
  const things = (n, one, many) => `${count(n)} ${n === 1 ? one : many || one + "s"}`;
  /* The first key an envelope actually carries, so one shape can be fed the
     payload of the analysis it belongs to without a mapping layer. */
  const keyOf = (row, wanted) => wanted.find((k) => row && k in row);

  /* ---------- a moment the reader came here about ----------
     `options.markers: [{t, label}]` puts a rule on a time axis at an instant
     the reader is asking about — the reading a dossier panel is open on, say.

     It is not a measurement and it is not a filter: nothing leaves the
     picture, no total changes, and the marker's own time goes in the footer so
     the sentence and the rule cannot come to disagree. It is drawn only on a
     TIMED axis, because a marker on an axis of bucket positions would be
     pointing at an index rather than at a moment. A marker outside the stretch
     drawn — the reader brushed elsewhere — is not drawn and the footer says
     so, rather than being clamped to the edge, which would put the instant
     somewhere it was not. */
  const markersIn = (options, timed) =>
    (timed ? (options.markers || []) : []).filter((m) => m && m.t);

  function markerNote(markers, start, end) {
    if (!markers.length) return null;
    return markers.map((m) => {
      const at = utc(m.t).getTime();
      const label = m.label || "marked";
      return `${label} at ${clockAt(at)}`
             + (at >= start && at <= end ? "" : " — outside the stretch drawn");
    }).join(" · ");
  }

  /* Drawn last by its caller, so it sits over the marks rather than under. */
  function paintMarkers(g, markers, box) {
    for (const marker of markers) {
      const at = utc(marker.t).getTime();
      if (at < box.start || at > box.end) continue;
      const x = box.left + ((at - box.start) / Math.max(box.end - box.start, 1)) * box.plot;
      const rule = add(g, "line", {
        x1: x, y1: box.top, x2: x, y2: box.top + box.height,
        class: "chart-marker", "data-marker": raw(marker.t),
      });
      add(rule, "title", {}, `${marker.label || "marked"} — ${FS.fmt.clock(marker.t)}`);
      if (marker.label) {
        add(g, "text", { x: x + 4, y: box.top + 9, class: "chart-marker-label" },
            marker.label);
      }
    }
  }

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
    /* A payload may say "absent" in so many words rather than by leaving the
       field off — the plant's own trace-graph read does, because a graph of
       records is not a rate over a watched window and the route says so out
       loud. Without
       this the next line would read the word as a figure and print
       "Watched —% of the window", which is the exact confusion `data-coverage`
       exists to prevent. */
    if (envelope.coverage === "absent") {
      return { attr: "absent", kind: "absent", text: envelope.coverage_note || null };
    }
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
     The plot, then whatever the reader can touch, then the footer: the total,
     any axis choice, the filters the reader applied, the unknown the chart is
     carrying, the coverage, and the ledger when there is one. Those sentences
     are assembled once and used twice — painted under the plot and
     concatenated into the `<desc>` — so the screen and the screen reader
     cannot come to disagree.

     Since 2026-09-29 the frame also owns the interaction, for the same reason
     it owns the six rules: a shape describes what it can be narrowed to and
     the frame does the narrowing, so a new shape cannot invent a filter that
     forgets to re-state the total. Every one of them redraws through here.

     The controls are drawn INSIDE the `<svg>` rather than as HTML beside it.
     Three reasons: `FS.kit.chart()` still returns one node, so nothing that
     already draws a chart has to change; `ui-check` still watches one
     `svg.fs-chart`; and the export of §3 is that node's own `outerHTML`, so
     what lands in a slide carries the legend and the threshold the reader
     chose along with the footer that states them. They are keyboard-reachable
     and announce themselves (STYLE.md rule 6's intent — there is no
     `<button>` inside an SVG). */
  function frame(kind, envelope, options, shape) {
    const width = Math.max(options.width || 0, 360);
    const id = `fs-chart-${++serial}`;
    const node = svg(width, 10);
    node.setAttribute("class", "fs-chart");
    node.setAttribute("data-kind", kind);
    node.setAttribute("role", "img");
    node.setAttribute("aria-labelledby", `${id}-title ${id}-desc`);

    /* What the READER has narrowed the picture to. Not data: three choices,
       each of which changes what is drawn and therefore what the total says. */
    const view = {
      hidden: new Set(options.hidden || []),
      threshold: has(options.threshold) ? options.threshold : null,
      brush: options.brush || null,
    };
    let hoverLayer = null;

    function render() {
      const plan = shape(envelope, { ...options, view }, width);
      const cover = coverageOf(envelope);
      const notes = [];
      const push = (cls, text) => { if (text) notes.push({ cls, text }); };
      push("chart-total", plan.total);
      /* The ANSWER, where a shape's payload carries one the plant itself
         wrote: "out of control, not out of spec". Second, under the total,
         because a conclusion is read before the arithmetic behind it, and in
         the footer rather than in the title so it travels into the `<desc>`
         and into an export with everything else. A shape returns one only when
         the envelope stated it — nothing here concludes anything. */
      push("chart-verdict", plan.verdict);
      push("chart-axis-note", windowNote(envelope.window));
      for (const note of plan.axes || []) push("chart-axis-note", note);
      /* The reader's own choices, in the footer with everything else, because
         a filtered chart whose footer did not say so is a picture claiming to
         be the whole of something. */
      for (const note of plan.filters || []) push("chart-filter-note", note);
      push("chart-unknown-note", plan.unknown);
      push("chart-coverage", cover.text);
      for (const note of ledgerNotes(envelope.ledger)) push("chart-ledger", note);
      for (const note of plan.notes || []) push("chart-withheld", note);

      const lineH = 14;
      const wrapped = notes.map((n) => ({ ...n, lines: wrapNote(n.text, width) }));
      const footLines = wrapped.reduce((n, w) => n + w.lines.length, 0);
      const legend = options.legend === false ? [] : (plan.legend || []);
      const slider = options.slider === false ? null : (plan.slider || null);
      const legendRows = legend.length ? layOutLegend(legend, width) : [];
      const chromeTop = plan.height + (legendRows.length || slider ? 6 : 0);
      const chromeH = legendRows.length * 16 + (slider ? 26 : 0);
      const footTop = chromeTop + chromeH;
      const height = footTop + (notes.length ? 8 + footLines * lineH : 0);

      node.replaceChildren();
      node.setAttribute("height", height);
      node.setAttribute("viewBox", `0 0 ${width} ${height}`);
      /* Rule 5 and rule 2 as machine-readable facts, so a test — or the AI tab,
         which has to say what it drew — reads them off the chart itself rather
         than off the payload it hopes the chart used. Rewritten on every
         redraw: hiding a kind or moving a threshold changes the total, and the
         attribute is the total. */
      node.setAttribute("data-total", plan.total || "");
      node.setAttribute("data-coverage", cover.attr);
      node.setAttribute("data-coverage-kind", cover.kind);
      if (plan.unknown) node.setAttribute("data-carries-unknown", "true");
      else node.removeAttribute("data-carries-unknown");
      if (view.hidden.size || has(view.threshold) || view.brush) {
        node.setAttribute("data-filtered", "true");
      } else {
        node.removeAttribute("data-filtered");
      }

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

      if (legendRows.length) drawLegend(node, legendRows, chromeTop, view, render);
      if (slider) drawSlider(node, slider, chromeTop + legendRows.length * 16, view, render);
      if (plan.brushable) drawBrush(node, plan.brushable, view, render);

      /* `data-footer` and not the class: since the frame also draws a legend
         and a slider, "a text whose class starts with chart-" no longer means
         "a sentence this chart is making about itself", and the <desc> is
         assembled from these and only these. */
      let row = 0;
      for (const note of wrapped) {
        const text = add(node, "text", { x: 4, y: footTop + 8 + row * lineH + 10,
                                         class: note.cls, "data-footer": "true" });
        /* The leading space on a continuation line is deliberate: it keeps
           `textContent` equal to the sentence this note was assembled from,
           which is what the total and the <desc> are checked against. */
        note.lines.forEach((line, i) => add(text, "tspan",
          i ? { x: 4, dy: lineH } : { x: 4 }, i ? ` ${line}` : line));
        row += note.lines.length;
      }
      /* Last, so it paints over everything, and empty until a pointer is on a
         mark. Stripped from an export: a tooltip is where the reader's mouse
         happens to be, which is not a fact about the plant. */
      hoverLayer = add(node, "g", { class: "chart-hover", "data-chrome": "hover" });
    }

    /* ---- hover: the mark's own number, and what it is short of knowing ----
       Rule 1 in the hand: what appears is the `data-value` the mark already
       carries, never a recomputed one, with the chart's coverage sentence
       under it so a number and how much of the window it covers are read
       together. */
    function describe(mark) {
      const value = mark.getAttribute("data-value");
      const label = mark.getAttribute("data-label") || mark.getAttribute("data-series")
                 || mark.getAttribute("data-node") || mark.getAttribute("data-edge-kind")
                 || mark.getAttribute("data-state") || "";
      const lines = [];
      lines.push(label ? `${label}: ${value === "" ? "unknown" : value}`
                       : (value === "" ? "unknown" : value));
      if (mark.getAttribute("data-state")) lines.push(mark.getAttribute("data-state"));
      if (mark.getAttribute("data-degree")) {
        lines.push(`degree ${mark.getAttribute("data-degree")}`);
      }
      if (mark.getAttribute("data-unknown") === "true") {
        lines.push(mark.getAttribute("data-withheld") === "true"
          ? "withheld — below this plant's coverage floor"
          : "unknown — nobody measured this");
      }
      /* Rule 2 in the hand: a number and how much of the window it covers are
         read together or not at all — INCLUDING when the answer is that this
         payload has no coverage figure on it, which is the third of the three
         facts `data-coverage` exists to tell apart. */
      const cover = coverageOf(envelope);
      lines.push(cover.text
        || "this payload carries no coverage figure — how much of the window was "
           + "watched is not part of what it measured");
      return { value, lines };
    }

    function showHover(mark, at) {
      if (!hoverLayer) return;
      const { value, lines: said } = describe(mark);
      /* Narrower than the footer: a tooltip as wide as the chart covers the
         chart it is describing. */
      const lines = said.flatMap((line) => wrapNote(line, 330));
      hoverLayer.replaceChildren();
      hoverLayer.setAttribute("data-hover-value", value === null ? "" : value);
      hoverLayer.setAttribute("data-hover-lines", String(lines.length));
      const texts = lines.map((text, i) => add(hoverLayer, "text", {
        x: 0, y: 12 + i * 12, class: i ? "chart-hover-note" : "chart-hover-value",
      }, text));
      const box = hoverLayer.getBBox();
      const pad = 5;
      const w = box.width + pad * 2;
      const h = box.height + pad * 2;
      const x = Math.max(2, Math.min(width - w - 2, at.x + 10));
      const y = Math.max(2, at.y - h - 8);
      const ground = document.createElementNS(NS, "rect");
      for (const [k, v] of Object.entries({ x: 0, y: 0, width: w, height: h, rx: 3,
                                            class: "chart-hover-box" })) {
        ground.setAttribute(k, v);
      }
      hoverLayer.insertBefore(ground, hoverLayer.firstChild);
      texts.forEach((t, i) => { t.setAttribute("x", pad); t.setAttribute("y", pad + 11 + i * 12); });
      hoverLayer.setAttribute("transform", `translate(${x} ${y})`);
    }

    function clearHover() {
      if (!hoverLayer) return;
      hoverLayer.replaceChildren();
      hoverLayer.removeAttribute("data-hover-value");
      hoverLayer.removeAttribute("data-hover-lines");
    }

    /* Delegated on the frame and attached once, because the plot is replaced
       on every redraw and a handler per mark would be re-attached with it. */
    node.addEventListener("pointermove", (event) => {
      let mark = event.target.closest ? event.target.closest("[data-value]") : null;
      if (!mark && document.elementsFromPoint) {
        /* A line's own stroke, a min/max band, a grid rule, a spec limit: none
           of them is a reading and every one of them sits over the mark that
           is. So the pointer looks THROUGH what it landed on for the topmost
           thing carrying a number — rather than each of those marks having to
           remember to opt out, which is the kind of rule that holds until
           somebody adds the next decoration. */
        mark = document.elementsFromPoint(event.clientX, event.clientY)
          .find((el) => node.contains(el) && el.hasAttribute
                        && el.hasAttribute("data-value")) || null;
      }
      if (!mark || !node.contains(mark)) { clearHover(); return; }
      showHover(mark, atPointer(node, event));
    });
    node.addEventListener("pointerleave", clearHover);
    /* A keyboard reaches the same sentence: a focused mark describes itself
       where it sits. */
    node.addEventListener("focusin", (event) => {
      const mark = event.target.closest ? event.target.closest("[data-value]") : null;
      if (!mark) return;
      const box = mark.getBBox ? mark.getBBox() : null;
      if (box) showHover(mark, { x: box.x + box.width, y: box.y });
    });

    render();
    /* What a page (D5) drives the chart with, and what a test reads back. */
    node.fsChart = {
      view,
      redraw: render,
      /* Set the whole view at once — one redraw, one new total. */
      apply(next) {
        if (next && "hidden" in next) view.hidden = new Set(next.hidden || []);
        if (next && "threshold" in next) view.threshold = next.threshold;
        if (next && "brush" in next) view.brush = next.brush;
        render();
        return node;
      },
    };
    return node;
  }

  /* Where the pointer is, in the chart's own units. `max-width: 100%` means a
     chart on a narrow screen is drawn at one scale and laid out at another. */
  function atPointer(node, event) {
    const box = node.getBoundingClientRect();
    const w = Number(node.getAttribute("width")) || box.width || 1;
    const h = Number(node.getAttribute("height")) || box.height || 1;
    return {
      x: (event.clientX - box.left) * (box.width ? w / box.width : 1),
      y: (event.clientY - box.top) * (box.height ? h / box.height : 1),
    };
  }

  /* ---------- the legend, which is a set of switches ----------
     Laid out in rows across the width the chart has; an entry is a swatch, a
     name and the entry's own figure. Clicking one takes that kind out of the
     picture AND out of the total, which is the whole point. */
  function layOutLegend(entries, width) {
    const rows = [];
    let row = [];
    let x = 4;
    for (const entry of entries) {
      const text = entry.note ? `${entry.label} (${entry.note})` : entry.label;
      const w = 16 + text.length * 5.6 + 12;
      if (row.length && x + w > width - 4) { rows.push(row); row = []; x = 4; }
      row.push({ ...entry, text, x, w });
      x += w;
    }
    if (row.length) rows.push(row);
    return rows;
  }

  function drawLegend(node, rows, top, view, redraw) {
    const g = add(node, "g", { class: "chart-legend", "data-chrome": "legend",
                               role: "group", "aria-label": "what is drawn" });
    rows.forEach((row, index) => {
      const y = top + index * 16;
      for (const entry of row) {
        const off = view.hidden.has(entry.key);
        const item = add(g, "g", {
          class: `chart-legend-item${off ? " off" : ""}`,
          "data-legend": entry.key, tabindex: "0", role: "switch",
          "aria-checked": off ? "false" : "true",
          "aria-label": `${entry.text}${off ? ", hidden" : ", drawn"}`,
        });
        add(item, "rect", { x: entry.x, y: y + 3, width: 9, height: 9, rx: 2,
                            class: `chart-legend-swatch ${entry.cls || "bar-mark"}` });
        add(item, "text", { x: entry.x + 14, y: y + 11, class: "chart-legend-text" },
            entry.text);
        const toggle = () => {
          if (view.hidden.has(entry.key)) view.hidden.delete(entry.key);
          else view.hidden.add(entry.key);
          redraw();
        };
        item.addEventListener("click", toggle);
        item.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
        });
      }
    });
  }

  /* ---------- the threshold, which is a choice the picture depends on ------
     Drawn rather than an <input range>, so it exports with the chart and so
     the number it is set to sits beside it in the same words the footer uses
     (rule 4). Arrow keys move it; so does a drag. */
  function drawSlider(node, slider, top, view, redraw) {
    const left = 4 + slider.label.length * 5.6 + 8;
    const right = Number(node.getAttribute("width")) - 60;
    const span = Math.max(right - left, 40);
    const [lo, hi] = slider.domain;
    const at = has(view.threshold) ? view.threshold : lo;
    const x = left + ((at - lo) / Math.max(hi - lo, 1e-9)) * span;
    const y = top + 13;
    const g = add(node, "g", { class: "chart-slider", "data-chrome": "slider" });
    add(g, "text", { x: 4, y: y + 4, class: "chart-legend-text" }, slider.label);
    add(g, "line", { x1: left, y1: y, x2: left + span, y2: y, class: "chart-slider-track" });
    add(g, "line", { x1: left, y1: y, x2: x, y2: y, class: "chart-slider-done" });
    add(g, "text", { x: left + span + 6, y: y + 4, class: "chart-legend-text" },
        slider.say ? slider.say(at) : String(at));
    const handle = add(g, "circle", {
      cx: x, cy: y, r: 6, class: "chart-slider-handle",
      tabindex: "0", role: "slider", "data-threshold": raw(at),
      "aria-valuemin": lo, "aria-valuemax": hi, "aria-valuenow": at,
      "aria-label": slider.label,
    });
    const step = slider.step || Math.max((hi - lo) / 20, 1);
    const set = (value) => {
      view.threshold = Math.max(lo, Math.min(hi, Math.round(value / step) * step));
      redraw();
    };
    const fromX = (px) => lo + ((px - left) / span) * (hi - lo);
    handle.addEventListener("keydown", (event) => {
      const by = { ArrowLeft: -step, ArrowDown: -step, ArrowRight: step, ArrowUp: step }[event.key];
      if (by === undefined) return;
      event.preventDefault();
      set((has(view.threshold) ? view.threshold : lo) + by);
    });
    const track = add(g, "rect", {
      x: left, y: y - 8, width: span, height: 16,
      class: "chart-grab", "data-chrome": "slider-track",
    });
    const drag = (event) => set(fromX(atPointer(node, event).x));
    for (const target of [track, handle]) {
      target.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        drag(event);
        const move = (e) => drag(e);
        const up = () => {
          node.removeEventListener("pointermove", move);
          node.removeEventListener("pointerup", up);
        };
        node.addEventListener("pointermove", move);
        node.addEventListener("pointerup", up);
      });
    }
  }

  /* ---------- the brush: narrowing the axis, and saying so ----------
     Drag across the plot to keep that stretch; click once to give it back.
     The shape does the narrowing — the frame only turns two pixel positions
     into two numbers in the shape's own domain. */
  function drawBrush(node, brushable, view, redraw) {
    const { x, width: w, y, height: h, domain } = brushable;
    /* BEHIND the plot, and catching no pointer of its own. A transparent
       surface over the marks would take every hover with it, and the first
       thing a reader does to a chart is point at a bar. The drag is caught on
       the frame instead and answered only inside this box. */
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "chart-brush");
    g.setAttribute("data-chrome", "brush");
    node.insertBefore(g, node.querySelector("g.chart-plot"));
    const toDomain = (px) => domain[0]
      + ((Math.max(x, Math.min(x + w, px)) - x) / Math.max(w, 1)) * (domain[1] - domain[0]);
    const toPixel = (v) => x + ((v - domain[0]) / Math.max(domain[1] - domain[0], 1e-9)) * w;
    if (view.brush) {
      /* The kept stretch, marked so a test can read the reader's own choice
         back off the picture. */
      add(g, "rect", {
        x: toPixel(view.brush[0]), y, height: h,
        width: Math.max(toPixel(view.brush[1]) - toPixel(view.brush[0]), 1),
        class: "chart-brush-span", "data-brush-from": raw(view.brush[0]),
        "data-brush-to": raw(view.brush[1]),
      });
    }
    const surface = add(g, "rect", {
      x, y, width: w, height: h, class: "chart-brush-surface",
      "data-brush-surface": "true",
      tabindex: "0", role: "button",
      "aria-label": view.brush
        ? "the axis is brushed — press Escape to show the whole window"
        : "drag across the plot to narrow the axis",
    });
    let from = null;
    let span = null;
    node.addEventListener("pointerdown", (event) => {
      const start = atPointer(node, event);
      if (start.x < x || start.x > x + w || start.y < y || start.y > y + h) return;
      event.preventDefault();
      from = start.x;
      span = add(g, "rect", { x: from, y, width: 1, height: h, class: "chart-brush-span" });
      const move = (e) => {
        const now = atPointer(node, e).x;
        span.setAttribute("x", Math.min(from, now));
        span.setAttribute("width", Math.max(Math.abs(now - from), 1));
      };
      span.setAttribute("data-brush-drawing", "true");
      const up = (e) => {
        node.removeEventListener("pointermove", move);
        node.removeEventListener("pointerup", up);
        const now = atPointer(node, e).x;
        /* A drag narrows; a click gives the whole window back. Four pixels is
           the difference between the two, and it is a click otherwise. */
        if (Math.abs(now - from) < 4) view.brush = null;
        else view.brush = [toDomain(Math.min(from, now)), toDomain(Math.max(from, now))];
        redraw();
      };
      node.addEventListener("pointermove", move);
      node.addEventListener("pointerup", up);
    });
    surface.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !view.brush) return;
      event.preventDefault();
      view.brush = null;
      redraw();
    });
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
    const carried = envelope.series
      || [{ label: options.label || envelope.tag || envelope.equipment || "series",
            points: envelope.points || [],
            coverage_note: envelope.coverage_note, coverage_floor: envelope.coverage_floor }];
    const valueKey = options.value
      || keyOf((carried[0].points || [])[0], ["value", "mean", "count"])
      /* Nothing to inspect and nothing to draw: an empty series still has to
         state its total, so it gets a name for a field that is not there. */
      || ((carried[0].points || []).length ? null : "value");
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
    const whole = {
      start: timed ? utc(envelope.window.start).getTime() : 0,
      end: timed ? utc(envelope.window.end).getTime() : 1,
    };
    /* The reader brushed a stretch of the clock. The axis narrows to it and
       the readings outside it leave the picture — and the total says how many
       did, because a chart zoomed in that kept the old total reads as the
       whole window. */
    const brush = timed ? viewOf(options).brush : null;
    const start = brush ? Math.max(whole.start, brush[0]) : whole.start;
    const end = brush ? Math.min(whole.end, brush[1]) : whole.end;
    const span = Math.max(end - start, 1);
    const inside = (p) => !brush || (utc(p.t).getTime() >= start && utc(p.t).getTime() <= end);
    const series = brush
      ? carried.map((s) => ({ ...s, points: (s.points || []).filter(inside) }))
      : carried;
    const outside = brush
      ? carried.reduce((n, s) => n + (s.points || []).filter((p) => !inside(p)).length, 0)
      : 0;

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
    const markers = markersIn(options, timed);
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
    /* A declared unwatched stretch may run off either edge of a brushed axis,
       so it is clipped to the plot rather than drawn outside it. */
    const onAxis = (x) => Math.max(left, Math.min(left + plot, x));
    for (const gap of envelope.unknown || []) {
      const from = timed ? onAxis(left + ((utc(gap.start).getTime() - start) / span) * plot) : left;
      const to = timed ? onAxis(left + ((utc(gap.end).getTime() - start) / span) * plot) : left + plot;
      if (to <= left || from >= left + plot || to === from) continue;
      gaps.push({ from, to, cause: gap.cause || gap.reason || "nobody was watching" });
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
              outside ? `${count(outside)} outside the brushed range` : null,
              `${things(drawn, "series")} of ${count(series.length)}`,
              withheld.length ? `${count(withheld.length)} withheld below the coverage floor` : null,
             ].filter(Boolean).join(" · "),
      axes: [scale.note, label.text ? `y: ${label.text}` : null,
             markerNote(markers, start, end)].filter(Boolean),
      /* Rule 4: the axis the reader is looking at is a choice they made, so
         the chart says which stretch of the clock it is showing. */
      filters: brush
        ? [`brushed to ${clockAt(start)}–${clockAt(end)} of `
           + `${clockAt(whole.start)}–${clockAt(whole.end)}`]
        : [],
      brushable: timed
        ? { x: left, width: plot, y: top, height: plotH,
            domain: [whole.start, whole.end], say: clockAt }
        : null,
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
               an average, so it is drawn first and the mean goes over it - and
               it breaks at the holes exactly as the line does. Drawn as one
               polygon across a gap it was a spread for half an hour nobody
               measured, painted over the hatch that said so; on a light theme
               that was the most visible thing on the chart. */
            let span = [];
            const flushBand = () => {
              if (span.length > 1) {
                const outline = span.map(([x, , hi]) => `${x},${yAt(hi)}`)
                  .concat(span.slice().reverse().map(([x, lo]) => `${x},${yAt(lo)}`));
                add(g, "polygon", { points: outline.join(" "), class: "trend-band" });
              }
              span = [];
            };
            points.forEach((p, i) => {
              if (isNum(p[band.low]) && isNum(p[band.high])) span.push([xs[i], p[band.low], p[band.high]]);
              else flushBand();
            });
            flushBand();
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
        /* Last, over the series: the moment the reader came here about. */
        paintMarkers(g, markers, { left, plot, top, height: plotH, start, end });
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
    const carried = options.rows || envelope.rows || envelope.reasons || envelope.stations || [];
    const labelKey = options.labelKey || keyOf(carried[0], ["label", "reason", "code", "name"]) || "label";
    const valueKey = options.valueKey || keyOf(carried[0], ["value", "seconds", "count"]) || "value";
    /* A row the reader switched off in the legend. It leaves the picture and
       it leaves the total with it — a filtered chart that kept the old total
       is a list that reads complete (rule 5, STYLE.md rule 4). */
    const hidden = hiddenIn(options);
    const rows = carried.filter((r) => !hidden.has(`row:${String(r[labelKey])}`));
    const off = carried.length - rows.length;
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
      : has(envelope.rows_total) ? envelope.rows_total : carried.length;

    return {
      height,
      title: options.title || (envelope.reasons ? "Downtime by reason, worst first" : "bars"),
      total: [totalText,
              `showing ${count(rows.length)} of ${things(of, noun)}`,
              off ? `${count(off)} switched off in the legend` : null,
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
      /* One toggle per row, because on this shape the rows ARE the kinds. */
      legend: carried.map((r) => ({
        key: `row:${String(r[labelKey])}`,
        label: String(r[labelKey]),
        note: isNum(r[valueKey]) ? say(r[valueKey]) : "unknown",
        cls: isUnknownRow(r) ? "chart-unknown-swatch" : "bar-mark",
      })),
      filters: off
        ? [`${things(off, "row")} switched off — every figure above is of what is drawn`]
        : [],
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
    const whole = { start: utc(envelope.window.start).getTime(),
                    end: utc(envelope.window.end).getTime() };
    const brush = viewOf(options).brush;
    const start = brush ? Math.max(whole.start, brush[0]) : whole.start;
    const end = brush ? Math.min(whole.end, brush[1]) : whole.end;
    const span = Math.max(end - start, 1);
    const UNKNOWN = new Set(["disconnected", "unknown"]);
    /* An interval that only overlaps the brushed stretch is DRAWN clipped and
       still carries its own full duration in `data-value`: the seconds are the
       envelope's number and the picture is the reader's choice, and rule 1
       says the two must not be confused. The footer says so. */
    const overlaps = (i) => utc(i.end).getTime() > start && utc(i.start).getTime() < end;
    const shown = (r) => (brush ? (r.intervals || []).filter(overlaps) : (r.intervals || []));
    const allIntervals = rows.flatMap((r) => r.intervals || []);
    const intervals = rows.flatMap(shown);
    const clipped = intervals.filter(
      (i) => utc(i.start).getTime() < start || utc(i.end).getTime() > end).length;
    const blind = intervals.filter((i) => UNKNOWN.has(i.state));
    const withheld = rows.filter(isWithheld);
    /* Three numbers, because they are three different facts: the rows on the
       picture, the rows the payload carried (a machine that reported nothing
       in this window is one of those, and a caller may leave it off), and the
       machines the line has. A Gantt of six machines on a line of eleven is
       not a picture of the line, and nothing in the picture says so. */
    const carried = (envelope.machines || envelope.rows || rows).length;
    const of = has(envelope.machines_total) ? envelope.machines_total : carried;
    const markers = markersIn(options, true);

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
              brush
                ? `${count(intervals.length)} of ${things(allIntervals.length, "interval")} `
                  + `in the brushed range`
                : things(intervals.length, "interval"),
              withheld.length
                ? `${count(withheld.length)} withheld below the coverage floor` : null,
             ].filter(Boolean).join(" · "),
      axes: [markerNote(markers, start, end)].filter(Boolean),
      filters: brush
        ? [`brushed to ${clockAt(start)}–${clockAt(end)} of `
           + `${clockAt(whole.start)}–${clockAt(whole.end)}`].concat(
            clipped ? [`${things(clipped, "interval")} run past the brushed edge — `
                       + `each is drawn short and still carries its whole duration`] : [])
        : [],
      brushable: { x: left, width: plot, y: top,
                   height: rows.length * (rowH + gap),
                   domain: [whole.start, whole.end], say: clockAt },
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
          for (const interval of shown(row)) {
            const on = (t) => Math.max(left, Math.min(left + plot,
              left + ((utc(t).getTime() - start) / span) * plot));
            const x0 = on(interval.start);
            const x1 = on(interval.end);
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
        /* Last, over the rows: the moment the reader came here about. */
        paintMarkers(g, markers, { left, plot, top,
                                   height: rows.length * (rowH + gap), start, end });
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
    const carried = options.bins || envelope.bins;
    if (!Array.isArray(carried)) {
      throw new TypeError(
        "kit.chart('histogram'): needs envelope.bins — [{from, to, count}]. This kit "
        + "does not bin a series: the bin width decides the shape, so it is the "
        + "API's choice to make and to state, not the chart's");
    }
    /* The reader brushed a stretch of the value axis. Whole bins only: half a
       bin is a count nobody measured, and slicing one would be the kit binning
       a series, which is exactly what it refuses to do. */
    const brush = viewOf(options).brush;
    const bins = brush
      ? carried.filter((b) => b.from >= brush[0] - 1e-9 && b.to <= brush[1] + 1e-9)
      : carried;
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
    const wholeLow = carried.length ? carried[0].from : 0;
    const wholeHigh = carried.length ? carried[carried.length - 1].to : 1;
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
              brush ? `${count(bins.length)} of ${things(carried.length, "bin")} drawn`
                    : `${things(bins.length, "bin")} drawn`,
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
      filters: brush
        ? [`brushed to ${lowEdge}${unit}–${highEdge}${unit} of `
           + `${wholeLow}${unit}–${wholeHigh}${unit}; whole bins only`]
        : [],
      brushable: { x: left, width: plot, y: top, height: plotH,
                   domain: [wholeLow, wholeHigh],
                   say: (v) => `${Number(v).toFixed(2)}${unit}` },
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
          /* A spec limit sits at the edge of the data far more often than not,
             so the label is anchored INTO the plot rather than centred on the
             rule: centred, "upper spec 101.5" was half off the right-hand side
             of the picture. */
          const near = 60;
          const anchor = x - left < near ? "start" : (left + plot - x < near ? "end" : "middle");
          add(g, "text", {
            x: anchor === "start" ? x + 2 : (anchor === "end" ? x - 2 : x),
            y: top - 3, class: "axis", "text-anchor": anchor,
          }, `${text} ${envelope[key]}`);
        }
        for (const [x, value] of [[left, lowEdge], [left + plot, highEdge]]) {
          add(g, "text", { x, y: top + plotH + 14, class: "axis",
                           "text-anchor": x === left ? "start" : "end" }, `${value}${unit}`);
        }
      },
    };
  }

  /* ---------- the network: nodes by kind, edges by kind with weight --------

     Fed the §7 envelope of `docs/design/deep-analysis.md`:

       { nodes: [{id, kind, label, weight, degree}],
         edges: [{from, to, kind, weight, unit, watched_seconds}],
         node_kinds: [{kind, nodes, note, unknown}],   // declared, empties too
         measures: {threshold, components, biggest, refused},
         showing: {nodes, edges}, total: {nodes, edges} }

     Four things about this shape are the design and not the drawing:

     **Every edge is a recorded fact.** The kit draws the edges the envelope
     carries and never an edge between two nodes that happen to be near each
     other. An edge nobody observed is not an edge, and a picture is where that
     rule is hardest to keep and easiest to break.

     **The holes are things with numbers on them.** `unattributed` and
     `unlabelled` are nodes, hatched, carrying their DEGREE rather than a
     weight — what a hole is, is what it touches — so a reader sees the three
     turns with no person rather than a tidy graph that is three turns short.

     **Empty node kinds are drawn empty.** A graph that silently omitted
     `screen` because no record names one would read as a complete picture of a
     plant where questions came from nowhere. They are drawn in their own row,
     outlined, carrying the zero the envelope stated.

     **Coverage is `absent`, always.** A graph of records is not a rate over a
     watched window, and one claiming a coverage percentage would be claiming
     something nobody measured (§7). `coverageOf` already says "absent" for a
     payload with no coverage field; the envelope must not invent one.

     Nothing is computed here. The threshold, the component count and the
     biggest cluster are the envelope's own numbers, printed. Centrality is not
     drawn because it is not offered: a centrality score over an edge set that
     is *whatever happens to be recorded* is the most convincing wrong number
     this product could show. */

  const cssName = (s) => String(s).replace(/[^A-Za-z0-9_-]/g, "");
  const GRAPH_HOLES = new Set(["unattributed", "unlabelled"]);
  const isHole = (n) => n.unknown === true || GRAPH_HOLES.has(String(n.kind));

  /* A Fruchterman–Reingold layout, hand-written, because the node count is
     bounded by "showing 60 of 340" anyway and 17 KB of vendored force maths
     would have to earn itself against that.

     DETERMINISTIC from the first frame: the starting positions are a
     golden-angle spiral seeded by the node's index, never `Math.random`, and
     the iteration count is fixed. A layout that moved between two runs would
     make a screenshot baseline, an export and a browser test three different
     pictures of the same graph, and the first one to disagree would be blamed
     on the plant. */
  function forceLayout(nodes, edges, box, options = {}) {
    const n = nodes.length;
    const at = new Map();
    const golden = Math.PI * (3 - Math.sqrt(5));
    const radius = Math.max(Math.min(box.width, box.height) / 2 - 8, 1);
    nodes.forEach((node, i) => {
      const r = radius * Math.sqrt((i + 0.5) / Math.max(n, 1));
      at.set(node.id, {
        x: box.x + box.width / 2 + r * Math.cos(i * golden),
        y: box.y + box.height / 2 + r * Math.sin(i * golden),
        dx: 0, dy: 0,
      });
    });
    if (n < 2) return at;
    const k = Math.sqrt((box.width * box.height) / n) * 0.62;
    /* Fewer passes on a big graph: the picture is bounded, the patience of a
       plant PC is not. */
    const steps = options.steps || (n > 140 ? 90 : 220);
    const links = edges
      .map((e) => ({ a: at.get(e.from), b: at.get(e.to) }))
      .filter((l) => l.a && l.b && l.a !== l.b);
    let temp = Math.min(box.width, box.height) / 6;
    const cool = temp / (steps + 1);
    const points = [...at.values()];
    for (let step = 0; step < steps; step++) {
      for (const p of points) { p.dx = 0; p.dy = 0; }
      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          const a = points[i], b = points[j];
          let ex = a.x - b.x, ey = a.y - b.y;
          /* Two nodes exactly on top of each other have no direction to push
             apart in, so they are given one — from their index, so it is the
             same one every run. */
          let d = Math.hypot(ex, ey);
          if (d < 0.01) { ex = ((i % 7) - 3) / 10 || 0.1; ey = ((j % 5) - 2) / 10 || 0.1; d = Math.hypot(ex, ey); }
          /* Repulsion has a reach. Without one, two components that cannot
             pull on each other push each other to opposite ends of the frame
             and the fit pass then flattens both — which is what looking at
             this on 2026-09-29 showed: four nodes in a row along the top edge
             with their names on top of each other. Beyond about two node
             spacings, nodes no longer shove. */
          if (d > k * 2.4) continue;
          const push = (k * k) / d;
          a.dx += (ex / d) * push; a.dy += (ey / d) * push;
          b.dx -= (ex / d) * push; b.dy -= (ey / d) * push;
        }
      }
      for (const link of links) {
        const ex = link.a.x - link.b.x, ey = link.a.y - link.b.y;
        const d = Math.max(Math.hypot(ex, ey), 0.01);
        const pull = (d * d) / k;
        link.a.dx -= (ex / d) * pull; link.a.dy -= (ey / d) * pull;
        link.b.dx += (ex / d) * pull; link.b.dy += (ey / d) * pull;
      }
      const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
      for (const p of points) {
        /* A whiff of gravity, or a component with no edges drifts off the
           picture and the reader never learns it was there. */
        p.dx += (cx - p.x) * 0.03;
        p.dy += (cy - p.y) * 0.03;
        const d = Math.max(Math.hypot(p.dx, p.dy), 1e-6);
        p.x += (p.dx / d) * Math.min(d, temp);
        p.y += (p.dy / d) * Math.min(d, temp);
        p.x = Math.max(box.x, Math.min(box.x + box.width, p.x));
        p.y = Math.max(box.y, Math.min(box.y + box.height, p.y));
      }
      temp -= cool;
    }
    /* And then FIT it. Looking at the graph on 2026-09-29 found two
       disconnected components flung to opposite corners and clamped there,
       with their labels off both edges of the picture — which is what a
       repulsion term does to a graph whose components cannot pull on each
       other. Scaling the finished layout into the box is deterministic, keeps
       every relative position the forces settled on, and cannot put a node
       somewhere a reader cannot see it. */
    const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
    const lo = { x: Math.min(...xs), y: Math.min(...ys) };
    const hi = { x: Math.max(...xs), y: Math.max(...ys) };
    const scale = Math.min(
      (hi.x - lo.x) > 1 ? box.width / (hi.x - lo.x) : 1,
      (hi.y - lo.y) > 1 ? box.height / (hi.y - lo.y) : 1);
    const mid = { x: (lo.x + hi.x) / 2, y: (lo.y + hi.y) / 2 };
    const centre = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    for (const p of points) {
      p.x = centre.x + (p.x - mid.x) * scale;
      p.y = centre.y + (p.y - mid.y) * scale;
    }
    return at;
  }

  function graphShape(envelope, options, width) {
    const carriedNodes = options.nodes || envelope.nodes || [];
    const carriedEdges = options.edges || envelope.edges || [];
    if (!Array.isArray(carriedNodes) || !Array.isArray(carriedEdges)) {
      throw new TypeError(
        "kit.chart('graph'): needs envelope.nodes and envelope.edges — this shape "
        + "draws the edges somebody recorded and has no way to infer one");
    }
    const view = viewOf(options);
    const hidden = hiddenIn(options);
    const threshold = has(view.threshold) ? view.threshold : null;

    /* The kinds this model declares, INCLUDING the ones this plant has no node
       of. The envelope says which; a caller that names none gets the kinds its
       own nodes have, and no empties, because the kit cannot know what a plant
       failed to record.

       Two spellings, read as one. `services/trace_analysis.py` — the plant's
       own trace-graph read — states the kinds as a count by name and the
       reasons for the empty ones separately; the design page writes them as a
       list of objects. The kit reads either, because its job is to draw
       what the API measured, not to make the API spell it a particular way. */
    const listed = Array.isArray(envelope.node_kinds) ? envelope.node_kinds
      : Array.isArray(envelope.kinds) ? envelope.kinds : null;
    const counted = !listed && envelope.node_kinds && typeof envelope.node_kinds === "object"
      ? envelope.node_kinds : null;
    const whyEmpty = envelope.empty_node_kinds || {};
    const declared = listed
      || (counted
        ? Object.keys(counted).map((kind) => ({
            kind, nodes: counted[kind], note: whyEmpty[kind] || null,
            /* NOT inferred from the note. The route gives every empty kind a
               sentence, and most of them say "nothing in this window is one of
               these" — a measured zero. "Nobody could have recorded this" is a
               different fact, and this spelling of the envelope has no field
               that claims it, so the kit does not claim it either: the kind is
               drawn empty and its own sentence says why. */
            unknown: false,
          }))
        : [...new Set(carriedNodes.map((n) => String(n.kind)))].map((kind) => ({ kind })));
    const nodeKinds = declared.map((k) => ({
      kind: String(k.kind),
      nodes: has(k.nodes) ? k.nodes
        : carriedNodes.filter((n) => String(n.kind) === String(k.kind)).length,
      note: k.note || null,
      unknown: k.unknown === true,
    }));
    const empties = nodeKinds.filter((k) => k.nodes === 0);
    const edgeKinds = [...new Set(carriedEdges.map((e) => String(e.kind)))];

    /* What the reader switched off, and what the threshold took out. Both are
       the reader's, both change the total, and the footer says both. */
    const keptEdges = carriedEdges.filter((e) =>
      !hidden.has(`edge:${String(e.kind)}`)
      && !(has(threshold) && isNum(e.weight) && e.weight < threshold));
    const keptNodeKind = (n) => !hidden.has(`node:${String(n.kind)}`);
    /* An edge whose end left the picture is not drawn — an edge to nowhere is
       a line the reader would read as reaching something. */
    const drawnNodes = carriedNodes.filter(keptNodeKind);
    const ids = new Set(drawnNodes.map((n) => n.id));
    const edges = keptEdges.filter((e) => ids.has(e.from) && ids.has(e.to));
    const nodes = drawnNodes;

    const carriedTotals = envelope.total || {};
    const nodesTotal = has(carriedTotals.nodes) ? carriedTotals.nodes
      : has(envelope.nodes_total) ? envelope.nodes_total : carriedNodes.length;
    const edgesTotal = has(carriedTotals.edges) ? carriedTotals.edges
      : has(envelope.edges_total) ? envelope.edges_total : carriedEdges.length;
    const notDrawn = Math.max(edgesTotal - edges.length, 0);

    const height = options.height || 320;
    /* The margins are for the NAMES, not the nodes: a label is centred over
       its node and is far wider than it, so a layout that filled the frame
       edge to edge would put half of "what is my next order" outside the
       picture. Looking at it on 2026-09-29 did exactly that. */
    const side = 58, top = 22, foot = 40;
    const box = { x: side, y: top, width: Math.max(width - side * 2, 80),
                  height: Math.max(height - top - foot, 60) };
    const at = forceLayout(nodes, edges, box, options);

    const weights = nodes.map((n) => (isNum(n.weight) ? n.weight : 0));
    const heaviest = Math.max(...weights, 0) || 1;
    const rOf = (n) => 4 + 9 * Math.sqrt(Math.max(isNum(n.weight) ? n.weight : 0, 0) / heaviest);
    const edgeWeights = carriedEdges.map((e) => (isNum(e.weight) ? e.weight : 0));
    const widest = Math.max(...edgeWeights, 0) || 1;
    const strokeOf = (e) => 0.8 + 2.6 * Math.sqrt(Math.max(isNum(e.weight) ? e.weight : 0, 0) / widest);

    /* Which nodes get a name beside them. Every label on sixty nodes is a grey
       smear; a choice about what the reader can read is a choice the chart
       states (rule 4). The holes and the empties are always named — they are
       the finding. */
    const labelCount = has(options.labels) ? options.labels : 14;
    const named = new Set(
      nodes.slice()
        .sort((a, b) => (isNum(b.weight) ? b.weight : 0) - (isNum(a.weight) ? a.weight : 0))
        .slice(0, labelCount).map((n) => n.id));
    for (const n of nodes) if (isHole(n)) named.add(n.id);

    const holes = nodes.filter(isHole);
    /* The harness's §6 rule 4, on an edge: seconds without how much of the
       window anybody watched are seconds nobody can check, so the number is
       not printed and the edge says why. */
    const unwatched = edges.filter(
      (e) => e.unit === "seconds" && !has(e.watched_seconds));
    const timedEdges = edges.filter((e) => has(e.watched_seconds));
    const measures = envelope.measures || {};
    /* The same two spellings again, for the one measure that is a sentence. */
    const biggest = measures.biggest
      || (has(measures.biggest_component_weight)
        ? { nodes: measures.biggest_component_nodes,
            weight: measures.biggest_component_weight,
            touches: measures.biggest_component_touches,
            absent: measures.biggest_component_does_not_touch }
        : null);
    /* And the refusal, which the design page calls the fourth measure. Printed
       in the envelope's own words, every one of them: a refusal a reader has
       to go and look up is a refusal that reads as an omission. */
    const refused = typeof measures.refused === "string" ? [measures.refused]
      : (measures.refused && typeof measures.refused === "object"
        ? Object.keys(measures.refused).map((m) => `${m} ${measures.refused[m]}`)
        : []);

    const byKind = (kind) => carriedNodes.filter((n) => String(n.kind) === kind).length;

    return {
      height,
      title: options.title || "The network of what this plant recorded",
      /* Rule 5, in §7's own words: "showing 60 of 340 nodes; 12 edges not
         drawn". Recomputed on every redraw, so hiding a kind or moving the
         threshold cannot leave yesterday's number on the picture. */
      total: [`showing ${count(nodes.length)} of ${things(nodesTotal, "node")}`,
              `${count(edges.length)} of ${things(edgesTotal, "edge")} drawn`,
              notDrawn ? `${count(notDrawn)} edges not drawn` : null,
              empties.length
                ? `${things(empties.length, "node kind")} declared and empty here`
                : null,
             ].filter(Boolean).join(" · "),
      /* Rule 4: every choice the shape depends on, and every measure the
         ENVELOPE computed — never one this chart worked out. */
      axes: [
        /* Two thresholds, and they are two different facts: the one the API
           applied before it handed the graph over, and the one the reader has
           moved to since. A single sentence would hide whichever was not
           being shown. */
        has(measures.threshold)
          ? `the API drew no edge lighter than ${measures.threshold}`
          : null,
        has(threshold) && threshold !== measures.threshold
          ? `and the reader has raised that to ${threshold}`
          : null,
        has(measures.components)
          ? `${things(measures.components, "connected component")} in the graph as measured`
          : null,
        biggest && has(biggest.weight)
          ? (has(biggest.of_weight)
              ? `the biggest cluster holds ${count(biggest.weight)} of `
                + `${count(biggest.of_weight)} ${biggest.unit || "turns"}`
              : `the biggest cluster is ${things(biggest.nodes, "node")} `
                + `weighing ${count(biggest.weight)}`)
            + ((biggest.touches || []).length
               ? `, touching ${biggest.touches.join(", ")}` : "")
          : null,
        biggest && (biggest.absent || []).length
          ? `it touches no ${biggest.absent.join(", no ")} — the absence is `
            + `the finding, not a silence`
          : null,
        measures.absence_note || null,
        nodes.length > labelCount
          ? `the ${count(labelCount)} heaviest nodes are named, and every hole is`
          : null,
        /* The harness's §6 rule 4, said the way round that is a reassurance
           rather than a warning: these edges carry their denominator with
           them. The sentence under `unknown` is the other way round, for the
           ones that do not. */
        timedEdges.length
          ? `${things(timedEdges.length, "edge")} weighted in seconds carry the `
            + `machine's own watched window`
          : null,
        `node size is a share of the heaviest, ${count(heaviest)}; `
          + `edge width a share of the widest, ${count(widest)}`,
        ...refused,
      ].filter(Boolean),
      unknown: [
        holes.length
          ? `${things(holes.length, "node")} hatched — `
            + `${holes.map((h) => h.label || h.id).join(", ")}: each carries its degree `
            + `and not a weight, because what a hole is, is what it touches`
          : null,
        empties.length
          ? `${things(empties.length, "kind")} drawn empty: `
            + `${empties.map((k) => k.kind).join(", ")} — declared by the model and `
            + `recorded by nothing on this plant`
          : null,
        unwatched.length
          ? `${things(unwatched.length, "edge")} measured in seconds with no record of `
            + `how much of the window was watched, so the seconds are not printed`
          : null,
      ].filter(Boolean).join("; ") || null,
      notes: [],
      filters: [
        hidden.size
          ? `${things(hidden.size, "kind")} switched off — every figure above is of `
            + `what is drawn`
          : null,
        has(threshold)
          ? `threshold ${threshold}: ${count(carriedEdges.length - keptEdges.length)} `
            + `edges below it are not drawn`
          : null,
      ].filter(Boolean),
      legend: edgeKinds.map((kind) => ({
        key: `edge:${kind}`, label: kind.replace(/_/g, " "),
        note: things(carriedEdges.filter((e) => String(e.kind) === kind).length, "edge"),
        cls: `edge-swatch edge-${cssName(kind)}`,
      })).concat(nodeKinds.map((k) => ({
        key: `node:${k.kind}`, label: k.kind.replace(/_/g, " "),
        note: things(byKind(k.kind), "node"),
        cls: k.nodes === 0 ? "graph-node-empty"
          : GRAPH_HOLES.has(k.kind) ? "chart-unknown-swatch"
          : `graph-node-kind node-${cssName(k.kind)}`,
      }))),
      slider: widest > 1
        ? { label: "edges at least", domain: [0, widest],
            step: Math.max(Math.round(widest / 20), 1), say: (v) => count(v) }
        : null,
      paint(g, ctx) {
        /* Edges first, so a node sits on top of what reaches it. */
        for (const edge of edges) {
          const a = at.get(edge.from), b = at.get(edge.to);
          if (!a || !b) continue;
          const kind = String(edge.kind);
          /* `watched_seconds` is only ever set on an edge weighted in seconds —
             a watched window beside a count of questions would mean nothing —
             so its presence says which this is, and `unit` says so explicitly
             when a caller has it to give. */
          const seconds = edge.unit === "seconds" || has(edge.watched_seconds);
          const blind = edge.unit === "seconds" && !has(edge.watched_seconds);
          const line = add(g, "line", {
            x1: a.x, y1: a.y, x2: b.x, y2: b.y,
            class: `graph-edge edge-${cssName(kind)}${blind ? " graph-edge-unknown" : ""}`,
            "stroke-width": strokeOf(edge),
            /* Rule 1: the weight the envelope carried, on the edge that drew it. */
            "data-value": raw(edge.weight),
            "data-edge-kind": kind,
            "data-from": raw(edge.from), "data-to": raw(edge.to),
            ...(blind ? { "data-unknown": "true" } : {}),
            ...(has(edge.watched_seconds) ? { "data-watched": raw(edge.watched_seconds) } : {}),
          });
          add(line, "title", {},
              `${edge.from} → ${edge.to} (${kind.replace(/_/g, " ")})\n`
              + (blind
                 ? `${duration(edge.weight)}, but how much of the window was watched is `
                   + `not recorded — so this number cannot be checked`
                 : seconds
                   ? `${duration(edge.weight)} of ${duration(edge.watched_seconds)} watched`
                   : `${raw(edge.weight)}`));
        }
        for (const node of nodes) {
          const p = at.get(node.id);
          if (!p) continue;
          const hole = isHole(node);
          const kind = String(node.kind);
          const mark = add(g, "circle", {
            cx: p.x, cy: p.y, r: rOf(node),
            class: `graph-node ${hole ? "chart-unknown"
                                      : `graph-node-kind node-${cssName(kind)}`}`,
            ...(hole ? { fill: ctx.hatch, "data-unknown": "true" } : {}),
            /* A hole carries its DEGREE (§7): what a hole is, is what it
               touches. Everything else carries the weight it was measured at. */
            "data-value": raw(hole ? node.degree : node.weight),
            /* Both numbers, always. `data-value` is the one the picture is
               drawn from, and for a hole §7 says that is the degree — but a
               hole of degree 0 would then be a node carrying nothing at all,
               and "four people outside any work centre" is exactly the finding
               it exists to show. Neither number is ever dropped. */
            "data-degree": raw(node.degree),
            "data-weight": raw(node.weight),
            "data-node": raw(node.id), "data-node-kind": kind,
            "data-label": node.label || node.id,
            tabindex: "0", role: "button",
            "aria-label": `${node.label || node.id}, ${kind.replace(/_/g, " ")}`,
          });
          add(mark, "title", {},
              `${node.label || node.id} (${kind.replace(/_/g, " ")})\n`
              + (hole
                 ? `a hole: ${raw(node.weight)} of them, and `
                   + `${things(node.degree, "edge")} reach it — nothing names what is `
                   + `behind them`
                 : `${raw(node.weight)}${node.unit ? ` ${node.unit}` : ""}`
                   + `, ${things(node.degree, "edge")}`));
          /* The page (D5) decides what "into its records" means; the kit says
             which node was asked for and draws nothing new itself. */
          mark.addEventListener("click", (event) => {
            event.stopPropagation();
            mark.dispatchEvent(new CustomEvent("fs-chart-expand", {
              bubbles: true,
              detail: { id: node.id, kind, label: node.label || node.id,
                        value: hole ? node.degree : node.weight, degree: node.degree },
            }));
          });
          if (named.has(node.id)) {
            const text = String(node.label || node.id);
            add(g, "text", {
              x: p.x, y: p.y - rOf(node) - 4, class: "graph-label", "text-anchor": "middle",
            }, text.length > 22 ? `${text.slice(0, 21)}…` : text);
          }
        }
        /* And the kinds this plant recorded nothing of, drawn rather than
           omitted — the row is the point of the row. */
        if (empties.length) {
          /* Wrapped, because the model declares eleven kinds and a chart can be
             360 wide — a row that ran off the edge would hide exactly the kinds
             this row exists to show. */
          let y = box.y + box.height + 16;
          add(g, "text", { x: 4, y: y + 4, class: "axis" }, "declared, and empty here:");
          let x = 140;
          for (const kind of empties) {
            const text = kind.kind.replace(/_/g, " ");
            const w = 18 + text.length * 5.6 + 12;
            if (x + w > ctx.width - 4 && x > 140) { x = 140; y += 14; }
            const mark = add(g, "circle", {
              cx: x, cy: y, r: 5, class: "graph-node graph-node-empty",
              "data-value": raw(kind.nodes), "data-empty": "true",
              "data-node-kind": kind.kind, "data-label": kind.kind,
              ...(kind.unknown ? { "data-unknown": "true" } : {}),
            });
            add(mark, "title", {},
                `${kind.kind}: no node — ${kind.note || "nothing on this plant records one"}`);
            add(g, "text", { x: x + 9, y: y + 4, class: "graph-label" }, text);
            x += w;
          }
        }
      },
    };
  }

  /* ---------- a control chart: X-bar and R, or individuals and moving range --

     Fed `/quality/spc/{material}/{characteristic}` whole — the payload the
     Quality SPC screen draws and the one the `spc_chart` tool hands the
     analysis agent, unaltered. `kind` says which chart it is and nothing here
     infers it: `imr`, one piece at a time with the gap between consecutive
     readings below it, or `xbar_r`, a sample of n pieces whose points are the
     sample means with the spread inside each sample below (decision 0040). A
     third word is a newer plant than this shape, and is refused by name rather
     than drawn as one of the two.

     It is a SHAPE and not a second chart engine, for the reason the state
     timeline became one on 2026-09-28. Until 2026-10-07 this picture lived in
     `spc.js` alone, so an exploration that asked to draw an `spc_chart` answer
     got `line`: thirty sample means on a zero-based axis, no control limits, no
     spec band, no flagged dots, under a title promising all three. Scott was
     shown exactly that and could not have put it on a slide. One
     implementation is the only thing that stops the screen and the exploration
     coming to draw the same control chart differently.

     Nothing is computed here. The centre, both sets of limits, which rules
     fired, the capability and the verdict are `services.spc`'s; the arithmetic
     below turns those numbers into pixels. A chart that worked out its own
     control limits would be a second opinion about a process that has one
     (house rule 6, contract rule 1).

     `options.openable` makes each dot a button that dispatches `fs-spc-open`
     and lets the PAGE decide what opening a point means — the division the
     graph's `fs-chart-expand` already keeps. The SPC screen opens the dossier
     panel beside the chart; the AI tab writes that point into the question
     box, because the dossier there is a question to ask rather than a panel to
     open. Off by default all the same: a dot that looks pressable and is not
     is worse than one that plainly is not, and most charts are read and not
     pressed. `options.selected` rings whichever point is the one being asked
     about, so a redraw keeps the selection instead of losing it. */

  const SPC_INDIVIDUALS = "imr";
  const SPC_SAMPLED = "xbar_r";
  /* Rule 5 of the Western Electric set is the RANGE rule, and the two kinds
     carry it in two places. On an individuals chart it fires on the gap
     between two readings — its own series with its own index space — and
     arrives in `moving_range.signals`. On a sampled chart it fires on the
     spread inside ONE sample, so both halves are indexed by the same samples
     and it arrives in `signals` beside rules 1 to 4. Hence the upper half
     filters it out and the lower half keeps only it: a red ring round a mean
     because the spread beside it was wide would be the chart answering a
     question nobody asked of it. */
  const SPC_RANGE_RULE = 5;
  /* The lower half answers a smaller question, so it gets about half the
     height, and `options.height` sizes the upper half as on every other
     shape. Nothing above it moves when it appears. */
  const SPC_LOWER_HEIGHT = 128;
  const SPC_LOWER_GAP = 18;

  function spcShape(envelope, options, width) {
    if (!("points" in envelope) || !("control" in envelope)) {
      throw new TypeError(
        "kit.chart('spc'): needs a control-chart payload — `points`, `control`, "
        + "`signals` and `kind`, as /quality/spc/{material}/{characteristic} "
        + "returns them. This shape draws limits and never works them out, so a "
        + "payload without them is one it would have to invent a process for");
    }
    const kind = envelope.kind || SPC_INDIVIDUALS;
    if (kind !== SPC_INDIVIDUALS && kind !== SPC_SAMPLED) {
      throw new TypeError(
        `kit.chart('spc'): this characteristic is charted as “${kind}”, which this `
        + `shape does not draw: it draws ${SPC_INDIVIDUALS} (individuals and moving `
        + `range) and ${SPC_SAMPLED} (X-bar and R). It draws nothing rather than `
        + "plot these points as one of the two, which is the mistake decision 0040 "
        + "exists to stop");
    }
    const sampled = kind === SPC_SAMPLED;
    const points = envelope.points || [];
    const control = envelope.control || null;
    const signals = envelope.signals || [];
    /* One key or the other is present and never both (`services.spc.chart`),
       and the two blocks have the same shape: points, a centre, an upper
       limit, a lower one and a sentence. Two readings make the first moving
       range and n readings recorded together make the first sample range, so a
       chart with neither has an upper half and nothing below it. */
    const lower = (sampled ? envelope.range_chart : envelope.moving_range) || null;
    const lowerPoints = (lower && lower.points) || [];
    const noun = sampled ? "sample" : "reading";
    const size = envelope.sample_size;
    const unit = envelope.unit ? ` ${envelope.unit}` : "";
    const named = `${envelope.material || ""} ${envelope.characteristic || ""}`.trim();
    const label = named + (envelope.unit ? ` (${envelope.unit})` : "");

    const left = 52, right = 14, top = 12, bottom = 26;
    const upperH = options.height || 260;
    const plot = width - left - right;
    const plotH = upperH - top - bottom;
    const lowerTop = upperH + SPC_LOWER_GAP;
    const height = lowerPoints.length ? lowerTop + SPC_LOWER_HEIGHT : upperH;

    /* Rule 4. The axis fits the readings AND the lines a reader judges them
       against: a control chart scaled to its dots alone puts the limit that
       matters off the top of the picture, which is the one place it is needed.
       `yScale` says on the chart that the axis does not start at nought. */
    const guides = [envelope.lower_spec, envelope.upper_spec,
                    control && control.lower, control && control.upper].filter(isNum);
    const scale = yScale(points.map((p) => p.value).concat(guides), { zero: false });
    const x = (i) => left + (i / Math.max(points.length - 1, 1)) * plot;
    const y = (v) => top + plotH - ((v - scale.lo) / (scale.hi - scale.lo)) * plotH;

    const upper = signals.filter((s) => s.rule !== SPC_RANGE_RULE);
    const flagged = new Set(upper.flatMap(
      (s) => s.points || s.indexes || (s.index !== undefined ? [s.index] : [])));
    /* Flagged below BY ID and never by counting along. A moving-range series
       is one shorter than the readings it is the gaps between, and a sampled
       chart's two halves are one series of samples seen twice — an off-by-one
       either way rings the wrong dot. */
    const lowerFired = new Set(sampled
      ? signals.filter((s) => s.rule === SPC_RANGE_RULE)
          .map((s) => (points[s.index] || {}).sample).filter((id) => id !== undefined)
      : ((lower && lower.signals) || []).map((s) => s.check)
          .filter((id) => id !== undefined));
    const lowerFirings = sampled
      ? signals.filter((s) => s.rule === SPC_RANGE_RULE).length
      : ((lower && lower.signals) || []).length;

    const open = options.selected || {};
    const openable = options.openable === true;
    const aside = envelope.set_aside || 0;

    /* A dot the reader can open. There is no <button> inside an SVG, so it
       carries what one would carry and answers a keyboard — the same way the
       frame's own legend and threshold controls do (STYLE.md rule 6's intent).
       What opening it MEANS is the page's business, so this dispatches and
       stops. */
    function openWith(dot, what, id, series, say, at) {
      if (!openable || id === undefined || id === null) return;
      dot.setAttribute("role", "button");
      dot.setAttribute("tabindex", "0");
      dot.setAttribute("aria-label", say);
      const fire = (event) => {
        event.stopPropagation();
        dot.dispatchEvent(new CustomEvent("fs-spc-open", {
          /* `at` is the point's own timestamp, carried because a page that
             wanted to say WHEN the dot it was handed was would otherwise have
             to find it again by index in a payload whose two halves are
             indexed differently - which is the off-by-one this shape already
             goes out of its way to avoid. The id is still what identifies the
             point; the stamp is for the words around it. */
          bubbles: true, detail: { what, id, series, label: say, at },
        }));
      };
      dot.addEventListener("click", fire);
      dot.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        fire(event);
      });
    }

    /* A line a reader judges the dots against, with its own number on it. It
       carries no `data-value`: a specification limit is not a reading, and
       making one a hover target would put a tolerance where the frame looks
       for the measurement under the pointer. */
    function guideAt(g, at, v, cls, text) {
      if (!isNum(v)) return;
      add(g, "line", { x1: left, y1: at(v), x2: left + plot, y2: at(v), class: cls });
      add(g, "text", { x: left + plot - 4, y: at(v) - 3, class: "axis",
                       "text-anchor": "end" }, `${text} ${v}`);
    }

    /* The lower half of either kind, in the SAME node and against the same x
       scale. One <svg> is what makes `FS.kit.export` carry both halves into
       one file rather than half a chart, and a pair that did not line up would
       be two charts rather than one. Nought is always on its axis: a range is
       bounded below by zero, which is why its lower limit is a number and not
       a line somebody could cross, and a scale that cropped the bottom off
       would make a settled process look as if it were wandering. */
    function paintLowerHalf(g) {
      const lowTop = 14, lowBottom = 24;
      const lowH = SPC_LOWER_HEIGHT - lowTop - lowBottom;
      const ranges = lowerPoints.map((p) => p.range).filter(isNum);
      let ceiling = Math.max(...ranges, lower.upper || 0, lower.centre || 0);
      if (!(ceiling > 0)) ceiling = 1;
      ceiling *= 1.1;
      const ry = (v) => lowerTop + lowTop + lowH - (v / ceiling) * lowH;
      /* A group of its own. The two halves share one node so they line up and
         one export carries both; the group is how a reader of the markup — or
         a test — can still say *the lower chart* without measuring pixels. */
      const half = add(g, "g",
                       { "data-half": sampled ? "sample-range" : "moving-range" });
      for (let i = 0; i <= 2; i++) {
        const v = (ceiling * i) / 2;
        add(half, "line", { x1: left, y1: ry(v), x2: left + plot, y2: ry(v),
                            class: "grid-line" });
        add(half, "text", { x: left - 6, y: ry(v) + 3, class: "axis",
                            "text-anchor": "end" },
            v.toFixed(Math.abs(ceiling) < 10 ? 2 : 0));
      }
      guideAt(half, ry, lower.upper, "limit-line", sampled ? "D4·R̄" : "UCL");
      /* Drawn when it is nought, not hidden. Below n = 7 the lower range limit
         IS zero (D3 is zero there), and a reader who cannot see the line
         cannot tell "there is no lower limit on this chart" from "this chart
         forgot to draw one". */
      if (sampled) guideAt(half, ry, lower.lower, "limit-line", "D3·R̄");
      guideAt(half, ry, lower.centre, "centre-line", "R̄");
      /* A moving range belongs over the LATER of the two readings it is the
         gap between, so the individuals half's series is offset by one. A
         sample range belongs under the mean of the same sample. */
      const at = (i) => (sampled ? x(i) : x(i + 1));
      add(half, "polyline", {
        points: lowerPoints.map((p, i) => `${at(i)},${ry(p.range)}`).join(" "),
        class: "trend-line" });
      lowerPoints.forEach((point, i) => {
        const id = sampled ? point.sample : point.check;
        const hit = lowerFired.has(id);
        const chosen = id !== undefined && id === (sampled ? open.sample : open.check);
        const dot = add(half, "circle", {
          cx: at(i), cy: ry(point.range), r: chosen ? 5.5 : hit ? 4.5 : 2.5,
          /* The selected ring is a class of its own on this half: one point is
             one point, and a chart that marked it twice under one name would
             make "the point that is open" ambiguous to anybody — a reader or a
             test — counting marks. */
          class: (hit ? "spc-flag" : "spc-dot") + (chosen ? " spc-mr-selected" : ""),
          "data-value": raw(point.range),
          "data-label": (sampled ? "range within the sample" : "moving range")
            + (envelope.unit ? ` (${envelope.unit})` : ""),
          "data-series": sampled ? "sample-range" : "moving-range",
          ...(hit ? { "data-flagged": "true" } : {}),
          ...(id === undefined ? {} : { [sampled ? "data-sample" : "data-check"]: id }),
        });
        add(dot, "title", {}, sampled
          ? `Range ${point.range}${unit} · ${FS.fmt.stamp(point.ts)} — how far apart `
            + `the ${point.n || size} readings of this sample were`
          : `Moving range ${point.range}${unit} · ${FS.fmt.stamp(point.ts)} — the gap `
            + "from the reading before it");
        openWith(dot, sampled ? "sample" : "check", id,
                 sampled ? "sample-range" : "moving-range",
                 sampled
                   ? `Sample ${i + 1}, range ${point.range}${unit} across its `
                     + `${point.n || size} readings — open the readings behind it`
                   : `Moving range ${i + 1}, ${point.range}${unit}, the gap between `
                     + `readings ${i + 1} and ${i + 2} — opens reading ${i + 2}, the `
                     + "later of the two",
                 point.ts);
      });
      add(half, "text", { x: left + 4, y: lowerTop + 10, class: "axis" },
          (sampled
            ? `Range within each sample${envelope.unit ? ` (${envelope.unit})` : ""}`
            : `Moving range${envelope.unit ? ` (${envelope.unit})` : ""}`)
          + (lower.centre === null ? " — no limits yet" : ""));
    }

    return {
      height,
      /* A caller may name the picture - the chat titles the chart it drew in
         the words the question used - but what each dot IS stays on the end of
         it either way. A reader who takes a sample mean for one bottle reads
         every rule on the chart as something it is not, and that is a mistake
         no title the model chose is allowed to make possible. */
      title: `${options.title || label || "control chart"} — ${sampled
        ? `X̄ and R, the mean of each sample of ${count(size)}`
        : "individuals and moving range"}`,
      /* The server's own conclusion, drawn with the picture so a chart that
         leaves this page still carries what the plant made of it. Rule 1: it
         is `verdict`, the sentence `services.spc` wrote, and never one
         assembled here out of the numbers. Below the fewest points limits need
         there is no verdict and `note` says why instead. */
      verdict: envelope.verdict || envelope.note || null,
      /* Rule 5, on both halves and on what neither half drew. */
      total: [
        sampled
          ? `${things(points.length, "sample")} of ${count(envelope.readings)} readings drawn`
          : `${things(points.length, "reading")} drawn`,
        `${count(flagged.size)} flagged by ${things(upper.length, "firing")} `
          + "of rules 1 to 4",
        lowerPoints.length
          ? `${things(lowerPoints.length, sampled ? "sample range" : "moving range")} `
            + `below, ${count(lowerFired.size)} flagged by `
            + `${things(lowerFirings, "firing")} of rule ${SPC_RANGE_RULE}`
          : `nothing drawn below: ${sampled
              ? `a sample range needs ${count(size)} readings recorded together`
              : "a moving range needs two readings"}`,
        aside
          ? `${count(aside)} stored sample(s) left out — they hold a different `
            + `number of readings than the ${count(size)} this specification now `
            + "asks for"
          : null,
      ].filter(Boolean).join(" · "),
      axes: [
        scale.note,
        label ? `y: ${label}` : null,
        /* Rule 4, and the choice this whole picture rests on: a control
           chart's x-axis is ORDER, not a clock. Two dots side by side may be
           four seconds or four hours apart, and a reader who took the spacing
           for time would read a drift off it that is not there. */
        points.length
          ? `x: ${noun} order, oldest first — ${FS.fmt.clock(points[0].ts)} to `
            + `${FS.fmt.clock(points[points.length - 1].ts)}. Not a clock axis: the `
            + "space between two dots is not the time between them"
          : null,
        sampled && isNum(size)
          ? `each point is the mean of ${count(size)} readings; the chart under it is `
            + `how far apart those ${count(size)} were, and it is read first — the `
            + "limits above are computed from the mean of it"
          : null,
        control
          ? `${sampled ? "X̿" : "x̄"} is the centre and UCL/LCL are this process's own `
            + "variation; LSL/USL are the specification, which control limits are "
            + "never drawn from"
          : null,
      ].filter(Boolean),
      /* Rule 2's withheld half: a figure this payload refuses, with the reason
         it gave. Never filled in, never averaged away. */
      notes: [
        control ? null : `no control limits: ${envelope.note || "this chart has none"}`,
        control && envelope.stable === false && envelope.capability
          ? "Cp and Cpk are withheld while the process is out of control — a "
            + "capability figure describes a process that no longer exists"
          : null,
        control && envelope.capability === null
          ? "no capability figure: this characteristic has no two-sided "
            + "specification to judge the spread against"
          : null,
        lowerPoints.length && lower.centre === null
          ? `${sampled ? "the sample range" : "the moving range"} below has no limits `
            + `yet: ${lower.verdict || lower.note || "not enough of it recorded"}`
          : null,
      ].filter(Boolean),
      paint(g) {
        /* Which of the two kinds this is, in the server's own word, on the
           shape's own group. `data-kind` on the <svg> is the kit's shape name
           — `spc` — now that one shape draws both; a screen or a test that
           needs to know whether it is looking at individual readings or at
           sample means reads the word the payload used. */
        g.setAttribute("data-spc-kind", kind);
        for (let i = 0; i <= 3; i++) {
          const v = scale.lo + ((scale.hi - scale.lo) * i) / 3;
          add(g, "line", { x1: left, y1: y(v), x2: left + plot, y2: y(v),
                           class: "grid-line" });
          add(g, "text", { x: left - 6, y: y(v) + 3, class: "axis",
                           "text-anchor": "end" },
              v.toFixed(Math.abs(scale.hi - scale.lo) < 10 ? 2 : 0));
        }
        guideAt(g, y, envelope.lower_spec, "spec-line", "LSL");
        guideAt(g, y, envelope.upper_spec, "spec-line", "USL");
        if (control) {
          guideAt(g, y, control.lower, "limit-line", "LCL");
          guideAt(g, y, control.upper, "limit-line", "UCL");
          /* X-double-bar on a sampled chart, and it is not a flourish: the
             centre there is the mean of the sample means, and calling it x̄
             under a chart whose points are already averages is the one label a
             process engineer would read as the wrong number. */
          guideAt(g, y, control.centre, "centre-line", sampled ? "X̿" : "x̄");
        }
        if (points.length) {
          add(g, "polyline", {
            points: points.map((p, i) => `${x(i)},${y(p.value)}`).join(" "),
            class: "trend-line" });
        }
        points.forEach((point, i) => {
          /* What this dot IS, by its own id: a sample on a sampled chart, a
             reading on an individuals one — and never the nth dot, which is a
             different point every time a check is recorded. */
          const id = sampled ? point.sample : point.check;
          const chosen = id !== undefined && id === (sampled ? open.sample : open.check);
          const hit = flagged.has(i);
          const dot = add(g, "circle", {
            cx: x(i), cy: y(point.value), r: chosen ? 5.5 : hit ? 4.5 : 2.5,
            class: (hit ? "spc-flag" : "spc-dot") + (chosen ? " spc-selected" : ""),
            /* Rule 1: the envelope's own number, on the mark that drew it. */
            "data-value": raw(point.value),
            "data-label": (label || noun) + (sampled ? ` — mean of ${count(size)}` : ""),
            "data-series": sampled ? "xbar" : "individuals",
            ...(hit ? { "data-flagged": "true" } : {}),
            ...(id === undefined ? {} : { [sampled ? "data-sample" : "data-check"]: id }),
          });
          add(dot, "title", {}, (sampled
            ? `Mean ${point.value}${unit} of ${size} · `
            : `${point.value}${unit} · `)
            + FS.fmt.stamp(point.ts)
            + (hit ? " — a rule fired here" : ""));
          openWith(dot, sampled ? "sample" : "check", id,
                   sampled ? "xbar" : "individuals",
                   sampled
                     ? `Sample ${i + 1}, mean ${point.value}${unit} of ${count(size)} `
                       + `readings at ${FS.fmt.stamp(point.ts)} — open the readings `
                       + "behind it"
                     : `Reading ${i + 1}, ${point.value}${unit} at `
                       + `${FS.fmt.stamp(point.ts)} — open the records behind it`,
                   point.ts);
        });
        /* What this picture is OF, on the picture. The frame's <title> is for a
           screen reader; a chart pasted into a slide has to name itself. */
        if (label) {
          add(g, "text", { x: left + 4, y: top + 10, class: "axis" },
              label + (sampled ? ` — mean of ${count(size)}` : ""));
        }
        /* The ends of the axis in the plant's own clock, so "oldest first" is
           a fact on the chart and not only in the footer. */
        if (points.length > 1) {
          add(g, "text", { x: left, y: top + plotH + 14, class: "axis" },
              FS.fmt.clock(points[0].ts));
          add(g, "text", { x: left + plot, y: top + plotH + 14, class: "axis",
                           "text-anchor": "end" },
              FS.fmt.clock(points[points.length - 1].ts));
        }
        if (lowerPoints.length) paintLowerHalf(g);
      },
    };
  }

  const SHAPES = { line: lineShape, bars: barsShape, states: statesShape,
                   histogram: histogramShape, graph: graphShape, spc: spcShape };

  /* ================================================================
     EXPORT — the same picture, off the page

         FS.kit.export(svgNode, "svg" | "png") -> Promise<Blob>

     §3 of docs/design/deep-analysis.md: the `fs-chart` node already carries
     everything an export needs — its own `<title>` and `<desc>`, the footer
     lines, `data-total`, `data-coverage`. So an export is that node's own
     markup with the palette's RESOLVED colours written onto it, and nothing
     else: no second renderer, no server round trip, and no chance of an export
     that draws a different picture from the screen.

     The rule this is built to keep, and the one worth checking in review: **a
     chart is presentation-ready when its footer survives being pasted into a
     slide.** An export whose coverage sentence was stripped is not an export
     this product makes — so the footer, the total and the coverage go into the
     file, and a test asserts they are still there.

     Inlining the colours is not "naming a colour" (rule 6). Nothing here
     chooses one: it reads back what the theme the reader is in already
     resolved, which is the only way the file looks in a slide the way it
     looked on the screen. */

  const EXPORT_STYLE = [
    "fill", "fill-opacity", "stroke", "stroke-width", "stroke-dasharray",
    "stroke-linecap", "stroke-linejoin", "stroke-opacity", "opacity",
    "font-family", "font-size", "font-weight", "font-style", "text-anchor",
    "dominant-baseline", "letter-spacing", "display",
  ];

  function exportable(node) {
    if (!node || node.tagName !== "svg" || !node.classList.contains("fs-chart")) {
      throw new TypeError(
        "kit.export: give it a chart — the <svg class=\"fs-chart\"> that "
        + "FS.kit.chart() returned, because the footer and the coverage this "
        + "export has to carry are on that node");
    }
    const clone = node.cloneNode(true);
    /* A tooltip is where somebody's mouse happened to be. Everything else the
       reader can see goes into the file, the legend and the threshold
       included: those are the choices the footer is stating. */
    for (const transient of clone.querySelectorAll('[data-chrome="hover"]')) {
      transient.remove();
    }
    for (const el of clone.querySelectorAll("[tabindex]")) el.removeAttribute("tabindex");
    const from = [node, ...node.querySelectorAll("*")];
    const to = [clone, ...clone.querySelectorAll("*")];
    /* The hover layer left the clone, so walk the SOURCE for its styles and
       skip what is no longer there — matched by position, which is document
       order in both and the reason the removal happens first is that it
       would otherwise shift the pairing. */
    const live = from.filter((el) => !el.closest('[data-chrome="hover"]'));
    for (let i = 0; i < to.length && i < live.length; i++) {
      const computed = getComputedStyle(live[i]);
      let css = "";
      for (const prop of EXPORT_STYLE) {
        const value = computed.getPropertyValue(prop);
        if (value) css += `${prop}:${value};`;
      }
      to[i].setAttribute("style", css);
    }
    /* The ground the chart is drawn on, carried with it. Looking at an
       exported file on 2026-09-29 with no stylesheet behind it found the page
       showing straight through — so a daylight chart pasted onto a dark slide
       took the slide's colour and its footer went with it. The PNG already
       filled its canvas; now the SVG says the same thing, and the two cannot
       come to disagree. */
    const ground = document.createElementNS(NS, "rect");
    ground.setAttribute("x", 0);
    ground.setAttribute("y", 0);
    ground.setAttribute("width", node.getAttribute("width") || 0);
    ground.setAttribute("height", node.getAttribute("height") || 0);
    ground.setAttribute("fill", panelColour());
    ground.setAttribute("data-chrome", "ground");
    clone.insertBefore(ground, clone.firstChild);
    clone.setAttribute("xmlns", NS);
    clone.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
    return clone;
  }

  /* The colour behind the picture. A chart is drawn on a panel, and an SVG
     with nothing behind it is a transparent PNG that reads as white wherever
     it is pasted — including onto a dark slide, where the footer disappears. */
  function panelColour() {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue("--panel").trim();
    return value || "#ffffff";
  }

  function exportChart(node, format = "svg") {
    const clone = exportable(node);
    const markup = `<?xml version="1.0" encoding="UTF-8"?>\n${clone.outerHTML}`;
    if (format === "svg") {
      return Promise.resolve(new Blob([markup], { type: "image/svg+xml;charset=utf-8" }));
    }
    if (format !== "png") {
      throw new TypeError(`kit.export: no such format: ${format} (svg, png)`);
    }
    const width = Number(node.getAttribute("width")) || 800;
    const height = Number(node.getAttribute("height")) || 400;
    const scale = 2;      // a slide is projected, and 1x is a soft chart
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = Math.round(width * scale);
          canvas.height = Math.round(height * scale);
          const ctx = canvas.getContext("2d");
          ctx.fillStyle = panelColour();
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
          canvas.toBlob((blob) => (blob
            ? resolve(blob)
            : reject(new Error("the browser produced no PNG from this chart"))), "image/png");
        } catch (err) { reject(err); }
      };
      image.onerror = () => reject(
        new Error("the browser could not read the chart back as an image"));
      image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(markup)}`;
    });
  }

  /* The one entry point. `pareto` is `bars` with the cumulative line on. */
  function chart(kind, envelope, options = {}) {
    const name = kind === "pareto" ? "bars" : kind;
    /* Own property only: SHAPES["constructor"] is a function, and a lookup that
       did not ask would happily call it as a chart shape. */
    const shape = Object.hasOwn(SHAPES, name) ? SHAPES[name] : null;
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
             /* The chart contract: one entry point, five shapes, six rules —
                and one way to take a chart off the page with its footer on. */
             chart, draw, export: exportChart };
})();
