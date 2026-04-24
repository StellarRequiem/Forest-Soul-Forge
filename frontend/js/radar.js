// Hand-rolled SVG radar chart. Consumes the `per_domain` array from a
// PreviewResponse.grade and paints a polygon over a polar axis. No d3, no
// canvas — SVG composes better with the CSS theme.

const SVG_NS = "http://www.w3.org/2000/svg";
const VIEW = 320;
const CENTER = VIEW / 2;
const MAX_RADIUS = CENTER - 36; // leave room for axis labels
const RINGS = [25, 50, 75, 100];

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    el.setAttribute(k, v);
  }
  return el;
}

function polar(angle, radius) {
  // angle is in radians, 0 at top (−π/2 offset), clockwise.
  return [
    CENTER + radius * Math.cos(angle - Math.PI / 2),
    CENTER + radius * Math.sin(angle - Math.PI / 2),
  ];
}

function clearSvg(svg) {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
}

/**
 * Render the radar. `perDomain` is the `per_domain` array from
 * PreviewResponse.grade — each entry has { domain, weighted_score, ... }.
 * Called with an empty array to clear.
 */
export function drawRadar(perDomain) {
  const svg = document.getElementById("radar");
  if (!svg) return;
  clearSvg(svg);

  svg.setAttribute("viewBox", `0 0 ${VIEW} ${VIEW}`);

  // If no data, draw the empty grid so the panel doesn't look broken.
  const domains = Array.isArray(perDomain) ? perDomain : [];
  const n = domains.length;

  // Background rings (every 25).
  for (const ring of RINGS) {
    const r = (ring / 100) * MAX_RADIUS;
    if (n === 0) {
      // Just a circle in the empty state.
      svg.appendChild(
        svgEl("circle", {
          cx: CENTER,
          cy: CENTER,
          r,
          fill: "none",
          stroke: "var(--border)",
          "stroke-dasharray": "2 3",
          "stroke-width": 1,
        })
      );
    } else {
      // Polygon connecting the ring at each axis so the grid matches axis
      // count.
      const pts = [];
      for (let i = 0; i < n; i++) {
        const [x, y] = polar((i / n) * Math.PI * 2, r);
        pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
      }
      svg.appendChild(
        svgEl("polygon", {
          points: pts.join(" "),
          fill: "none",
          stroke: "var(--border)",
          "stroke-dasharray": "2 3",
          "stroke-width": 1,
        })
      );
    }
  }

  if (n === 0) return;

  // Axes + labels.
  for (let i = 0; i < n; i++) {
    const angle = (i / n) * Math.PI * 2;
    const [x1, y1] = polar(angle, 0);
    const [x2, y2] = polar(angle, MAX_RADIUS);
    svg.appendChild(
      svgEl("line", {
        x1,
        y1,
        x2,
        y2,
        stroke: "var(--border)",
        "stroke-width": 1,
      })
    );

    const [lx, ly] = polar(angle, MAX_RADIUS + 18);
    const label = svgEl("text", {
      x: lx,
      y: ly,
      fill: "var(--fg-muted)",
      "font-size": 10,
      "text-anchor": "middle",
      "dominant-baseline": "middle",
    });
    label.textContent = domains[i].domain;
    svg.appendChild(label);
  }

  // Filled polygon of weighted scores.
  const pts = [];
  for (let i = 0; i < n; i++) {
    // Clamp 0..100 so a bad entry can't spike off the chart.
    const value = Math.max(0, Math.min(100, domains[i].weighted_score ?? 0));
    const r = (value / 100) * MAX_RADIUS;
    const [x, y] = polar((i / n) * Math.PI * 2, r);
    pts.push([x, y]);
  }

  svg.appendChild(
    svgEl("polygon", {
      points: pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" "),
      fill: "rgba(119, 212, 167, 0.20)",
      stroke: "var(--accent)",
      "stroke-width": 2,
      "stroke-linejoin": "round",
    })
  );

  // Dots + per-axis value labels.
  for (let i = 0; i < n; i++) {
    const [x, y] = pts[i];
    svg.appendChild(
      svgEl("circle", {
        cx: x,
        cy: y,
        r: 3,
        fill: "var(--accent)",
      })
    );
  }
}
