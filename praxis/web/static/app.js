// The dashboard shell: a hash router, one fetch per view, and the Overview.
//
// No framework (ADR 0036). Views render to a string and are written once, so
// there is no diffing to get wrong.

const { esc, sparkline } = window.charts;

const VIEWS = [
  { id: "overview", label: "Overview", glyph: "M3 10.5 10 4l7 6.5M5 9.5V16h10V9.5" },
  { id: "decisions", label: "Decisions", glyph: "M5 5h10M5 10h10M5 15h6" },
  { id: "assumptions", label: "Assumptions", glyph: "M10 3 17 16H3zM10 8v4M10 14v.5" },
  { id: "estimates", label: "Estimates", glyph: "M4 16V8M8 16V4M12 16v-6M16 16v-9" },
  { id: "calibration", label: "Calibration", glyph: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM10 7v3l2 2" },
  { id: "queue", label: "Review queue", glyph: "M4 5h12v11H4zM7 9h6M7 12h4" },
  { id: "reasoning", label: "Reasoning", glyph: "M4 5h12v8H8l-4 3z" },
  { id: "timeline", label: "Audit trail", glyph: "M6 3v14M6 6h9M6 11h6" },
];

// One glyph per KPI card, as the asset gives them.
const GLYPH = {
  graph: "M6 6.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM14 4a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM13 12a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM7.8 7.6 12.2 6M8 10.2l3.4 2.4",
  warn: "M10 4 17 16H3zM10 8.5v3.5M10 13.6v.4",
  shield: "M10 3.5 16 6v4.5c0 3-2.5 5-6 6-3.5-1-6-3-6-6V6zM7.3 10.2 9.3 12l3.4-3.6",
  target: "M10 3.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM10 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM10 9.6v.8",
};

const view = document.getElementById("view");
const title = document.getElementById("view-title");

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} answered ${response.status}`);
  return response.json();
}

function icon(path) {
  return `<svg width="19" height="19" viewBox="0 0 20 20" fill="none" stroke="currentColor"
    stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="${path}"/></svg>`;
}

function drawNav(current) {
  document.getElementById("nav").innerHTML = VIEWS.map(
    (v) =>
      `<a href="#${v.id}" class="${v.id === current ? "on" : ""}">${icon(v.glyph)}${v.label}</a>`,
  ).join("");
}

// --- shared pieces ---------------------------------------------------------

function panel(heading, body, link) {
  const action = link ? `<a class="view-all" href="#${link}">View all →</a>` : "";
  return `<section class="panel">
      <div class="panel-head"><h3>${esc(heading)}</h3>${action}</div>
      ${body}
    </section>`;
}

function empty(message) {
  return `<p class="empty">${esc(message)}</p>`;
}

// How this kind of record accumulated, in the order the store wrote it.
//
// Over write order rather than over dates: a seeded store writes everything in
// one instant, and bucketing by day would give one point and no line. This is
// the store growing, which is what it says it is.
function seriesFrom(events, kind) {
  const matching = events.filter((e) => e.entity_kind === kind).reverse();
  if (matching.length < 2) return [];
  const buckets = Math.min(28, matching.length);
  const size = matching.length / buckets;
  const series = [];
  for (let i = 1; i <= buckets; i += 1) series.push(Math.round(i * size));
  return series;
}

// --- the Overview ----------------------------------------------------------

function kpi(glyphColour, value, label, series, glyph) {
  return `<article class="kpi">
      <div class="kpi-top">
        <span class="kpi-glyph" style="color:${glyphColour}">${icon(glyph)}</span>
        <div>
          <div class="n">${esc(value)}</div>
          <div class="k">${esc(label)}</div>
        </div>
      </div>
      ${sparkline(series, glyphColour)}
    </article>`;
}

async function overview() {
  const [data, timeline, queue] = await Promise.all([
    api("/api/overview"),
    api("/api/timeline?limit=500"),
    api("/api/queue"),
  ]);
  document.getElementById("provider").textContent = `${data.provider} · offline`;
  document.getElementById("schema").textContent = data.schema_version;
  document.getElementById("counts").textContent =
    `${data.decisions} decisions · ${data.assumptions} assumptions`;

  const events = timeline.events;
  const valid = data.assumptions ? `${Math.round(data.assumptions_valid * 100)}%` : "—";
  const bias = data.calibration_bias ? `${Number(data.calibration_bias).toFixed(2)}×` : "n/a";

  const cards = `<div class="kpis">
      ${kpi("var(--teal)", data.decisions, "Decisions", seriesFrom(events, "decision"), GLYPH.graph)}
      ${kpi("var(--red)", data.at_risk, "At risk", seriesFrom(events, "finding"), GLYPH.warn)}
      ${kpi("var(--blue)", valid, "Assumptions valid", seriesFrom(events, "assumption"), GLYPH.shield)}
      ${kpi("var(--violet)", bias, `Calibration bias (n=${data.calibration_n})`, seriesFrom(events, "estimate"), GLYPH.target)}
    </div>`;

  const health = Object.entries(data.assumption_health)
    .map(
      ([status, n]) =>
        `<div class="row"><span class="is-${esc(status)}">●</span>
           <span>${esc(status)}</span><span class="count">${n}</span></div>`,
    )
    .join("");

  const audit = events.length
    ? events
        .slice(0, 12)
        .map(
          (e) => `<div class="row">
            <div>
              <div>${esc(e.entity_id)} <span class="dim">${esc(e.action)}</span></div>
              <div class="faint" style="font-size:12.5px">${esc(e.reason)}</div>
            </div>
            <span class="count faint" style="font-weight:400;font-size:12px">
              ${esc(e.occurred_at.slice(0, 10))}</span>
          </div>`,
        )
        .join("")
    : empty("Nothing has been written yet.");

  const items = queue.length
    ? queue
        .slice(0, 6)
        .map(
          (item) => `<a class="row" href="#queue">
            <div>
              <span class="tag tag-${esc(item.finding.severity)}">${esc(item.finding.severity)}</span>
              <div style="margin-top:6px">${esc(item.subject_label)}</div>
            </div>
          </a>`,
        )
        .join("")
    : empty("Nothing needs a person. Run praxis monitor to evaluate the predicates.");

  view.innerHTML = `${cards}
    <div class="grid">
      <div class="stack" id="provenance-slot">
        <p class="loading">Reading the argument chain…</p>
      </div>
      <div class="stack">
        ${panel("Assumption health", health, "assumptions")}
        ${panel("Review queue", items, "queue")}
      </div>
      <div class="stack span-rest">
        ${panel("Append-only audit", audit, "timeline")}
      </div>
    </div>`;

  if (window.views?.provenance) {
    document.getElementById("provenance-slot").innerHTML = await window.views.provenance();
  }
}

// --- routing ---------------------------------------------------------------

const ROUTES = { overview };

async function route() {
  const id = (location.hash.replace("#", "") || "overview").split("/")[0];
  const known = VIEWS.find((v) => v.id === id) ? id : "overview";
  drawNav(known);
  title.textContent = VIEWS.find((v) => v.id === known).label;
  view.innerHTML = '<p class="loading">Reading the store…</p>';
  const render = ROUTES[known] || window.views?.[known];
  try {
    if (render) {
      await render();
    } else {
      view.innerHTML = empty("This view is not built yet.");
    }
  } catch (error) {
    view.innerHTML = `<p class="empty">${esc(error.message)}</p>`;
  }
}

window.addEventListener("hashchange", route);
window.praxisApi = api;
window.praxisUi = { panel, empty, api };
route();
