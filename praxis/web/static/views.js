// The list views and the drill-downs. Each renders to a string from one read.

(function () {
  const { esc, bellCurves, factorBars } = window.charts;
  const { panel, empty, api } = window.praxisUi;
  const view = document.getElementById("view");

  const day = (iso) => String(iso ?? "").slice(0, 10);

  function head(title, note) {
    return `<div class="list-head"><h2>${esc(title)}</h2>
      <span class="faint">${esc(note)}</span></div>`;
  }

  // --- decisions -----------------------------------------------------------

  async function decisions() {
    const rows = await api("/api/decisions");
    if (!rows.length) return void (view.innerHTML = empty("No decisions yet."));
    const at_risk = rows.filter((r) => r.at_risk).length;
    view.innerHTML =
      head(`${rows.length} decisions`, `${at_risk} resting on something that has failed`) +
      `<div class="cards-grid">${rows
        .map(
          (r) => `<a class="item ${r.at_risk ? "risk" : ""}" href="#decisions/${esc(
            r.decision.id,
          )}">
            <div class="item-id">${esc(r.decision.id)} · ${esc(day(r.decision.decided_at))}</div>
            <h3>${esc(r.decision.title)}</h3>
            <div class="item-meta">
              <span>${r.assumptions} assumption${r.assumptions === 1 ? "" : "s"}</span>
              ${r.breached ? `<span class="is-breached">${r.breached} breached</span>` : ""}
              ${r.findings ? `<span class="is-expiring">${r.findings} finding(s)</span>` : ""}
              <span>${esc(r.decision.impact)} impact</span>
            </div>
          </a>`,
        )
        .join("")}</div>`;
  }

  // One decision, the whole chain under it, and its audit trail.
  async function decision(id) {
    const detail = await api(`/api/decisions/${encodeURIComponent(id)}`);
    const d = detail.decision;
    const lines = detail.lines.length
      ? detail.lines
          .map(
            (line) => `<tr>
              <td><span class="item-id">${esc(line.assumption.id)}</span><br>
                  ${esc(line.assumption.statement)}</td>
              <td><code>${esc(line.assumption.predicate)}</code></td>
              <td class="is-${esc(line.assumption.status)}">${esc(line.assumption.status)}
                ${line.findings
                  .map(
                    (f) =>
                      `<div style="margin-top:6px"><span class="tag tag-${esc(f.severity)}">${esc(
                        f.severity,
                      )}</span></div>`,
                  )
                  .join("")}</td>
              <td>${line.estimate ? esc(line.estimate.id) : '<span class="faint">—</span>'}</td>
              <td>${
                line.outcome
                  ? esc(`${line.outcome.active_quantity ?? "—"} ${line.outcome.unit}`)
                  : '<span class="faint">unresolved</span>'
              }</td>
            </tr>`,
          )
          .join("")
      : "";

    const chain = lines
      ? `<div class="scroller"><table class="table">
           <thead><tr><th>Assumption</th><th>Predicate</th><th>Status</th>
             <th>Estimate</th><th>Actual</th></tr></thead>
           <tbody>${lines}</tbody></table></div>`
      : empty("This decision records no assumptions.");

    const rejected = d.rejected
      .map(
        (r) => `<tr><td>${esc(r.option)}</td><td class="dim">${esc(r.reason)}</td></tr>`,
      )
      .join("");

    // A finding about an assumption is a finding about the decision on it.
    const allFindings = [...detail.findings, ...detail.lines.flatMap((l) => l.findings)];

    const audit = detail.audit
      .map(
        (e) => `<div class="row"><span class="dim">${esc(e.action)}</span>
          <span>${esc(e.reason)}</span>
          <span class="count faint" style="font-weight:400">v${e.entity_version}</span></div>`,
      )
      .join("");

    view.innerHTML =
      `<div class="list-head">
        <h2>${esc(d.title)}</h2>
        <span class="faint">${esc(d.id)} · ${esc(day(d.decided_at))} · ${esc(d.status)}</span>
      </div>
      <div class="grid">
        <div class="stack">
          ${panel("Chosen", `<div class="panel-note">${esc(d.chosen)}</div>`)}
          ${panel("The argument chain", chain)}
          ${panel(
            "Rejected",
            `<div class="scroller"><table class="table">
               <thead><tr><th>Option</th><th>Why not</th></tr></thead>
               <tbody>${rejected}</tbody></table></div>`,
          )}
        </div>
        <div class="stack">
          ${panel(
            "Findings",
            allFindings.length
              ? allFindings
                  .map(
                    (f) => `<div class="row">
                      <div><span class="tag tag-${esc(f.severity)}">${esc(f.severity)}</span>
                      <div style="margin-top:6px">${esc(f.prosecution)}</div></div>
                    </div>`,
                  )
                  .join("")
              : empty("Nothing has been alleged about this decision or what it rests on."),
          )}
          ${panel("Audit trail", audit || empty("No writes recorded."))}
        </div>
      </div>`;
  }

  window.views = { ...(window.views || {}), decisions, decision };
})();
