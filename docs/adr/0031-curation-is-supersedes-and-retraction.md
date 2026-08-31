---
id: ADR-0031
status: accepted
date: 2026-08-31
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0031 — Curate with `supersedes` and retraction, and compute it without a model

## Chosen

`CuratorAgent` collapses two assumptions by writing a `LinkType.SUPERSEDES`
edge from the later to the earlier, and retires an idle one through
`Repository.retract`. It makes **no model call** and joins `NON_LLM_AGENTS`.

An assumption is **idle** when its status is `UNVERIFIED`, its audit trail
carries no event written by `AssumptionMonitor`, and its first version was
written at least `IDLE_DAYS` before the moment being asked about.

## Rejected

| Option | Why not |
| ------ | ------- |
| Add a `RETIRED` member to `AssumptionStatus` | A migration, because the SQL `CHECK` constraints mirror the enum — and a migration to express something two existing mechanisms already express. Worse, `Assumption`'s validator ties a non-`UNVERIFIED` status to a `last_evaluated_at`, so retiring a never-evaluated assumption would mean writing an evaluation time for an evaluation that never happened. That is a lie in the record to avoid a link. |
| Reuse `AssumptionStatus.EXPIRED` | Closest of the existing members, and still wrong. `EXPIRED` means "the expiry condition fired, so the last verdict is no longer evidence", and an idle assumption has no verdict to stop being evidence. It would also make the monitoring metrics unreadable: `praxis.eval.metrics.monitoring_score` counts aged assumptions, and a curated store would inflate that number with records nothing aged out. |
| Delete the record | Invariant 7. Not seriously considered, and recorded so the space is visibly narrow rather than silently so. |
| Give the curator a model, as ADR 0006 originally routed it | The judgement it needs was already made and already paid for. "Can these two claims both be true" is `ContradictionDetector`'s question, answered in Phase 5 on the `reason` tier and written down as a `CONTRADICTS` edge; "do these two predicates say the same thing" is `constraints_of` over a parsed expression, which is arithmetic. What is left is reading an edge, comparing two shapes, ordering two timestamps and asking an audit trail a question. A model has no privileged access to any of it. |
| Count monitoring passes in a new column, so "never fires" is exact | Parallel bookkeeping for a fact the audit trail nearly answers, and it would need a write on every pass over every assumption — turning a write-on-change monitor into a write-always one, which is the property `praxis.monitor.run` was built around. The imprecision this leaves is stated in assumption 3 rather than engineered away. |

## What was known at the time

Phase 1 defined six link types and `SUPERSEDES` was one of two with nothing
writing it (`COLLATERAL_OF` was the other, and Phase 8 gave it a writer).
`endpoints_are_valid` already restricted it to pairs of the same kind, so an
assumption superseding an assumption was expressible and a decision superseding
an estimate was not. `Repository.retract` had existed since Phase 1 with its
semantics written into its own docstring: *"Not a delete. The withdrawn versions
stay readable, so a retraction is a statement about a record rather than the
disappearance of one."*

Phase 5's audit trail records every write with an actor, and
`Repository.audit_for` returns one record's events oldest first. Phase 5's
monitor writes **only on a change**, which was known and is the source of the
one imprecision below.

Six consecutive components had moved to the arithmetic side across Phases 7 and
8. `EST-0010` priced this one as the eighth and said why in advance.

What was **not** known: whether `IDLE_DAYS = 60` is the right window. There is
no data to fit it to — this project has one corpus and generated it. It is a
judgement, stated as one, the same way ADR 0024 states `MINIMUM_SAMPLE = 5`.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The curator writes no record type beyond a `supersedes` link and a retraction, so no migration is ever needed for it | `curator_migrations == 0` | `on_event("curation needs a record kind the schema does not hold")` |
| 2 | Contradiction across documents is a sound proxy for revision — the pairs it collapses really are revisions | `curator_merge_precision >= 0.9` | `on_event("a reviewer rejects a proposed merge")` |
| 3 | Reading idleness off status and the audit trail agrees with reading it off a monitoring counter nobody keeps | `idle_disagreements == 0` | `on_event("a monitoring pass records a decision without writing")` |
| 4 | Retirement stays a minority of what a curation pass considers, so it is curation rather than demolition | `retirement_rate <= 0.5` | `on_event("a curation pass proposes retiring more than half the store")` |
| 5 | Nothing a live decision rests on is ever retired | `retired_with_dependants == 0` | `on_event("any retirement names a live decision")` |

Assumption 2 is the one most likely to fire, and it should: two assumptions that
genuinely conflict are not always a revision of one another. The corpus's
revision notes are the friendly case. The bound is the rule's cost of being
wrong, written down before anyone has measured it.

## Consequences

**Accepted costs.** A curated store carries edges pointing at retracted records
forever, because nothing is deleted — so every candidate query has to narrow to
live records first, and a pass that forgot to would keep re-proposing merges it
already made. That narrowing is asserted by test rather than left to care.

Idleness cannot distinguish "monitored repeatedly and never settleable" from
"never monitored at all". Both read as `UNVERIFIED` with a silent trail, and the
curator retires both. The defence is that `UNVERIFIED` means *nothing has ever
settled it* in either case, and an assumption nothing can settle is dead weight
whichever way it got there — but it is a real limit and assumption 3 is written
to fire on it.

The idle clock is read off the audit trail's first event rather than off the
record, because `Repository.revise` overwrites `created_at`. That is one extra
read per assumption, and without it any unrelated write would reset the window.

**Reversal cost.** Small and bounded. The merge rules are two functions and the
retirement rule is one; `IDLE_DAYS` is a constant reported beside every
retirement so a reader who disagrees can re-derive without re-running anything.
Undoing an individual curation is what the append-only store is for: a
retraction is a version, and the version before it is still there. What is not
cheap to reverse is a curation pass run against a corpus with assumption 2
false — the merges would be individually undoable and collectively a mess, which
is why the curator proposes and a separate pass writes.
