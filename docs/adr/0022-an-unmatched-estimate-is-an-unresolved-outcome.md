---
id: ADR-0022
status: accepted
date: 2026-08-29
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0022 — An unmatched estimate is an `unresolved` outcome, never a silence

## Chosen

Every estimate that reaches `OutcomeMatcher` leaves it with exactly one
`Outcome`. Where nothing in the corpus reports an actual, that row is written
with `match_quality = unresolved`, carrying no quantity and no resolution time —
which the record model and the SQL `CHECK` constraint both enforce — and a note
naming which stage lost the pairing.

Six causes are recorded and reported apart, because a low match rate means six
different things and only three are about the model:

| Cause | What it means |
| ----- | ------------- |
| `no_candidates` | Deterministic selection offered nothing. No call was made. |
| `model_found_none` | The model read the passages and said none resolves this. The ordinary case. |
| `no_usable_answer` | The model refused or never satisfied the schema. |
| `uncited` | A passage or quotation that did not survive the citation gate. |
| `incomparable_units` | An actual in a unit with no honest conversion. |
| `incoherent_record` | Well cited, and not a record the store will hold. |

The *row* is identical in every case; only the note differs. That identity is
what makes re-running free, and it is also why the split is reported from the
run rather than read back from the store.

## Rejected

| Option | Why not |
| ------ | ------- |
| Write nothing when no outcome is found | The failure the `unresolved` member was added to Phase 1 to prevent, in the record's own words: an unresolved outcome exists so estimates that never resolved stay visible in the calibration data instead of being dropped, "which is how a curve ends up flattering its estimator". An estimator whose misses silently leave the sample looks better the more often they are missed. |
| Leave it out and use a `LEFT JOIN` in Phase 7 | Moves the problem to every future reader instead of solving it once. It also splits the two numbers that have to agree: `n` would come from one query and the unresolved count from another, and the honest denominator is the one that goes missing when they drift. Writing the query first is what showed this — see ADR 0008's precedent of settling a table's shape against the query that will read it. |
| One `unmatched` boolean instead of six causes | A low match rate becomes unattributable. "Blocking never proposed the pair" and "the model read it and declined" call for opposite responses — one is a selection bug and one is correct behaviour — and a single flag cannot distinguish them. |
| Record the cause as a `Finding` as well | Two records for one fact. `FindingKind` has no member for it, so it is a migration for a record nothing reads until the reporting layer exists. In `BACKLOG.md` with the first consumer named. |
| Re-ask on every run until something resolves | An estimate nobody ever wrote an actual for is the ordinary case, so this pays the extract tier forever for the commonest outcome in the corpus. The existing row is the record that the question was asked and answered. |
| Give the unresolved row the estimate's span | A citation nobody checked, standing in a column whose entire purpose is that every citation was checked. It cites nothing because it claims nothing. |

## What was known at the time

**The corpus's ceiling is 3 of 9, by construction.** `praxis/corpus/` states nine
estimates and resolves three of them: `status_update` records an estimate and
its actual joined by `resolves_item_id`, and `issue_export` records an estimate
with no outcome at all. The six unmatched ones are not a gap in the corpus —
they are the case this ADR is about, planted before the agent that has to handle
it was written. A matcher scoring 1.0 would have invented six pairings.

The report therefore prints the ceiling beneath the match rate, so a 0.33 is not
read as a failure by someone comparing it against a precision on the line above.

**Offline the rate says less than it looks.** The citation gate refuses almost
every extraction against the mock (ADR 0016), so few estimates reach the matcher
at all and the rate measures plumbing rather than matching. That is stated in the
report rather than left for a reader to infer.

Not known: what fraction of estimates a real corpus resolves. Three of nine is a
property of a corpus written to exercise both paths, not a measurement of
anything.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Every estimate the matcher sees leaves with exactly one outcome | `estimates_without_an_outcome == 0` | `on_event("a second agent writes an Outcome")` |
| 2 | Unresolved rows stay a minority once a real corpus is loaded | `unresolved_share <= 0.8` | `when(stored_outcomes >= 30)` |
| 3 | The Phase 7 query needs no outer join | `calibration_query_outer_joins == 0` | `on_event("CalibratorAgent is written")` |
| 4 | A second pass writes no duplicate outcome | `duplicate_outcomes_per_estimate == 0` | `on_event("the first live matching run")` |

## Consequences

**Accepted costs.** The `outcome` table grows one row per estimate whether or
not anything was found, so it is as large as the `estimate` table by
construction. That is the price of the denominator being a fact rather than an
inference, and at this corpus size it is not a cost anyone can measure.

An estimate whose actual is reported in a *different* document stays unresolved,
because ADR 0015 makes an offering one document's spans and `OutcomeMatcher`
respects it. That is a real recall cost, taken deliberately, and it is
`FusionBridge`'s to recover in Phase 8 — recorded in `BACKLOG.md` rather than
half-built here.

**Reversal cost.** Low in code and high in data. Dropping the unresolved write is
a few lines; recovering the rows afterwards is impossible, because the fact they
record is precisely that a question was asked and nothing answered it, and
nothing else in the store remembers that a question was asked.
