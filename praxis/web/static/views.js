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

  // --- calibration ---------------------------------------------------------

  const median = (numbers) => {
    const sorted = [...numbers].sort((a, b) => a - b);
    if (!sorted.length) return 0;
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  };

  // The lens applies the group's factor to a typical estimate from that group,
  // so raw and corrected are both quantities rather than multipliers.
  function lens(factor, rows) {
    const mine = rows.filter(
      (r) => r.work_class === factor.work_class && r.owner === factor.owner,
    );
    const raw = median(mine.map((r) => Number(r.estimated_active)));
    const corrected = raw * Number(factor.factor);
    const low = raw * Number(factor.low);
    const high = raw * Number(factor.high);
    const unit = mine.length ? mine[0].unit : "";
    return `<div class="lens">
        <div><span class="lens-k">Raw</span>
          <span class="lens-v" style="color:var(--violet)">${raw.toFixed(1)}${esc(unit[0] ?? "")}</span></div>
        <div><span class="lens-k">Corrected</span>
          <span class="lens-v" style="color:var(--teal)">${corrected.toFixed(1)}${esc(unit[0] ?? "")}</span></div>
        <div><span class="lens-k">Band</span>
          <span class="lens-v" style="font-size:16px">${low.toFixed(1)}–${high.toFixed(1)}</span></div>
        <div><span class="lens-k">n</span><span class="lens-v">${factor.n}</span></div>
        <div><span class="lens-k">Confidence</span>
          <span class="lens-v">${Number(factor.confidence).toFixed(2)}</span></div>
      </div>
      <div style="padding:0 var(--s5) var(--s5)">${bellCurves(raw, corrected, low, high)}</div>
      <p class="panel-note" style="padding-top:0">${esc(factor.describe)}</p>`;
  }

  function historyTable(rows) {
    const body = rows
      .map((r) => {
        const actual = r.actual_active;
        const ratio = actual ? Number(actual) / Number(r.estimated_active) : null;
        const tone = !ratio ? "faint" : ratio > 1 ? "is-breached" : "is-valid";
        return `<tr>
          <td><span class="item-id">${esc(r.estimate_id)}</span></td>
          <td>${esc(r.work_class)}</td>
          <td class="num">${esc(r.estimated_active)}</td>
          <td class="num">${actual === null ? "—" : esc(actual)}</td>
          <td class="num ${tone}">${ratio ? `${ratio.toFixed(2)}×` : "unresolved"}</td>
          <td>${esc(r.match_quality)}</td>
        </tr>`;
      })
      .join("");
    return `<div class="scroller"><table class="table">
        <thead><tr><th>Estimate</th><th>Class</th><th class="num">Predicted</th>
          <th class="num">Actual</th><th class="num">Ratio</th><th>Quality</th></tr></thead>
        <tbody>${body}</tbody></table></div>`;
  }

  async function calibration() {
    const data = await api("/api/calibration");
    const speaking = data.factors.filter((f) => f.speaks);
    const lensPanel = speaking.length
      ? panel(`Calibration lens · ${speaking[0].work_class}`, lens(speaking[0], data.history))
      : panel(
          "Calibration lens",
          `<p class="panel-note">No group has reached ${data.minimum_sample} resolved
             estimates, so nothing is corrected. That is the threshold working.</p>`,
        );
    view.innerHTML =
      head(
        `${data.factors.length} calibration group(s)`,
        `${speaking.length} with enough history to speak · refuses below n=${data.minimum_sample}`,
      ) +
      `<div class="grid">
        <div class="stack">
          ${lensPanel}
          ${panel("Every estimate beside its actual", historyTable(data.history))}
        </div>
        <div class="stack span-rest">
          ${panel("Per person, per class of work", `<div class="bars">${factorBars(data.factors)}</div>`)}
        </div>
      </div>`;
  }

  // --- the review queue ----------------------------------------------------

  async function queue() {
    const rows = await api("/api/queue");
    if (!rows.length) {
      view.innerHTML =
        head("Review queue", "nothing needs a person") +
        panel(
          "Empty",
          `<p class="panel-note">No finding is standing. Run <code>praxis monitor</code> to
             evaluate the predicates, or <code>praxis fuse</code> to price the assumptions that
             are estimates in disguise.</p>`,
        );
      return;
    }
    const undecided = rows.filter((r) => r.finding.verdict === "undecided").length;
    view.innerHTML =
      head(`${rows.length} finding(s)`, `${undecided} not yet challenged · most severe first`) +
      `<div class="stack">${rows
        .map(
          (item) => `<section class="panel"><div class="prov" style="padding:var(--s5)">
            <div class="item-meta" style="margin-top:0">
              <span class="tag tag-${esc(item.finding.severity)}">${esc(item.finding.severity)}</span>
              <span>${esc(item.finding.kind.replaceAll("_", " "))}</span>
              <span>${esc(item.finding.subject_id)}</span>
              <span>${esc(item.finding.verdict)}</span>
              <span>${esc(day(item.finding.detected_at))}</span>
            </div>
            <p style="margin-top:var(--s4);font-weight:600">${esc(item.subject_label)}</p>
            <p class="sentence" style="margin-top:var(--s3)">${esc(item.finding.prosecution)}</p>
            ${
              item.finding.challenge
                ? `<p class="panel-note" style="padding:var(--s4) 0 0">
                     <strong>Challenged:</strong> ${esc(item.finding.challenge)}</p>`
                : `<p class="panel-note" style="padding:var(--s4) 0 0">
                     Not yet challenged. <code>praxis govern</code> argues against a finding
                     before a person sees it.</p>`
            }
          </div></section>`,
        )
        .join("")}</div>`;
  }

  // --- assumptions ---------------------------------------------------------

  async function assumptions() {
    const rows = await api("/api/assumptions");
    const counted = rows.reduce((acc, a) => ({ ...acc, [a.status]: (acc[a.status] || 0) + 1 }), {});
    const body = rows
      .map(
        (a) => `<tr>
          <td><span class="item-id">${esc(a.id)}</span></td>
          <td>${esc(a.statement)}</td>
          <td><code>${esc(a.predicate)}</code></td>
          <td><code class="faint">${esc(a.expiry_condition)}</code></td>
          <td class="is-${esc(a.status)}">${esc(a.status)}</td>
        </tr>`,
      )
      .join("");
    view.innerHTML =
      head(
        `${rows.length} assumptions`,
        Object.entries(counted)
          .map(([k, n]) => `${n} ${k}`)
          .join(" · "),
      ) +
      panel(
        "Every predicate the store holds",
        `<div class="scroller"><table class="table">
           <thead><tr><th>Id</th><th>Statement</th><th>Predicate</th>
             <th>Expires when</th><th>Status</th></tr></thead>
           <tbody>${body}</tbody></table></div>`,
      );
  }

  // --- estimates -----------------------------------------------------------

  async function estimates() {
    const rows = await api("/api/estimates");
    const body = rows
      .map(
        (e) => `<tr>
          <td><a href="#estimates/${esc(e.id)}"><span class="item-id">${esc(e.id)}</span></a></td>
          <td>${esc(e.subject.slice(0, 110))}${e.subject.length > 110 ? "…" : ""}</td>
          <td>${esc(e.work_class)}</td>
          <td class="num">${esc(e.active_quantity)}</td>
          <td class="num">${esc(e.blocked_quantity)}</td>
          <td class="num">${Number(e.confidence).toFixed(2)}</td>
          <td>${esc(day(e.estimated_at))}</td>
        </tr>`,
      )
      .join("");
    view.innerHTML =
      head(`${rows.length} estimates`, "every prediction, logged before the work")+
      panel(
        "Predictions",
        `<div class="scroller"><table class="table">
           <thead><tr><th>Id</th><th>Subject</th><th>Class</th><th class="num">Active</th>
             <th class="num">Blocked</th><th class="num">Confidence</th><th>Logged</th></tr></thead>
           <tbody>${body}</tbody></table></div>`,
      );
  }

  async function estimate(id) {
    const detail = await api(`/api/estimates/${encodeURIComponent(id)}`);
    const e = detail.estimate;
    const o = detail.outcome;
    const conditions = e.conditions.length
      ? e.conditions.map((c) => `<div class="row">${esc(c)}</div>`).join("")
      : empty("No conditions recorded.");
    const impacted = detail.impacted.length
      ? detail.impacted
          .map(
            (n) => `<div class="row"><span class="item-id">${esc(n.id)}</span>
              <span class="dim">${esc(n.kind)}</span>
              <span class="count faint" style="font-weight:400">${n.depth} hop(s)</span></div>`,
          )
          .join("")
      : empty("Nothing recorded rests on this estimate.");
    const result = o
      ? `<div class="lens">
           <div><span class="lens-k">Predicted</span>
             <span class="lens-v">${esc(e.active_quantity)}</span></div>
           <div><span class="lens-k">Actual</span>
             <span class="lens-v">${esc(o.active_quantity ?? "—")}</span></div>
           <div><span class="lens-k">Blocked</span>
             <span class="lens-v">${esc(o.blocked_quantity ?? "—")}</span></div>
           <div><span class="lens-k">Quality</span>
             <span class="lens-v" style="font-size:16px">${esc(o.match_quality)}</span></div>
         </div>
         <p class="panel-note">${esc(o.notes || "No notes recorded.")}</p>`
      : empty("Nothing has answered this estimate yet.");

    view.innerHTML =
      `<div class="list-head"><h2>${esc(e.id)}</h2>
        <span class="faint">${esc(e.work_class)} · ${esc(e.owner)} · ${esc(day(e.estimated_at))}</span>
      </div>
      <div class="grid">
        <div class="stack">
          ${panel("Subject", `<div class="panel-note">${esc(e.subject)}</div>`)}
          ${panel("What happened", result)}
          ${panel("Conditions it assumed", conditions)}
        </div>
        <div class="stack span-rest">
          ${panel("What rests on it", impacted)}
        </div>
      </div>`;
  }

  // --- the reasoning panel -------------------------------------------------

  // Phase 2 has recorded every model call since the first agent ran. This is
  // the first thing to show one.
  async function reasoning() {
    const data = await api("/api/traces?limit=40");
    if (!data.total) {
      view.innerHTML =
        head("Reasoning", "no model call has been recorded in this store") +
        panel(
          "Nothing to show, and that is the correct answer",
          `<p class="panel-note">Every model call is written to the trace store with the exact
             request, the raw answer and what it cost. This store was seeded deterministically —
             <code>praxis demo seed</code> calls no model — so there is nothing to read.
             Run <code>praxis extract</code> or <code>praxis estimates</code> and this panel
             fills with what each agent was actually asked and actually said.</p>`,
        );
      return;
    }
    const rows = data.traces
      .map(
        (t) => `<details class="trace">
          <summary>
            <span class="agent">${esc(t.agent)}</span>
            <span class="dim">${esc(t.task)}</span>
            <span class="faint">${esc(t.provider)} · ${esc(t.model_id)}</span>
            <span class="count faint" style="font-weight:400">
              ${esc(t.outcome)} · ${t.latency_ms}ms · $${esc(t.cost_usd)}</span>
          </summary>
          <pre>${esc(t.request_json)}</pre>
          <pre>${esc(t.response_text || t.error || "(no answer)")}</pre>
        </details>`,
      )
      .join("");
    view.innerHTML =
      head(`${data.total} model call(s)`, `${data.runs.length} run(s) · newest first`) +
      panel("What every agent was asked, and what it said", rows);
  }

  // --- the audit timeline --------------------------------------------------

  async function timeline() {
    const data = await api("/api/timeline?limit=200");
    if (!data.events.length) {
      view.innerHTML = head("Audit trail", "nothing written yet") + empty("The store is empty.");
      return;
    }
    const rows = data.events
      .map(
        (e) => `<tr>
          <td class="faint">${esc(e.occurred_at.replace("T", " ").slice(0, 19))}</td>
          <td><span class="item-id">${esc(e.entity_id)}</span></td>
          <td>${esc(e.entity_kind)}</td>
          <td class="dim">${esc(e.action)}</td>
          <td class="num">v${e.entity_version}</td>
          <td>${esc(e.reason)}</td>
          <td class="faint">${esc(e.actor)}</td>
        </tr>`,
      )
      .join("");
    view.innerHTML =
      head(
        `${data.total} write(s)`,
        `showing ${data.events.length}, newest first · append-only, nothing here is ever revised`,
      ) +
      panel(
        "Every write this store has recorded",
        `<div class="scroller"><table class="table">
           <thead><tr><th>When</th><th>Record</th><th>Kind</th><th>Action</th>
             <th class="num">Version</th><th>Why</th><th>Actor</th></tr></thead>
           <tbody>${rows}</tbody></table></div>`,
      );
  }

  window.views = {
    ...(window.views || {}),
    decisions,
    decision,
    calibration,
    queue,
    assumptions,
    estimates,
    estimate,
    reasoning,
    timeline,
  };
})();
