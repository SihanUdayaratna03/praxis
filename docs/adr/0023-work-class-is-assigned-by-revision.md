---
id: ADR-0023
status: accepted
date: 2026-08-29
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0023 — Work class is assigned by revising a record, against the vocabulary the store already holds

## Chosen

`WorkClassifier` does not write estimates. It **revises** them: it reads every
estimate whose `work_class` is `unclassified`, asks once, and writes a new
version plus an audit row through `Repository.revise`. Append-only, invariant 7,
and the same shape `AssumptionFormalizer` uses for a compiled predicate.

Three consequences follow, and each is the point rather than a side effect.

**It repairs records it did not write.** `AssumptionExtractor` has been leaving
`unclassified` estimates since Phase 4, and `praxis/agents/extractor.py` said in
as many words that this agent would own the field. Half B improves Half A's rows
without Half A changing, which is what leaving the field revisable bought.

**The vocabulary is offered, not assumed.** The classifier is shown the classes
already in the store, numbered in the same bracketed shape every offering in this
pipeline uses, and asked to prefer one. It may propose a new class, and the
result records **which it did** — computed against the store rather than taken
from the model's own claim, because a model saying it picked an existing class
does not make it one. A run that invents a class per estimate is then a number
in the eval table rather than a discovery six phases later.

**Spelling is repaired; meaning is not.** `work_class_of` lower-cases and joins
words with hyphens, so `Data Migration` and `data migration` are one class.
Anything that is not a run of words becomes `unclassified` rather than being
massaged into something plausible. The function is property-tested as total and
idempotent against the record model, which is the authority on what the field
accepts. It lives in this module and `AssumptionExtractor` calls into it, so the
one function standing between a model's prose and a grouping key cannot become
two.

`created_by` is **not** changed by the revision. It names the agent that
*produced* the record; the audit trail names who wrote each version.

## Rejected

| Option | Why not |
| ------ | ------- |
| Classify at extraction time, in `EstimateExtractor` | A plausible guess is indistinguishable from a right answer and is paid for months later by somebody reading a calibration number computed over the wrong rows. It also could not repair the rows Phase 4 already wrote. Phase 4 chose the fallback deliberately and this is the other half of that choice. |
| A closed, hard-coded vocabulary | Wrong for a product that does not know what its users build. It would force every real class into the nearest listed one and make the grouping key a property of this repository rather than of the organisation using it. |
| Let the model name a class freely, with no listing | The failure this agent exists to prevent. `data-migration` and `database-migration` are one class written twice, and they turn one estimator's ten migrations into two sets of five under a detective that refuses below `n = 5`. Nothing raises; the history is just worth less. |
| Update `work_class` in place | Invariant 7, and it would destroy the evidence that the row was ever unclassified — which is the only thing distinguishing "nobody has looked at this" from "somebody decided it was `backend`". |
| Stamp `WorkClassifier` into `created_by` on the new version | Tried, and it broke something real: `EstimationPipeline` asks the store which documents `EstimateExtractor` has already read, so a classified document looked unread and would have been re-extracted, allocating a second estimate per passage on every run. `AssumptionFormalizer` already leaves the field alone; the two agents now agree. |
| Re-ask about an estimate that came back `unclassified` | Pays the scan tier for the same failure on every run, forever. The audit trail already records that this agent has written a version of the record, so the skip needs no column — the same argument `praxis.agents.formalization` makes. |

## What was known at the time

The corpus uses seven classes across eight topics — `infrastructure`,
`backend`, `mobile`, `migration`, `data-engineering` — and grades `work_class`
as an expected field on every estimate, so the classification is measurable
rather than merely present.

**Two rates are reported and the higher one is not the better one.** An agent
that classifies everything wrongly scores 1.0 on the classified rate and 0.0 on
class accuracy; one that classifies nothing scores 0.0 on both, and that is the
*safer* failure — `BiasDetective` can exclude an `unclassified` row and cannot
detect a confidently wrong one. The report prints that sentence above the two
numbers rather than leaving a reader to compare them the wrong way round.

The bug in the rejected-options table was found by an integration test and not
by any unit test, because each agent was correct in isolation and the
interaction was not. That is recorded here because it is the argument for the
store-level test existing at all.

Not known: whether a real organisation's vocabulary converges under this scheme
or fragments anyway. `proposed` is reported per run precisely so that question
has an answer before anyone has to guess.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The vocabulary converges rather than growing per estimate | `proposed_classes_per_run <= 3` | `when(stored_estimates >= 50)` |
| 2 | Two spellings of one class never both reach the store | `duplicate_class_spellings == 0` | `when(stored_estimates >= 50)` |
| 3 | A second pass classifies nothing and costs no call | `classifier_calls_on_unchanged_store == 0` | `on_event("the first live classification run")` |
| 4 | Most estimates end up on the axis rather than unclassified | `classified_rate >= 0.7` | `on_event("the first live classification run")` |

## Consequences

**Accepted costs.** One call per unclassified estimate on the scan tier, which
is the highest-volume Half B call and grows linearly with the corpus. It is
bounded by being paid once per estimate ever, not once per run.

The listing is capped at forty classes. A store past that has a fragmentation
problem a prompt cannot fix, and truncating visibly is better than pretending
the model weighed three hundred.

A class assigned once is not revisited. If the vocabulary later improves, the
old rows keep their old class until somebody forces a re-run — and `--force`
does not exist on this command yet, which is a gap rather than a decision.

**Reversal cost.** Low. The field is already revisable by construction, so
changing how it is assigned is changing one agent; every previous assignment
stays readable as an earlier version, which is the property that made revision
the right mechanism in the first place.
