---
id: ADR-0034
status: accepted
date: 2026-09-01
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0034 — The eval harness runs a coherently-citing mock

## Chosen

`praxis.llm.synthesis` gains an **opt-in** mode in which a synthesised answer
draws its cited ordinal *first* and then quotes a sentence from **that
passage**, instead of drawing the two independently. `MockProvider` takes the
flag, `provider_for` passes it through, and `praxis eval` is the only command
that turns it on.

**The default is unchanged and stays unchanged.** With the flag off the
synthesiser is byte-identical to the one Phases 2 to 9 ran on.

## Rejected

| Option | Why not |
| ------ | ------- |
| Accept the zeros | This is the status quo and it defeats the phase. Measured before the change: over the sixteen-document corpus the citation gate refused 28 of 39 claims, integrity **0.2821**, assumption recall **0.0000**. An ablation table exists to show what each layer *adds*, and no layer can be seen adding anything to zero. Phase 10's deliverable would be a correctly-shaped table full of correct zeros. |
| Make it the default | It would move every number in nine phase reports and re-pin an unknown number of the 2948 tests, for a change none of those phases asked for. It would also delete the only offline exercise of the refusal paths: `FABRICATED_QUOTE` and `MIS_ATTRIBUTED_QUOTE` are covered today *because* the default draws incoherently, and a coherent default would leave the gate's most important branches unrun offline. |
| Disable the citation gate for the ablation's baseline rung | Invariant 6, read literally: every extracted claim carries a `span_id` that really contains it, "no exceptions for convenience". A rung with the gate off writes records that violate it, in a scratch store or not. The gate's contribution is computed counterfactually instead — a refused claim has no valid span, so it pairs with no key item, so gate-off precision is exactly `TP/(TP+FP+refused)`. Arithmetic on numbers the harness already collects. |
| Record fixtures from a live run and replay them | `ReplayProvider` exists and this is what it is for, but recording the fixtures needs a credential, and invariant 1 says no credential may be needed to run anything. A fixture set nobody without a key can regenerate is a fixture set that rots. Worth revisiting the day this project has a funded key and a recorded corpus; recorded in `BACKLOG.md`. |
| Make the mock answer *well* rather than *coherently* | Not the same change and not one this ADR would accept. Choosing the right passage is judgement, and a mock that made it would let an offline run report a recall figure that reads as evidence about a model. This mode changes only whether the quotation and the ordinal agree with each other — the passage is still drawn at random. |

`BACKLOG.md` refused a neighbouring change at Phase 9 — "a concede rate that
means something offline" — on the grounds that special-casing the mock for one
agent would make every other agent's offline numbers a measurement of that
special case. **That objection is the one to answer, and it is answered by two
properties rather than waved away.** The mode is off by default, so no agent's
existing offline numbers move at all; and it is not special-cased for an agent,
because the ordinal-and-quotation pair is the one shape every citing agent uses
through the same `CitationGate`. The Phase 9 entry stands and is not being
reversed: making a boolean answer *correctly* is still refused.

## What was known at the time

[ADR 0016](0016-the-extractor-writes-the-first-estimated-as-edge.md) already
recorded the mechanism in writing: "`praxis.llm.synthesis` draws each field
independently, so an offline `quantified` is a biased coin and the estimate's
citation agrees with its ordinal only by chance." Everything here follows from
that sentence; nothing about it is a surprise.

What was measured rather than recalled, on the corpus as it stood before this
phase: 16 documents, 50 model calls, 11 claims stored and 28 refused — 12
fabricated quotations, 8 mis-attributions, 8 empty answers. One decision found
of nine, no assumptions at all, one `estimated_as` edge of three.

What is **not** known is whether a coherent offline run's numbers mislead a
reader who skims. The mitigation is that they are labelled at every exit: the
provenance line names the provider, `praxis eval` prints the caveat, and the
phase report leads with it. That is a mitigation and not a proof, which is why
assumption 3 below is written to expire on the first live run.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | With the flag off, synthesis is byte-identical to the Phase 2 to 9 default | `default_synthesis_answers_changed == 0` | `on_event("any change to the default draw is proposed")` |
| 2 | Coherent citation lifts integrity far enough for the ablation to show a difference | `coherent_citation_integrity >= 0.8` | `on_event("the corpus or the offering rendering changes shape")` |
| 3 | No reader takes a coherent offline number as evidence about a model | `unlabelled_offline_metrics_published == 0` | `on_event("the first live extraction run")` |
| 4 | The refusal paths stay covered, because the default still draws incoherently | `refusal_branches_uncovered == 0` | `when(phases_completed >= 12)` |

## Consequences

**Accepted costs.** There are now two offline behaviours rather than one, and a
reader has to know which produced a number. The provenance line already carries
the provider; it now has to carry the mode as well, or two tables that cannot be
compared will look comparable. That is the specific way this decision goes
wrong.

**What the numbers mean.** A coherent offline run measures the *plumbing*: that
a claim which cites honestly is stored, formalized, monitored, matched,
calibrated, fused and argued with. It measures nothing about whether the passage
cited was the right one, because the passage is still drawn at random. ADR
0016's warning stands word for word, and every rung of the ablation table is
subject to it.

**Reversal cost.** Small and local. The flag defaults to off, so reverting is
deleting a branch in `_Answerer` and a keyword in two signatures. Nothing in the
store, the schema or the records depends on it.
