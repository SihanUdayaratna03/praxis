---
id: ADR-0003
status: accepted
date: 2026-08-09
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0003 — SQLite with FTS5 as the embedded graph store

## Chosen

One SQLite file holds everything: entity tables (`Document`, `Span`,
`Decision`, `Assumption`, `Estimate`, `Outcome`, `Finding`, `AuditEvent`), a
single typed edge table for the graph, and FTS5 indexes for text search. No
server, no container, no connection string.

## Rejected

| Option | Why not |
| ------ | ------- |
| Neo4j or another native graph database | The graph is small — thousands of nodes, not millions — and the queries are shallow: ancestors of a decision, decisions reachable from an estimate. A recursive CTE handles both. The real cost is that a judge or a new contributor would have to stand up a server before seeing anything run, which contradicts the offline-first stance in ADR 0005. |
| PostgreSQL | Same objection, plus a migration story for a single-writer workload that has one writer. |
| A graph library in memory, persisted to JSON | Loses transactions, loses the audit trail's durability guarantees, and loses full-text search. The append-only audit requirement effectively demands a real transactional store. |
| DuckDB | Better at analytics, worse at the transactional append-only writes that dominate here, and its recursive-CTE and FTS story is weaker for this shape of work. |

## What was known at the time

The corpus target for Phase 10 is ≥60 documents with injected ground truth,
producing an estimated low thousands of nodes and edges. Access is
single-process and single-writer. Python ships SQLite in the standard library
with FTS5 compiled in on all target platforms. The dashboard in Phase 11 is
read-mostly.

Not known: how many edges the fusion layer generates per decision, and whether
`CollateralAgent`'s graph walk in Phase 8 stays shallow enough for a recursive
CTE to be the right tool.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The graph stays small enough for recursive CTEs to be fast | `edge_count < 1_000_000` | `when(edge_count > 500_000)` |
| 2 | Provenance walks stay shallow | `max_traversal_depth <= 8` | `on_event("CollateralAgent is implemented")` |
| 3 | Single-writer access is sufficient; the dashboard never needs concurrent writes | `concurrent_writers == 1` | `on_event("Phase 11 dashboard gains write endpoints")` |
| 4 | FTS5 is available in the bundled SQLite on every target platform | `fts5_available == true` | `on_event("a new target platform is added")` |
| 5 | A single-file store on a OneDrive-synced path does not corrupt under sync | `db_corruption_events == 0` | `when(phases_completed >= 3)` |

## Consequences

**Accepted costs.** Deep or wide graph traversals will be more awkward to
express than in Cypher, and there is no built-in graph algorithm library.
Assumption 5 is a genuine risk specific to this machine — the working copy sits
inside a OneDrive folder, and file-sync tools have a poor record around SQLite
WAL files. The database is gitignored and reproducible from the corpus, so the
blast radius is a rebuild rather than data loss, but the assumption is recorded
so it can fire rather than be discovered.

**Reversal cost.** Moderate and bounded by design: all storage access goes
through a repository layer built in Phase 1, so swapping the backend is an
implementation change behind a stable interface rather than a rewrite of the
agents.
