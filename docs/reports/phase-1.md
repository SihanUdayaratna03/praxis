# Phase 1 — Data model and store

## What was built

Praxis can now hold a record, version it, audit the change, refuse to update it
in place, walk the graph it belongs to, search it and report on it. No LLM call,
no credential, no network — this phase is Pydantic, SQL and property tests.

**The nine records** (`praxis/domain/`). `Document`, `Span`, `Decision`,
`Assumption`, `Estimate`, `Outcome`, `Link`, `Finding`, `AuditEvent` as Pydantic
v2 models: frozen, `extra="forbid"`, timezone-aware datetimes, and `Decimal`
quantities with `strict=True` so a float is an error rather than a silent
conversion. Each model rejects what would otherwise become somebody's debugging
session — a span whose text does not fill its byte range, an `unresolved`
outcome carrying a number, a finding with a verdict but no challenge behind it,
an edge whose endpoint kinds the vocabulary cannot express.

**Two id schemes, chosen per kind.** Most records get a sequential,
human-readable id allocated by the store (`D-0042`). `Span` and `Link` derive
theirs from their own coordinates, because for those two the coordinates *are*
the identity: two agents citing the same byte range, or asserting the same edge,
arrive at the same id without coordinating. Citation de-duplication and edge
idempotence are structural rather than a clean-up job, which is what stops a
re-run of an agent forking the graph.

**The store** (`praxis/store/`). One SQLite file: nine entity tables, a node
registry, `record_version`, one typed edge table, a child table for a finding's
evidence spans, an FTS5 index kept in step by triggers, and three views defining
what "current" means. Access goes through a repository whose entire write
surface is `add`, `revise` and `retract`. There is no `update` and no `delete` —
invariant 7 as an API rather than as a rule people remember.

**The CLI.** `praxis init` creates and migrates the store; `praxis store stats`
reports what is in it; `praxis doctor` gained ADR 0010's sync-root check.

## The three things holding the boundary up

Each closes a hole the obvious design leaves open, and each is the reason a
later phase can trust the store without re-reading it.

1. **Append-only is enforced by the schema**, not by the repository: 24
   `BEFORE UPDATE` / `BEFORE DELETE` triggers that `RAISE(ABORT)`. The invariant
   is therefore true of anything holding a connection, including a person with
   the `sqlite3` shell and a good reason.
2. **The audit row is written inside its change's transaction.** One cannot be
   lost without the other. An audit row that can be lost independently of its
   change is worse than no audit row, because a trail with a hole in it reads
   exactly like a complete one.
3. **`sqlite3` does not leak past the package**, in the failure path as well as
   the query path. Driver errors are translated by extended result code, never
   by matching message text — messages are prose and change between versions.

## Key decisions

| ADR | Decision |
| --- | -------- |
| [0008](../adr/0008-typed-ids-and-append-only-versioning.md) | Typed ids, two id schemes, append-only versioning |
| [0009](../adr/0009-forward-only-migrations-without-a-framework.md) | Numbered SQL migrations with a checksummed ledger, no framework |
| [0010](../adr/0010-store-location-under-a-syncing-filesystem.md) | Accepted before the phase; implemented in it |

ADR 0009 also records a deliberate refinement to 0008: three shared views
(`record_head`, `current_record`, `current_link`) plus one generic repository
read, rather than the nine `<kind>_current` views 0008 called for.

## Test and quality status

| Check | Result |
| ----- | ------ |
| `pytest` | 453 passed |
| Coverage (`praxis/`) | 98.17%, gate 85% |
| `ruff check` | clean |
| `ruff format --check` | clean |
| `mypy --strict` | clean, 25 source files |
| CI on the PR | 5/5 green, Windows and Ubuntu 3.12/3.13 |
| CI on `main` after merge | green |
| Commits on the phase branch | 35 |

35 commits is well over the 8–20 guidance, and the reason is a standing
instruction rather than drift: this phase ran across four sessions that could
end at any moment, under a rule to commit and push after every component and
record what landed. Roughly a third of the commits are one-line handover entries
in `NEXT.md`. The alternative — batching work into tidy commits — would have
traded a recoverable history for a presentable one.

The tests worth naming are the ones that would not exist if the suite had been
written by translating the implementation into assertions:

- **`test_schema.py`** reads the `CHECK` constraints back out of `sqlite_master`
  and compares them to the Python enums. Those lists are written in two
  languages and nothing else would notice them drifting apart.
- **`test_audit.py`** compares the audit table against `record_version` row for
  row, not by count. A count matches just as well when the trail describes the
  wrong versions.
- **`test_graph.py`** lifts the impact query out of the comment above the
  `dependency_edge` view and runs it against the same fixture as
  `graph.impacted_by`, at three depths. The documented query is now executable
  documentation rather than a claim that was true once.
- **`test_properties.py`** states the invariants over generated records:
  round-trip identity, span offsets that still address their document
  afterwards, edges that cannot point at a node that is not there, and a version
  history no sequence of revisions and retractions can shorten.

## Metrics

| Metric | Phase 0 | Phase 1 |
| ------ | ------- | ------- |
| Tests | 122 | 453 |
| Property-based tests | 5 | 19 |
| Coverage | 97.14% | 98.17% |
| Source modules under `praxis/` | 4 | 19 |
| Migrations | 0 | 2 |
| ADRs | 7 | 10 |
| Runtime dependencies | 4 | 5 |
| Credentials required to run anything | 0 | 0 |

## Estimate versus actual

Logged as `EST-0002` **before** the first Phase 1 file was created.

| | |
| --- | --- |
| Estimated | 2.0 hours of **active engineering**, confidence 0.5, class `data-modelling` |
| Actual, active engineering | ~4.2 hours |
| Actual, wall clock | 72.7 hours (first commit → merge) |
| Actual, blocked | ~68.5 hours, credit exhaustion between sessions |
| Scored | **`miss`** |

Under-estimated by about **2.1×**. Method, stated so the number can be argued
with: active time is the sum of four working windows bounded by commit
timestamps, each extended backwards by the reading and writing that preceded its
first commit. It is an estimate of an estimate's error, and it is the honest
resolution available — nothing here measures keystrokes.

Two things this outcome is careful *not* to claim.

It does not claim 68.5 hours of blocked time in any useful sense. The phase ran
across four sessions ended by credit exhaustion, and the gaps between them are
elapsed time containing nights, not measured waiting. Recorded so the wall clock
reconciles, and `ScoringAgent` should compare `active` against `active` — which
is precisely why `Estimate` and `Outcome` carry the split at all.

It does not claim a bias direction for this estimator. `OUT-0001` over-estimated
`scaffolding` by 2.3×; `OUT-0002` under-estimates `data-modelling` by 2.1×.
Opposite directions, different work classes, `n = 1` each. That is the shape of
data a human would confidently over-read and `BiasDetective` is specified to
refuse — below `n = 5` it declines to answer, and these two points are the first
evidence that the refusal is the right behaviour rather than a cautious one.

Where the estimate actually went wrong: it priced the records and the schema,
which took about as long as predicted, and did not price the four test modules
that make them trustworthy. Those are roughly 1,700 lines and over half the
elapsed engineering. The lesson is legible enough to be worth stating —
**estimate the tests, not the feature** — and it is a claim `EST-0003` can now
be checked against.

## What went wrong

**Four tests were green on Windows and red in CI, and had been for two days.**
`test_location.py` spelled its Windows examples as `Path`, so under a POSIX
`Path` the backslashes were ordinary characters, the whole string was a single
component, and the lexical sync-root check had nothing to look at. It correctly
returned `None`; the assertions read that as a failure. They were testing the
host's path flavour rather than the rule. Now spelled `PureWindowsPath` and
`PurePosixPath`, and `sync_root_of` takes a `PurePath` to say that it never
touches a filesystem.

The general form is worth keeping: a check whose whole point is to describe
*other people's machines* must not be tested only on this one.

**Three smaller ones, each caught by a test rather than by review.**

- The append-only trigger tests initially passed on three tables because those
  tables were empty. An empty table cannot demonstrate its own triggers, so the
  fixture gained an `Outcome` and a `Finding`.
- Quantity round-trips pass under equality while losing scale, because
  `Decimal("1.10") == Decimal("1.1")`. The assertions compare `str()`.
- `audit_event` needed its own `ordinal` column. Audit rows are deliberately not
  graph nodes, so they cannot draw a counter from `node.ordinal`, and allocating
  from `MAX(id)` breaks past four digits, where `AUD-9999` sorts above
  `AUD-10000`.

## The risk that did not materialise

ADR 0003 assumption 5 (`db_corruption_events == 0`) is **intact**. No corruption
event, because ADR 0010 moved the store off the synced tree before the first
database existed — the risk was removed rather than survived, which is not the
same thing and should not be scored as one. `StoreCorruptError` exists as a
distinct exception class so that counting these events stays a matter of
catching a type rather than grepping logs.

The residual case the ADR flagged — a `%LOCALAPPDATA%` that an enterprise
known-folder policy has itself redirected into OneDrive — is what the `doctor`
check exists for. It warns and never fails: a deliberate override is legitimate,
and a tool that refuses to run is a tool that gets worked around.

## Risks for Phase 2

| Risk | Mitigation |
| ---- | ---------- |
| The estimate for Phase 2 repeating this phase's error | `EST-0003` prices the tests explicitly, and says so in its conditions. |
| `repository.py` growing past its seams as agents arrive | It is 495 lines against a ~400 guideline, already split into `graph`, `reports`, `audit` and `mapping`. The next split is by caller, not by line count. |
| The provider layer acquiring a credential path by accident | Invariant 1 and `praxis doctor`. CI has no secret, so a provider that needs one fails the pipeline rather than a review. |
| Trace records for LLM calls duplicating the store's audit trail | They are different things — one records what an agent was told, the other what it changed. Decide the boundary in Phase 2 rather than discovering it in Phase 4. |
