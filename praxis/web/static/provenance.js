// The payoff: one decision's chain of argument, drawn as linked cards.
//
// Two things can put a decision here. A fusion flip -- calibration re-priced
// an assumption and the predicate changed verdict -- or a measured breach.
// Both read the same way: this decision assumed X, the evidence says Y, so Z.
//
// The sentence under the strip is PricedAssumption.describe() or the finding's
// own prosecution, verbatim, so the page and the CLI cannot drift.

(function () {
  const { esc } = window.charts;
  const api = window.praxisApi;

  const GLYPH = {
    decision: "M5 5h10M5 10h10M5 15h6",
    assumption: "M10 3 17 16H3zM10 8v4M10 14v.5",
    estimate: "M4 16V8M8 16V4M12 16v-6M16 16v-9",
    verdict: "M10 3.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM10 7v4M10 13.4v.4",
  };

  function glyph(path) {
    return `<svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor"
      stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="${path}"/></svg>`;
  }

  function node({ id, value, kind, tone, icon }) {
    return `<div class="prov-node tone-${tone}">
        <span class="prov-glyph">${glyph(icon)}</span>
        ${id ? `<span class="prov-id">${esc(id)}</span>` : ""}
        <span class="prov-value">${esc(value)}</span>
        <span class="prov-kind">${esc(kind)}</span>
      </div>`;
  }

  function callout(heading, body, href) {
    return `<div class="callout">
        <span class="mark">${glyph(GLYPH.verdict)}</span>
        <div>
          <h4>${esc(heading)}</h4>
          <p>${esc(body)}</p>
        </div>
        ${href ? `<a class="btn btn-ghost" href="${esc(href)}">Open impact view →</a>` : ""}
      </div>`;
  }

  function strip(nodes, sentence, calloutHtml, lead) {
    return `<section class="panel">
        <div class="panel-head"><h3>Decision provenance</h3></div>
        <div class="prov">
          <p class="prov-lead">${esc(lead)}</p>
          <div class="prov-strip">${nodes.join("")}</div>
          ${calloutHtml}
          <p class="sentence">${esc(sentence)}</p>
        </div>
      </section>`;
  }

  // A fusion flip: the decision, the assumption, the estimate it turned out to
  // be, and what the estimator's record implies.
  function fromFlip(flip, decision) {
    const nodes = [
      node({
        id: decision ? decision.id : null,
        value: decision ? decision.title : "a decision resting on it",
        kind: "Decision",
        tone: "blue",
        icon: GLYPH.decision,
      }),
      node({
        id: flip.assumption_id,
        value: flip.assumption.predicate,
        kind: "Assumption",
        tone: "teal",
        icon: GLYPH.assumption,
      }),
      node({
        id: flip.estimate ? flip.estimate.id : null,
        value: `${flip.subject ?? "the quantity"} = ${flip.raw}`,
        kind: "Estimate",
        tone: "violet",
        icon: GLYPH.estimate,
      }),
      node({
        value: `${flip.calibrated} implied`,
        kind: flip.verdict,
        tone: "amber",
        icon: GLYPH.verdict,
      }),
    ];
    return strip(
      nodes,
      flip.describe,
      callout(
        "Re-examine this decision",
        `Calibration re-priced this assumption and the predicate changed verdict. ${flip.factor ? flip.factor.describe : ""}`,
        decision ? `#decisions/${decision.id}` : null,
      ),
      "Trace how this estimator's record re-priced the assumption under a decision.",
    );
  }

  // A measured breach: the decision, the assumption, what was measured, and
  // the verdict the arithmetic reached.
  function fromBreach(finding, decision, assumption) {
    const nodes = [
      node({
        id: decision ? decision.id : null,
        value: decision ? decision.title : "a decision resting on it",
        kind: "Decision",
        tone: "blue",
        icon: GLYPH.decision,
      }),
      node({
        id: finding.subject_id,
        value: assumption ? assumption.predicate : finding.subject_id,
        kind: "Assumption",
        tone: "teal",
        icon: GLYPH.assumption,
      }),
      node({
        value: assumption ? assumption.statement : "measured against the facts on file",
        kind: "Evidence",
        tone: "violet",
        icon: GLYPH.estimate,
      }),
      node({
        value: "breached",
        kind: finding.kind.replaceAll("_", " "),
        tone: "amber",
        icon: GLYPH.verdict,
      }),
    ];
    return strip(
      nodes,
      finding.prosecution,
      callout(
        "Re-examine this decision",
        "The predicate under it evaluated false against the measurements on file, so the decision no longer rests on what it was made on.",
        decision ? `#decisions/${decision.id}` : null,
      ),
      "Trace how a measurement invalidated the assumption under a decision.",
    );
  }

  // Nothing to show is a state with a reason, not a blank panel.
  function refusal(fusion) {
    const why = fusion.priced.length
      ? `${fusion.priced.length} edge(s) priced, none flipped.`
      : "No estimated_as edge exists yet, so there is nothing to price. That edge is written by praxis extract (ADR 0016), not by the seeder.";
    return `<section class="panel">
        <div class="panel-head"><h3>Decision provenance</h3></div>
        <div class="prov">
          <p class="prov-lead">Nothing is currently arguing against a decision.</p>
          <p class="sentence">${esc(why)}</p>
        </div>
      </section>`;
  }

  async function decisionFor(subjectId) {
    // The decision resting on this assumption, found through the index rather
    // than through a query the store does not have.
    const summaries = await api("/api/decisions");
    for (const summary of summaries.filter((s) => s.at_risk || s.findings)) {
      const detail = await api(`/api/decisions/${summary.decision.id}`);
      const line = detail.lines.find((l) => l.assumption.id === subjectId);
      if (line) return { decision: detail.decision, assumption: line.assumption };
    }
    return { decision: null, assumption: null };
  }

  async function provenance() {
    const [fusion, queue] = await Promise.all([api("/api/fusion"), api("/api/queue")]);
    if (fusion.flips.length) {
      const flip = fusion.flips[0];
      const { decision } = await decisionFor(flip.assumption_id);
      return fromFlip(flip, decision);
    }
    const breach = queue.find((item) => item.finding.kind === "assumption_breach");
    if (breach) {
      const { decision, assumption } = await decisionFor(breach.finding.subject_id);
      return fromBreach(breach.finding, decision, assumption);
    }
    return refusal(fusion);
  }

  window.views = { ...(window.views || {}), provenance };
})();
