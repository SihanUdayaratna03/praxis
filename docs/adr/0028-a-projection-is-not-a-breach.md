---
id: ADR-0028
status: accepted
date: 2026-08-31
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0028 — a projection is not a breach

## Chosen

The fusion layer writes **two finding kinds and neither of them is
`ASSUMPTION_BREACH`**:

| Direction | Starts from | Kind | Filed against |
| --------- | ----------- | ---- | ------------- |
| Forwards, `FusionBridge` | a calibration factor | `STALE_DECISION` | the `Assumption` |
| Backwards, `CollateralAgent` | an `Outcome` that missed | `COLLATERAL_IMPACT` | the `Estimate` |

The distinction is **whether anything was measured**.

`praxis.monitor.breach` writes `ASSUMPTION_BREACH` when a predicate evaluates
false against *facts a monitoring run was given* — a measured value, supplied by
a person or bound from a resolved outcome. Something happened.

A fusion flip is a predicate evaluated false against a number **nobody has
observed yet**: the raw estimate multiplied by the estimator's historical
factor. Nothing has happened. The work may still come in on time, and the
factor is a tendency across a class of work rather than a measurement of this
piece of it.

So `stale_prosecution` opens with the words *"Nothing has been measured yet"*,
and the finding carries **no evidence spans** — there is no passage to quote,
because the allegation is about what the history implies rather than about what
a document says.

`ARCHITECTURE.md`'s fusion section says step 5 emits "an `AssumptionBreach`".
**That sentence is superseded by this record and the file is updated**, because
it was written before `praxis.monitor.breach` existed and before the word
"breach" had a specific meaning in this codebase.

## Rejected

| Option | Why not |
| ------ | ------- |
| Use `ASSUMPTION_BREACH` for the forward direction, as `ARCHITECTURE.md` says | The decision this record reverses. The two would become indistinguishable in the store, and a triage queue sorted by severity would rank a projection above a measurement whenever the projected decisions happened to be higher-impact. A person cannot un-see "this assumption is breached", and being told that about work that has not started yet is the fastest way to make the whole fusion output ignorable. |
| One `FUSION_FINDING` kind for both directions, with the difference in the prose | The prose is what nobody reads under load. A kind is queryable, sortable and countable; a distinction living only in the first sentence of `prosecution` cannot be filtered on, and the first report that groups by kind would merge them back together. |
| A `projected` boolean on `Finding` | A migration, and a weaker version of the same thing. It would leave `ASSUMPTION_BREACH` meaning two different things depending on a flag, so every existing query over breaches would silently start including projections. Adding a member to an enum that already has five costs nothing and breaks nothing. |
| Raise the forward finding against the `Decision` rather than the `Assumption` | One finding per damaged decision, which `praxis.monitor.breach` rejected for a breached assumption and this rejects for the same reason: the count of findings becomes a count of citations. The decisions are named in the prosecution, which is where a person needs them. |
| Write no forward finding at all until an outcome lands | That is the product. Waiting for the outcome is exactly what makes a decision review useless — by then the migration has taken ten weeks and the decision that assumed six is already spent. |
| Emit a finding for every priced edge, not only the flips | The common case is a factor that changes the number and changes nothing else. One finding per priced assumption per run would bury the flips, and the flips are the entire output worth having. |

## What was known at the time

`FindingKind` has carried `COLLATERAL_IMPACT` and `STALE_DECISION` since Phase
1, with nothing writing either. Phase 8 is what writes them, and the fit turned
out to be exact — which is some evidence that the Phase 1 vocabulary was drawn
from the right five allegations rather than from five that sounded plausible.

The naming is the one part worth flagging as imperfect. `STALE_DECISION` is
filed against an `Assumption`, not a `Decision`, which reads oddly until you
follow the argument: the decisions are what has gone stale, the assumption is
what made them stale, and filing against the cause is what stops one fact
becoming N findings. `COLLATERAL_IMPACT` has the same shape — filed against the
estimate that missed, naming the decisions damaged. Both follow the anchor
pattern ADR 0025 established for calibration findings, where a finding is filed
against the record the allegation is *about* rather than the records it affects.

Not known: whether a person triaging a real queue actually wants these
separated, or whether they want one list sorted by severity regardless of
provenance. `ReviewTriageAgent` is the first consumer that would answer it and
it is a Phase 9 component. Separating them now is the reversible direction —
merging two kinds later is a query change, while splitting one kind later means
re-classifying findings already written.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | A projection is never stored as a measured breach | `assumption_breaches_from_fusion == 0` | `on_event("ReviewTriageAgent reports on a real queue")` |
| 2 | Flips stay rare enough to be worth a person's attention | `flips_per_priced_edge <= 0.2` | `when(priced_edges >= 100)` |
| 3 | Readers act differently on the two kinds | `stale_decision_dismissal_rate != collateral_dismissal_rate` | `on_event("the first triage queue is reviewed by a person")` |
| 4 | The two kinds stay separately queryable | `fusion_finding_kinds == 2` | `on_event("a report merges them")` |

## Consequences

**Accepted costs.** `ARCHITECTURE.md` had to change, and a document that
described the product's central mechanism for eight phases is now different in
one step of five. That is the cost of the mechanism having been built: the
architecture was written before `breach` was a word with a definition, and
keeping the old sentence would have made the file wrong rather than merely
imprecise.

Two kinds means two places a report can forget to include. `fuse_store` returns
both directions in one `FusionRun` for that reason, so the pass is one call and
a caller has to work to drop half of it.

A reader who wants "everything wrong with this decision" now queries three
kinds rather than two. That is a real ergonomic cost and it is the one
`ReviewTriageAgent` exists to absorb in Phase 9.

**Reversal cost.** Low in one direction and high in the other, which is why the
choice went the way it did. Merging `STALE_DECISION` into `ASSUMPTION_BREACH`
later is a query change plus a migration that rewrites a column. Splitting them
apart later would mean deciding, for every breach already written, whether it
had been measured or projected — and nothing in the record would say.
