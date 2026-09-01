// Inline SVG drawing. No chart library: none is reachable offline (ADR 0036),
// and these are small enough to draw directly.


(function () {
  const NS = "http://www.w3.org/2000/svg";

  // Escaping is done here rather than at each call site, because every string
  // below comes out of the store and some of them are ADR prose with angle
  // brackets in it.
  function esc(text) {
    return String(text ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function svg(width, height, body, extra = "") {
    return `<svg class="chart" width="100%" height="${height}" viewBox="0 0 ${width} ${height}"
      preserveAspectRatio="none" fill="none" aria-hidden="true" ${extra}>${body}</svg>`;
  }

  // A path through evenly spaced values, scaled to the box.
  function linePath(values, width, height, pad = 2) {
    if (values.length < 2) return "";
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const span = hi - lo || 1;
    const step = width / (values.length - 1);
    return values
      .map((v, i) => {
        const x = i * step;
        const y = height - pad - ((v - lo) / span) * (height - pad * 2);
        return `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
      })
      .join(" ");
  }

  // A sparkline with a soft fill under it, as the KPI cards in the asset have.
  function sparkline(values, colour, height = 34) {
    if (!values || values.length < 2) return "";
    const width = 240;
    const path = linePath(values, width, height);
    const fill = `${path} L${width} ${height} L0 ${height} Z`;
    const id = `g${Math.random().toString(36).slice(2, 9)}`;
    return svg(
      width,
      height,
      `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
         <stop offset="0" stop-color="${colour}" stop-opacity="0.28"/>
         <stop offset="1" stop-color="${colour}" stop-opacity="0"/>
       </linearGradient></defs>
       <path d="${fill}" fill="url(#${id})"/>
       <path d="${path}" stroke="${colour}" stroke-width="1.5" stroke-linejoin="round"/>`,
      'class="spark"',
    );
  }

  // Two overlaid normal-ish curves: the raw estimates and the corrected ones,
  // the shape the asset's Calibration Lens shows.
  function bellCurves(rawCentre, correctedCentre, low, high) {
    const width = 320;
    const height = 92;
    const lo = 0;
    const hi = Math.max(rawCentre, correctedCentre, high || 0) * 1.6 || 1;
    const x = (v) => ((v - lo) / (hi - lo)) * width;
    const spread = (high && low ? Math.abs(high - low) : hi * 0.25) / 2 || hi * 0.15;

    const curve = (centre, colour, opacity) => {
      const points = [];
      for (let i = 0; i <= 60; i += 1) {
        const v = lo + ((hi - lo) * i) / 60;
        const z = (v - centre) / (spread || 1);
        const y = height - 6 - Math.exp(-0.5 * z * z) * (height - 18);
        points.push(`${i ? "L" : "M"}${x(v).toFixed(1)} ${y.toFixed(1)}`);
      }
      const path = points.join(" ");
      return `<path d="${path} L${width} ${height - 6} L0 ${height - 6} Z"
                fill="${colour}" fill-opacity="${opacity}"/>
              <path d="${path}" stroke="${colour}" stroke-width="1.5"/>`;
    };

    const band =
      low && high
        ? `<rect x="${x(Math.min(low, high))}" y="4" width="${Math.abs(x(high) - x(low))}"
             height="${height - 10}" fill="var(--teal)" fill-opacity="0.07"/>`
        : "";

    return svg(
      width,
      height,
      `${band}
       ${curve(rawCentre, "var(--violet)", 0.14)}
       ${curve(correctedCentre, "var(--teal)", 0.14)}
       <line x1="${x(correctedCentre)}" y1="4" x2="${x(correctedCentre)}" y2="${height - 6}"
         stroke="var(--teal)" stroke-width="1" stroke-dasharray="3 3"/>
       <line x1="0" y1="${height - 6}" x2="${width}" y2="${height - 6}" stroke="var(--line)"/>`,
    );
  }

  // Horizontal bars, one per calibration group. Refusals draw no bar at all --
  // a zero-length bar would read as "no bias" rather than "not enough evidence".
  function factorBars(factors) {
    return factors
      .map((f) => {
        const label = `${esc(f.work_class)} · ${esc(f.owner)}`;
        if (!f.speaks) {
          return `<div class="bar-row">
              <div class="bar-label">${label}<span class="faint"> · n=${f.n}</span></div>
              <div class="bar-refused">refused — ${esc(f.reason)}</div>
            </div>`;
        }
        const magnitude = Number(f.magnitude);
        // 3x fills the track; anything larger is clamped and still reads as large.
        const width = Math.min(100, (magnitude / 3) * 100);
        const colour = f.direction === "over" ? "var(--violet)" : "var(--amber)";
        return `<div class="bar-row">
            <div class="bar-label">${label}<span class="faint"> · n=${f.n}</span></div>
            <div class="bar-track">
              <div class="bar-fill" style="width:${width}%;background:${colour}"></div>
            </div>
            <div class="bar-value" style="color:${colour}">${magnitude.toFixed(2)}× ${esc(
              f.direction,
            )}</div>
          </div>`;
      })
      .join("");
  }

  window.charts = { esc, svg, sparkline, bellCurves, factorBars, NS };
})();
