/* The screen kit: the charts and controls every object page draws.

   analysis.js and quality.js each grew their own SVG helpers; a third copy
   for the machine page would have made the drift permanent. These are the
   one set. Hand-drawn SVG, no library - a plant PC renders this for years
   without a toolchain (principle 5). Nothing here computes a number: a chart
   draws what the API measured, and draws "unknown" when it did not. */

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
          new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
    }
  }

  /* ---------- a state timeline: one row per machine ----------
     data = /analysis/timeline: { window: {start, end}, machines: [{code, intervals: [{state, reason, start, end, seconds}]}] } */
  function timeline(host, data, options = {}) {
    host.replaceChildren();
    const machines = data.machines.filter((m) => m.intervals.length);
    if (!machines.length) return empty(host, options.emptyText || "No equipment states recorded in this window yet.");

    const left = 78, right = 12, rowH = options.rowHeight || 26, gap = 6, top = 6;
    const width = Math.max(host.clientWidth || 900, 620);
    const plot = width - left - right;
    const height = top + machines.length * (rowH + gap) + 22;
    const start = utc(data.window.start).getTime();
    const end = utc(data.window.end).getTime();
    const span = Math.max(end - start, 1);

    const chart = svg(width, height);
    timeTicks(chart, start, end, plot, top + machines.length * (rowH + gap), left);
    machines.forEach((machine, index) => {
      const y = top + index * (rowH + gap);
      add(chart, "text", { x: left - 8, y: y + rowH / 2 + 4, class: "row-label", "text-anchor": "end" }, machine.code);
      for (const interval of machine.intervals) {
        const x0 = left + ((utc(interval.start).getTime() - start) / span) * plot;
        const x1 = left + ((utc(interval.end).getTime() - start) / span) * plot;
        const rect = add(chart, "rect", {
          x: x0, y, width: Math.max(x1 - x0, 1), height: rowH, rx: 2,
          fill: `var(--${interval.state}, var(--unknown))`,
          opacity: interval.state === "running" ? 0.85 : 0.95,
        });
        add(rect, "title", {}, `${machine.code} ${interval.state}${interval.reason ? ` (${interval.reason})` : ""}
${duration(interval.seconds)} from ${utc(interval.start).toLocaleTimeString()}`);
      }
    });
    host.appendChild(chart);
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

  /* ---------- the OEE bars a machine card draws ---------- */
  function oeeBars(host, oee) {
    host.replaceChildren();
    [["Availability", oee.availability], ["Performance", oee.performance],
     ["Quality", oee.quality], ["OEE", oee.oee]].forEach(([label, value], index) => {
      const row = FS.el("div", `bar-row${index === 3 ? " total" : ""}`);
      const bar = FS.el("div", "bar");
      const fill = FS.el("i");
      fill.style.width = `${Math.min(100, (value || 0) * 100)}%`;
      if (value === null || value === undefined) fill.classList.add("unknown");
      bar.append(fill);
      row.append(FS.el("span", null, label), bar, FS.el("span", "num", FS.fmt.pct(value)));
      host.append(row);
    });
  }

  FS.kit = { utc, duration, age, svg, add, empty, timeTicks, timeline, trend, oeeBars };
})();
