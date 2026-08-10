---
id: ADR-0008
status: accepted
date: 2026-08-11
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0008 — Typed ids, two id strategies, and append-only versioning

## Chosen

Four things, decided together because each one only works given the others.

1. **A `NewType` per record kind.** `SpanId` and `DecisionId` are both `str` at
   runtime and different types to `mypy --strict`.
2. **Two id strategies, chosen per kind.** Seven kinds get a sequential,
   human-readable id allocated by the store (`D-0042`, `EST-0002`). `Span` and
   `Link` get an id derived by SHA-256 from their own coordinates —
   `(doc_id, start_byte, end_byte)` and `(link_type, source_id, target_id)`.
3. **A change is a new version plus an `AuditEvent`.** Entity tables are keyed
   on `(id, version)`; withdrawal is a new version carrying `retracted`, never
   a `DELETE`. The read API is built around "current version of X", and a
   `<kind>_current` view exists per table so no caller filters by version by
   hand.
4. **Append-only is enforced by SQLite triggers**, not by repository
   discipline. `BEFORE UPDATE` and `BEFORE DELETE` on every entity table
   `RAISE(ABORT)`.

## Rejected

| Option | Why not |
| ------ | ------- |
| A single `RecordId = str` everywhere | Cheapest to write and the most expensive to debug. A `SpanId` passed where a `DecisionId` belongs produces a foreign key that matches no row, and that surfaces as an empty traversal result, which reads as "the graph is sparse" rather than as a bug. `mypy --strict` catches the whole class for the price of nine `NewType` lines. |
| UUIDv4 for everything | Nothing to read. This corpus is meant to be inspected by a human and cited in a report — `D-0042` appears in prose, a UUID does not. It also gives up the property that made content addressing worth having, since two agents extracting the same span would mint two ids. |
| Sequential ids for `Span` and `Link` too | Uniform, and it makes the store responsible for de-duplication forever. Two agents citing the same byte range would create two spans that must later be recognised as one; asserting the same edge twice would create a duplicate edge that some future clean-up pass has to reconcile. Deriving the id from the coordinates makes both problems disappear rather than be managed, and it is what lets a re-run revise a record instead of forking the graph — which ADR 0004's determinism requirement needs. |
| Content addressing for every kind | A decision's identity is not its text. Editing a typo in a decision's title would mint a new id and orphan every edge pointing at it, which is precisely the provenance loss this project exists to prevent. |
| Mutable rows with a separate history table | The ordinary choice, and it makes the current state the privileged one and history a secondary artifact that can be wrong without anything failing. Praxis's demo is reading its own history; a history that can silently disagree with the present is not a corpus. |
| Enforce append-only in the repository layer only | The repository is a Python class. A future migration script, a debugging session, or a Phase 11 dashboard query holding the same connection can bypass it in one line. A trigger cannot be bypassed by anything speaking SQL to that database, which is what makes "append-only" an invariant rather than a convention. |
| Full soft-delete semantics (a `deleted_at` on the current row) | Requires updating a row in place, which is the thing being ruled out. Retraction as a new version costs one row and keeps the withdrawn version readable, so a retraction remains a statement *about* a record rather than the disappearance of one. |

## What was known at the time

Phase 0 shipped no storage, so nothing depended on an id format yet and the
cost of choosing now was zero. The existing dogfood corpus already used
`EST-0001` and `OUT-0001`, and the ADR files already used `ADR-0010`, so a
sequential prefixed scheme was in use before it was decided — deciding
otherwise would have meant rewriting the corpus Phase 12 reads.

ADR 0003 fixed the store as one SQLite file with a single typed edge table.
SQLite enforces foreign keys but cannot express a polymorphic one, so edges
needed a node registry to point at; that registry is what makes a composite
`(id, kind)` foreign key possible, and it is why an id's prefix must agree with
its declared kind.

The corpus target for Phase 10 is ≥60 documents, giving low thousands of nodes.
At that scale a 64-bit digest has a negligible collision probability, and every
version of every record fitting in one file is not a concern.

Not known: how many versions a record accumulates in practice, and therefore
whether the `MAX(version)` subquery behind each `_current` view stays fast
enough without a materialised current-version column. Also not known whether
the audit trail's write volume becomes the dominant cost of ingestion — Phase 3
is the first phase that writes at volume, so both questions resolve there.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | 64 bits of digest is enough for content-addressed ids | `span_id_collisions == 0` | `when(node_count > 100_000)` |
| 2 | Records accumulate few enough versions that `MAX(version)` views stay fast | `max_versions_per_record <= 20` | `on_event("Phase 3 ingests the full corpus")` |
| 3 | No legitimate operation needs to delete a row | `hard_delete_requests == 0` | `on_event("a GDPR-style erasure requirement appears")` |
| 4 | An id's prefix always agrees with its node kind | `kind_prefix_mismatches == 0` | `when(phases_completed >= 6)` |
| 5 | Storing every version, plus one audit row per write, keeps the store small enough to stay a single file | `db_size_mb < 500` | `on_event("the Phase 10 corpus is ingested")` |

## Consequences

**Accepted costs.** Every read pays a `MAX(version)` subquery, or goes through
a view that does. Every write costs two rows rather than one, and the FTS index
gains an entry per version rather than per record — the store is therefore
strictly larger than a mutable one, and assumption 5 is the one to watch.
Content-addressed ids are unreadable where sequential ones are not, which is a
real cost when reading a `link` table by hand and the reason `link_type`,
`source_id` and `target_id` are all stored as columns rather than left implicit
in the digest.

Sequential allocation also makes the store the only thing that can mint most
ids, so constructing a record is two steps rather than one. That is deliberate:
a caller that could invent its own ordinal could collide with the store's.

**Reversal cost.** High for (2) and (3), low for (1) and (4). The id strategy
is baked into every id already written, so changing it means rewriting the
corpus — which is exactly why it is being decided in the phase that writes the
first row rather than the phase that notices the problem. Adding a
materialised current-version column later, if assumption 2 expires, is a
migration and a repository change with no effect on any agent.
