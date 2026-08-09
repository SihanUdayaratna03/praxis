---
id: ADR-0001
status: accepted
date: 2026-08-09
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0001 — Record architecture decisions in Praxis's own schema

## Chosen

Every non-obvious decision gets an ADR in `docs/adr/`, written in the same
schema Praxis extracts from any other corpus: chosen option, rejected options,
what was known at the time, and assumptions as checkable predicates with expiry
conditions.

## Rejected

| Option | Why not |
| ------ | ------- |
| No ADRs; decisions live in commit messages | Commit messages explain a diff. They cannot express a rejected alternative or an assumption with an expiry, which are the two fields the product is built around. |
| Standard Nygard-format ADRs, converted to Praxis records in Phase 12 | The conversion would be doing the work the system is supposed to do. A demo where the impressive step is a bespoke import script is not a demo of Praxis. |
| Praxis schema, but only from Phase 5 once the predicate DSL exists | The most interesting decisions are made in Phases 0–4, before the DSL. Deferring means the self-analysis corpus is missing exactly the period a judge would most want to see. Hand-writing predicates in the eventual syntax costs nothing now. |

## What was known at the time

Phase 12 is specified to run Praxis over its own construction history. The
predicate DSL does not exist yet and will not until Phase 5, so predicates
written now cannot be executed — only parsed later. The syntax may change; if
it does, these files are a small, fixed set to migrate, and the migration is a
Phase 5 task rather than an open-ended risk.

Not known: whether hand-written predicates will survive contact with the real
DSL unchanged, and whether the ADR corpus will be large enough by Phase 12 to
produce interesting findings rather than a thin demo.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The predicate DSL built in Phase 5 can parse predicates written by hand before it existed | `adr_predicates_parsed / adr_predicates_total >= 0.9` | `on_event("Phase 5 predicate DSL is implemented")` |
| 2 | The ADR corpus is large enough by Phase 12 to yield real findings | `adr_count >= 25` | `on_event("Phase 12 begins")` |
| 3 | Writing ADRs in this format costs under 20 minutes each and so does not get skipped under time pressure | `median_adr_authoring_minutes <= 20` | `when(phases_completed >= 6)` |

## Consequences

**Accepted costs.** ADRs take longer to write than a paragraph would, and some
predicates written now will be wrong in a way that only becomes visible in
Phase 5. Both are acceptable: the second is itself a finding the system is
designed to surface, and a wrong predicate that gets flagged is a better demo
than no predicate at all.

**Reversal cost.** Low while the corpus is small — a format change is a
mechanical edit across a handful of files. It rises with every ADR written,
which argues for settling the schema now rather than in Phase 5.
