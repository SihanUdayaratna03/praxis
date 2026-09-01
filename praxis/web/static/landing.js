// The hero preview, filled from the same API the dashboard reads.
//
// The asset puts a screenshot here. Live numbers instead, so the landing page
// cannot claim something the store does not hold. An empty store shows zeros
// and says so.

const set = (id, text) => {
  const node = document.getElementById(id);
  if (node) node.textContent = text;
};

const percent = (fraction) => `${Math.round(fraction * 100)}%`;

function sentence(overview, fusion) {
  if (overview.decisions === 0) {
    return "This store is empty. Run `praxis demo seed` to fill it from Praxis's own history.";
  }
  const flip = fusion.flips[0];
  if (flip) {
    return flip.describe;
  }
  const priced = fusion.priced.length;
  if (priced > 0) {
    // Refusals are the honest majority on a small store. Say which, not "none".
    return `${priced} assumption(s) priced against their estimator's record; none flipped. ${fusion.priced[0].reason}`;
  }
  return `${overview.decisions} decision(s), ${overview.assumptions} assumption(s), ${overview.estimates} estimate(s). No estimated_as edge yet, so there is nothing to price.`;
}

async function load() {
  try {
    const [overview, fusion] = await Promise.all([
      fetch("/api/overview").then((r) => r.json()),
      fetch("/api/fusion").then((r) => r.json()),
    ]);

    set("p-provider", `${overview.provider} · offline`);
    set("p-decisions", overview.decisions);
    set("p-risk", overview.at_risk);
    set("p-valid", overview.assumptions ? percent(overview.assumptions_valid) : "—");
    set(
      "p-bias",
      overview.calibration_bias ? `${Number(overview.calibration_bias).toFixed(2)}×` : "n/a",
    );
    set("p-sentence", sentence(overview, fusion));
  } catch {
    set("p-sentence", "The API did not answer. Is `praxis serve` still running?");
  }
}

load();
