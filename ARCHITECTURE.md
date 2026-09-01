# Architecture

> Status: this describes the system Praxis is being built toward. Phase 0
> shipped the foundation — config, logging, CLI, CI, hooks — Phase 1 the data
> model and the store, Phase 2 the model access layer, Phase 3 ingestion: the
> first real agents, Phase 4 Half A's extraction and the eval harness, and
> Phase 5 the memory that extraction feeds: a predicate language, the monitor
> that evaluates it, contradiction detection and the archaeologist, and Phase 6
> Half B: the estimates a corpus states, what kind of work each covers, and
> what actually happened to them, and Phase 7 the calibration maths those
> numbers turn into a verdict: a bias detective that refuses below `n = 5`, a
> calibrator that mostly passes an estimate through and says why, and a
> backtest that walks a history forward, and Phase 8 **the fusion layer**: the
> two halves arguing with each other, which is the claim the whole project
> exists to make. See [`docs/FUSION.md`](docs/FUSION.md), and Phase 9
> **adversarial and governance**: the first layer that adds no extraction
> surface and only judgement about what the system has already produced --
> a challenger that argues against a finding before a person sees it, a
> curator that collapses duplicated beliefs and withdraws abandoned ones,
> and a gate that refuses to conclude when the evidence is thin. Sections
> marked
> *(built)* exist and are tested; everything else is the target, not the
> present. Each phase updates
> this file when something structural lands.

## The shape of the thing

```
                    ┌──────────────────────────────────────────┐
   sources ────────▶│ SourceAdapter → SegmenterAgent  (built)  │  ingestion
   (md, txt, json)  │   Document → Span (stable ids, offsets)  │
                    └────────────────────┬─────────────────────┘
                                         │
              ┌──────────────────────────┴───────────────────────────┐
              ▼                                                      ▼
   ┌──────────────────────┐                          ┌───────────────────────────┐
   │ HALF A — provenance  │                          │ HALF B — calibration      │
   │                      │                          │                           │
   │ DecisionScout        │                          │ EstimateExtractor         │
   │ DecisionStructurer   │                          │ WorkClassifier            │
   │ AssumptionExtractor  │                          │ OutcomeMatcher            │
   │ AssumptionFormalizer │                          │ BiasDetective   (no LLM)  │
   │ AssumptionMonitor    │                          │ CalibratorAgent           │
   │ ContradictionDetector│                          │ ScoringAgent    (no LLM)  │
   │ ArchaeologistAgent   │                          │                           │
   └──────────┬───────────┘                          └─────────────┬─────────────┘
              │                                                    │
              │         ┌────────────────────────────────┐         │
              └────────▶│ FUSION                         │◀────────┘
                        │  FusionBridge       (no LLM)   │
                        │  CollateralAgent    (no LLM)   │
                        │  ReviewTriageAgent  (phase 9)  │
                        └───────────────┬────────────────┘
                                        ▼
                        ┌────────────────────────────────┐
                        │ GOVERNANCE                     │
                        │  VerifierAgent      (no LLM)   │
                        │  ChallengerAgent    (built)    │
                        │  CuratorAgent       (no LLM)   │
                        │  AbstentionGate     (no LLM)   │
                        │  ReporterAgent                 │
                        └────────────────────────────────┘
```

Nothing enters the store without passing `VerifierAgent`, and no high-severity
finding reaches a human without surviving `ChallengerAgent`.

## The fusion mechanism

This is the part that justifies the project, so it is worth stating precisely.

1. `AssumptionFormalizer` compiles an assumption into a predicate:
   `migration_weeks <= 6`.
2. Something recognises that the predicate's subject is a **quantified
   forward-looking claim** — that is, an estimate wearing an assumption's
   clothes — and writes an `estimated_as` edge between the `Assumption` and the
   `Estimate`. **That something is `AssumptionExtractor`, in Phase 4**, inside a
   `reason`-tier call already holding the assumption and its quantity — not
   `FusionBridge`, as this step used to say. It is the only judgement in the
   whole mechanism and it is paid for once. See
   [ADR 0016](docs/adr/0016-the-extractor-writes-the-first-estimated-as-edge.md).
3. `FusionBridge` walks the edges that already exist and resolves each linked
   estimate's `(owner, work_class)`, which is a second source of evidence beyond
   current facts: the estimator's calibration history for that *work class*.
4. `BiasDetective` answers with a factor, an `n`, an interval and a confidence
   — and refuses to answer at all when `n < 5`, with no override. Below the
   threshold the factor is not computed rather than computed and withheld. The
   interval is multiplicative and one log sigma wide, because a scattered
   estimator gets a wider band rather than a second refusal — ADR 0024.
5. If the calibrated value violates a predicate the **raw** estimate satisfied
   — a flip, not merely a correction — a `StaleDecision` finding is filed
   against the `Assumption`, naming every `Decision` linked by an `assumes`
   edge. *(Phase 8, built)*

**Not an `AssumptionBreach`, and the difference is load-bearing.** A breach is a
predicate evaluated false against facts a monitoring run was *given*: something
happened. A fusion flip is a predicate evaluated false against a number nobody
has observed yet, projected from the estimator's track record: nothing has
happened, and the work may still land on time. The two are different
`FindingKind`s so a triage queue cannot rank a projection above a measurement,
and the prosecution opens by saying so. This supersedes the earlier wording of
this step — see [ADR 0028](docs/adr/0028-a-projection-is-not-a-breach.md).

And in reverse: when an `Outcome` misses its `Estimate` badly, `CollateralAgent`
walks the impact DAG to find every decision that leaned on it and files a
`CollateralImpact` against the estimate. That direction *is* a measurement, and
the walk is `Repository.impacted_by`, which Phase 1 built and called the fusion
query — see [ADR 0027](docs/adr/0027-collateral-damage-is-a-walk-phase-1-already-wrote.md).
*(Phase 8, built)*

Neither half can do this alone. Provenance without calibration cannot tell a
stale assumption from a live one that was always optimistic. Calibration
without provenance can tell you a team is 1.8x optimistic on migrations and
cannot tell you which decisions that fact invalidates.

## Data model *(Phase 1, built)*

Versioned, append-only, fully audited. Nothing is updated in place; a change is
a new version plus an `AuditEvent`.

| Record | Holds |
| ------ | ----- |
| `Document` | A normalised source with a content hash |
| `Span` | An addressable range with exact source offsets and a stable id |
| `Decision` | Chosen option, rejected options, maker, date, scope |
| `Assumption` | Plain statement, compiled predicate, expiry condition, status |
| `Estimate` | Quantity, unit, owner, work class, stated confidence, conditions |
| `Outcome` | Actual value, unit, match quality, or explicitly `unresolved` |
| `Link` | A typed edge (see below) |
| `Finding` | Prosecution, challenge, verdict, severity, confidence |
| `AuditEvent` | Who or what wrote what, when, and why |

Edge types: `assumes`, `justified_by`, `contradicts`, `supersedes`,
`estimated_as`, `collateral_of`.

Every extracted claim carries a `span_id`. `VerifierAgent` re-reads that span
and rejects the claim if the span does not actually contain it — this is the
hallucinated-citation gate, and it is deterministic code, not a model.

Ids are typed per kind, and there are two schemes rather than one. Most records
get a sequential, human-readable id (`D-0042`) allocated by the store. `Span`
and `Link` derive theirs from their own coordinates, because for those two the
coordinates *are* the identity: two agents citing the same byte range, or
asserting the same edge, arrive at the same id without coordinating. That makes
citation de-duplication and edge idempotence structural, which is why re-running
an agent cannot fork the graph. See
[ADR 0008](docs/adr/0008-typed-ids-and-append-only-versioning.md).

## Storage *(Phase 1, built)*

One SQLite file. Entity tables, one typed edge table, FTS5 for text search,
recursive CTEs for graph walks. No server. See
[ADR 0003](docs/adr/0003-sqlite-as-the-graph-store.md).

All access goes through a repository layer, so the backend is replaceable
without touching an agent. Three things hold that boundary up:

- **Append-only is enforced by the schema, not by the repository.** Every table
  carries `BEFORE UPDATE` and `BEFORE DELETE` triggers that `RAISE(ABORT)`, so
  invariant 7 is true of anything holding a connection — including a person with
  the `sqlite3` shell and a good reason.
- **The audit row is written in the same transaction as its change.** One
  cannot be lost without the other, so "every mutation produced exactly one
  `AuditEvent`" is true by construction rather than by discipline.
- **No `sqlite3` import leaves the package**, in the failure path as well as the
  query path: driver errors are translated into `praxis.store.errors` by result
  code, never by matching message text.

Migrations are numbered SQL files with a checksummed ledger and no framework —
[ADR 0009](docs/adr/0009-forward-only-migrations-without-a-framework.md). The
schema is at **version 4**: core, full-text search, traces, and the columns
naming the prompt behind each trace. The
store lives in
the platform data directory rather than the repository, because the repository
is on a synced filesystem and WAL sidecars corrupt under one —
[ADR 0010](docs/adr/0010-store-location-under-a-syncing-filesystem.md).

## Ingestion *(Phase 3, built)*

```
   bytes ──▶ SourceAdapter ──▶ block grid ──▶ SegmenterAgent ──▶ VerifierAgent ──▶ store
             deterministic      deterministic   picks block        deterministic
             md / txt / json    the only place  numbers, never     re-reads every
                                a cut may fall  an offset          span
```

Segmentation is two decisions and only one of them is a model's.
`praxis/ingest/blocks.py` cuts a document deterministically into headings,
paragraphs, list items, whole tables and whole fenced code, and
`SegmenterAgent` is shown that grid numbered and asked which contiguous runs
belong together. **It answers in block numbers.** The offsets are read off the
grid and the text is sliced out of the document, so a citation of text that is
not there is not something this pipeline can express — see
[ADR 0011](docs/adr/0011-semantic-segmentation-over-a-deterministic-block-grid.md).

The grid is also the floor. A group that cannot be honoured exactly is dropped
with a reason, blocks no group claimed become spans of their own, and a window
the model refused or never answered usably degrades to one span per block. The
worst answer available therefore costs segmentation quality and never
correctness, and the spans always partition the document's blocks.

`VerifierAgent` re-reads everything anyway, because "impossible by
construction" and "checked regardless" is the only pairing worth making a
promise out of. It resolves each span's document rather than being handed one,
so a citation of a document nothing ever ingested is refused as such — the most
complete way a citation can be fabricated.

Normalisation is three transformations and no more: drop the byte-order mark,
collapse CRLF and a bare CR to LF, normalise to NFC. Each closes a way the same
document could produce two byte lengths on two machines, and therefore two sets
of content-addressed span ids. A JSON source is re-rendered one `path: value`
block per leaf, so **its spans address the rendering rather than the file** —
prose left inside a JSON string is prose behind escape sequences.

`praxis/llm/structured.py` arrived with the first agent that needed it, as
Phase 2 said it would: a Pydantic model is reduced to the dialect the API
accepts — strip the unsupported keyword, say it in the description instead,
close every object, and validate locally against the original — and a malformed
answer is repaired by continuing the conversation, one trace row per attempt.

## The synthetic corpus *(Phase 3, built)*

Phase 10 grades extractions automatically, which needs a corpus whose answers
are written down in a shape a program can compare against.
`praxis/corpus/` writes one, and emits the answer key **by construction**:
each document is assembled from fragments and the byte range of every fragment
is recorded as it is appended, never found by searching the finished text.

The key carries byte offsets into the *normalised content* (the same
coordinate system a `Span` uses, so `content_sha256` is exactly
`Document.content_hash`), stable item ids, the typed edges between them, per
field an expected value and a comparison mode, and `is_distractor` for text
that looks extractable and must not be extracted — without labelled negatives
only recall is measurable. See
[ADR 0012](docs/adr/0012-machine-gradeable-corpus-ground-truth.md).

Every generated ADR carries two assumptions: one about the world, and one about
how long the work will take — a quantified forward-looking claim, which is an
estimate wearing an assumption's clothes. The second carries an `estimated_as`
edge, so the corpus contains the fusion relationship the product exists to find,
labelled, before the agent that has to find it is written.

## Half A extraction *(Phase 4, built)*

Three agents in a fixed sequence, each cheaper than the one it feeds:

```
  spans ──▶ DecisionScout ──▶ DecisionStructurer ──▶ AssumptionExtractor
            scan tier             extract tier            reason tier
            one call per          one call per            one call per
            window                candidate               verified decision
```

`praxis/agents/extraction.py` runs them over a store and writes in dependency
order. A document that already holds decisions is recognised rather than
redone, and a refused decision is never paid for twice — the extractor runs
once per *verified* decision.

**Every extraction cites a span by its ordinal in a numbered offering**, never
by id and never by byte range, so a fabricated coordinate is not a thing an
agent can express. `praxis/agents/offering.py` builds the listing;
`praxis/agents/citation.py` is the single gate every claim's citation passes,
and the order of its checks is the substance — an unoffered ordinal beats
everything downstream, and a span that will not resolve beats a bad quotation.
See [ADR 0015](docs/adr/0015-extraction-cites-spans-by-offered-ordinal.md).

The extractor writes an `Estimate` and the first `estimated_as` edge wherever
an assumption turns out to be a quantified forward-looking claim — the cheap
half of the fusion recognition, in a call already holding the assumption and
its quantity. The cross-document half is still `FusionBridge`'s. See
[ADR 0016](docs/adr/0016-the-extractor-writes-the-first-estimated-as-edge.md).

Prompts are versioned stored artefacts read through `importlib.resources`, and
`llm_trace` records the `prompt_id` and digest behind every call, so a metrics
table can name the bytes that produced it. See
[ADR 0014](docs/adr/0014-prompts-as-versioned-stored-artefacts.md).

## Half A memory *(Phase 5, built)*

Extraction produces records; this is what makes them a memory rather than a
filing cabinet. Four components, and the boundary between what is arithmetic
and what is a judgement is the whole design.

```
  assumptions ──▶ AssumptionFormalizer ──▶ AssumptionMonitor ──▶ finding
                  prose -> predicate       predicate + facts
                  reason tier              arithmetic only
                        │
                        ▼
                  ContradictionDetector ──▶ contradicts edges
                  blocking -> intervals -> a model, in that order
```

`praxis/predicates/` is a small **total** language: comparisons over named
quantities, `and`/`or`/`not`, and a closed set of call forms, evaluated to
true, false or unknown-with-a-reason. Nothing raises at evaluation time, and
`render(parse(s))` round-trips — which is what lets a stored predicate be
normalised so two spellings of one claim land in one place. Expiry conditions
are a second, smaller grammar, because "is this still true" and "is it time to
look again" are different questions. See
[ADR 0017](docs/adr/0017-a-small-total-predicate-language.md).

`AssumptionFormalizer` compiles an assumption's prose into that grammar and
**never drops what will not compile** — the best attempt is stored, marked, with
its confidence capped, because the monitor refuses to breach on a predicate it
cannot read and a person can fix what they can see.

`AssumptionMonitor` evaluates every predicate against what is measured and
writes **only what changed**. Two properties hold together: no model can produce
a breach — `BREACHED` is reached only by arithmetic on facts, and the single
model call in a pass can only ever move an assumption to `EXPIRED` — and a
second pass over an unchanged world writes nothing at all. The eval table
reports `aged_misreported_as_breached` by name, and it is zero by construction.
See [ADR 0019](docs/adr/0019-only-arithmetic-can-breach-and-writes-happen-on-change.md).

`ContradictionDetector` runs three stages and only the last is a model:
deterministic blocking proposes the pairs worth comparing, interval arithmetic
settles every pair whose conflict is a fact rather than a judgement, and only
the residue is batched to the `reason` tier. `Settlement` records which stage
decided, so a low recall can be traced to the stage that lost the pair. See
[ADR 0018](docs/adr/0018-blocking-then-arithmetic-then-a-model.md).

`ArchaeologistAgent` answers "why not X" from the record. **The model selects;
the store speaks** — it is asked only which decision and which rejected option,
by reference into a listing, and the answer is assembled from stored fields. An
option the decision never recorded cannot be named, and a question the record
does not cover is refused. See
[ADR 0020](docs/adr/0020-the-archaeologist-retrieves-and-never-generates.md).

`praxis formalize`, `praxis monitor`, `praxis contradictions` and `praxis why`
are the four commands, in the order they are meant to be run.

## Half B calibration *(Phase 6, built)*

The other pillar Phase 8's fusion stands on. Half A asks what was decided and
what it rested on; this asks what was predicted and what actually happened.

```
  spans ──▶ EstimateExtractor ──▶ WorkClassifier ──▶ OutcomeMatcher ──▶ Outcome
            scan tier              scan tier          extract tier
            one call per           one call per       blocking, then one
            window                 unclassified       call, then arithmetic
                                   estimate
```

`AssumptionExtractor` already wrote an estimate wherever an assumption turned
out to be a quantified claim (ADR 0016), but only ever *inside* an assumption it
was paid to read. Most estimates in a corpus are nowhere near one: they are in a
status update, a plan, a ticket, and nobody called them estimates when they
wrote them. `EstimateExtractor` runs over spans for exactly those, on the scan
tier, one call per window — `DecisionScout`'s economics for `DecisionScout`'s
reason. It never guesses a unit, never invents an owner, and never sets
`work_class`.

`WorkClassifier` owns `work_class`, and it **revises** rather than writes: the
field is the axis `BiasDetective` groups by, so it is a key rather than a label,
and the failure worth preventing is not a wrong class but *two spellings of
one*. `data-migration` and `database-migration` turn one estimator's ten
migrations into two sets of five under a detective that refuses below `n = 5`,
and nothing raises. So the store's existing vocabulary is offered to the model,
whether a class was newly proposed is computed against the store rather than
taken from the answer, and spelling is repaired while meaning is not. It also
repairs the `unclassified` rows Phase 4 left behind. See
[ADR 0023](docs/adr/0023-work-class-is-assigned-by-revision.md).

`OutcomeMatcher` runs three stages and only the middle one is a model:
deterministic selection proposes the passages worth reading, the model says
which reports the actual for *this* estimate, and arithmetic does everything
after. **`match_quality` is computed, never asked for** — the banding, the unit
conversion and the candidate selection live in `praxis/agents/reconciliation.py`,
which imports no provider and therefore cannot reach one. Invariant 3 made
structural rather than remembered. Units are reconciled at write time against a
stated convention, written into the outcome's notes; across incommensurable
families the pairing is refused rather than converted at an invented rate. See
[ADR 0021](docs/adr/0021-match-quality-is-arithmetic-not-a-judgement.md).

**An estimate nothing resolves gets an `unresolved` `Outcome`, not a silence.**
Phase 1 designed the record for it — an unresolved outcome exists so estimates
that never resolved stay visible instead of dropping out of the sample, which is
how a curve ends up flattering its estimator. Every estimate leaves the matcher
with exactly one row, and the six causes of an unmatched pairing are reported
apart, because a low match rate means six different things and only three are
about the model. See
[ADR 0022](docs/adr/0022-an-unmatched-estimate-is-an-unresolved-outcome.md).

`praxis/agents/estimation.py` runs the three over a store, and `praxis estimates`
is the command. A second pass costs nothing on the classify and match stages;
extraction re-reads a document that produced no estimate, which is the same
limitation `praxis extract` has and is in `BACKLOG.md` with its reason.

**This is where the fusion chain first runs end to end.** `measured_in` binds an
identifier only where an assumption's `estimated_as` edge reaches an estimate
that an `Outcome` resolves, so the mechanism Phase 5 shipped had nothing to
reach through. It does now: a matched outcome binds the quantity a predicate
names, and `AssumptionMonitor` breaches the assumption on it by arithmetic — a
missed estimate reaching forward to invalidate the decision that leaned on it.
An `unresolved` outcome binds nothing and breaches nothing, which is what stops
the chain firing without a measurement.

**Phase 7's query was written here and corrected there.**
`calibration_history` in `praxis/store/reports.py` answers "for this person and
this work class, what is the distribution of estimated against actual" as a
single indexed join, and it was written against Half B's output while that
output could still change — the discipline Phase 1 applied to the `Link` table
for Phase 8. Four properties keep it to one join: the pairing is a column rather
than an edge, an unmatched estimate is a row rather than an absence, units are
reconciled before the write, and `work_class` sits on the estimate where
`estimate_owner_work_class` already indexes it.

Writing it early is also what caught the thing writing it early is for. Phase 6
applied both narrowings in Python after `fetchall`, so every answer was right
and the index was never used — a full scan of both tables per question, in the
one read `BiasDetective` runs per group and `FusionBridge` will run per
assumption. Phase 7 pushed them into the statement and made the *query plan* the
thing asserted on, with the unnarrowed case as the control.

## Calibration maths *(Phase 7, built)*

Where Half B's numbers become a verdict. Three components, and **none of them
calls a model** — the first phase in this project where that is true of every
component. There is no text to interpret here: the input is `Estimate` and
`Outcome` rows the store already holds, and everything out is arithmetic over
them, property-tested with `hypothesis` the way Phase 1 tested the store's
invariants.

```
  calibration_history ──▶ BiasDetective ──▶ CalibratorAgent ──▶ a calibrated estimate
   (one indexed join)      groups, refuses     applies or         with n, the band and
                           or measures         passes through     the confidence cited
                                │
                                └──────────▶ ScoringAgent ──▶ would it have helped?
                                             walked forward       (prequential)
```

**`BiasDetective` is mostly a refusal, and that is the product.** A group is one
`(owner, work_class)` pair, and below **five** resolved estimates with a usable
ratio it says nothing — no override, no keyword argument, no default to lower.
Below the threshold the factor is **not computed**, not computed and withheld,
so there is no number on the returned object for a later change to start
printing. Run against this repository's own history it declines on every class:
`agent-implementation` at four, three others at one.

**A ratio is multiplicative, so everything is computed in log space.** The
centre is a geometric mean, the spread a standard deviation of logarithms, and
the band one you divide and multiply by. An estimator twice over on one job and
twice under on the next is on average exactly right, and an arithmetic mean of
`0.5` and `2.0` would report a 25% bias that does not exist. It is `Decimal`
throughout — `ln`, `exp` and `sqrt` inside a pinned context — which is what makes
"re-running over an unchanged store gives an identical answer" a testable
property rather than a hope.

**Dispersion widens the band; only `n` refuses.** Sample size is a fact about
how much evidence exists, and too little means silence. Scatter is a fact about
the *estimator*, and an erratic estimator is precisely who needs telling — so a
wide sample gets a wide interval and a low confidence, never a second refusal on
a second magic number. The factor cannot travel without its band, because they
are one frozen object. See
[ADR 0024](docs/adr/0024-dispersion-widens-the-band-and-only-n-refuses.md).

**An unclassified estimate is in no group.** `work_class` is a grouping key, so
`unclassified` is the absence of one; pooling those rows would compute a factor
across migrations, refactors and incident response and call it a person's bias.
They are reported as their own row — "fourteen estimates in no class" is what
somebody can fix this afternoon — and never summarised.

**`CalibratorAgent`'s pass-through is the primary path, not the fallback.** Six
of this project's own seven estimates would take it, and in any real corpus most
groups sit below the threshold most of the time. It comes back fully populated,
carrying the detective's verdict and a sentence saying in as many words that an
unchanged estimate is the correct answer rather than a stage that failed. Its
explanation is a **template over numbers it was handed** — every value exists
before the sentence does — which is why it moved out of ADR 0006's routing table
and into `NON_LLM_AGENTS`. See
[ADR 0025](docs/adr/0025-the-calibrator-explains-rather-than-generates.md).

**`ScoringAgent`'s backtest is prequential.** Each row is scored using only the
rows before it. A factor fitted over a whole history and applied to a row inside
it has already seen the answer it is being graded on, and would report an
improvement rate meaning only that a mean sits close to the points it came from.
`graded` travels beside `score`, because a backtest that scored nothing and one
that scored badly both print `0.0` and only one is a grade.

**Calibration is a store pass, not an ingestion stage.** It operates over
accumulated history rather than over one document, so there is nothing
per-document to iterate — a store that has ingested nothing since the last run
can still answer differently, because an outcome may have landed. It writes only
on a change: a second pass over an unchanged store produces no version and no
audit row.

**A calibration finding is filed against an anchor, not a subject.**
`Finding.subject_id` is one graph node and a factor belongs to a *group*, which
has none — so the finding is filed against the group's **earliest** estimate,
stable as the group grows, with the prosecution naming the group in its first
clause. No migration was needed; `FindingKind.CALIBRATION_BIAS` and an empty
`evidence_span_ids` were designed for this in Phase 1. What `Finding` cannot
hold is the *query*: `prosecution` is prose, so `FusionBridge` recomputes
through `BiasDetective` instead — which also means a factor can never go stale,
and a stale calibration factor is exactly the failure this product exists to
catch.

**Phase 8's fusion query was executed here, not sketched.** `factor_for(owner=,
work_class=)` is one indexed read plus `O(n)` arithmetic, returns a populated
object in every case so an absence is never an exception, and a test renders the
sentence this document promises — *"migration work is 1.8x under, n=6,
confidence 0.5129"* — straight off its fields. Sketching it is also what found
the Phase 6 read narrowing its two arguments in Python after `fetchall`, which
made every per-group question a full scan of both tables.

## Adversarial and governance *(Phase 9, built)*

The first layer that adds no extraction surface. Everything above it produces
records; this decides what the records are worth, and it is the only place in
the system whose headline output is a count of things it refused to say.

```
  findings ──▶ ChallengerAgent ──▶ CuratorAgent ──▶ AbstentionGate
               one reason call     no model call    no model call
               per batch           writes edges     writes nothing
                                   and retractions
```

**`ChallengerAgent` argues against a finding and records what survived.** Onto
`Finding.challenge` and `Finding.verdict`, which Phase 1 built for it -- so the
phase costs no storage. It is the **first component in seven to make a model
call**, and ADR 0027 called that in advance: its input is a `prosecution` in
prose that nobody reduced to a record, and no arithmetic decides whether a case
is sound. ADR 0019 is untouched, because a verdict is not a status: only
arithmetic can still reach `BREACHED`. See
[ADR 0030](docs/adr/0030-the-challenger-keeps-its-model-route.md).

An unconfident verdict is recorded as **no verdict**, and an empty rebuttal
forfeits the verdict with it -- `Finding`'s own validator refuses a verdict with
no challenge behind it, and this refuses to build one.

**`CuratorAgent` collapses and withdraws, and deletes nothing.** Merging is a
`LinkType.SUPERSEDES` edge pointing newer to older; retiring is
`Repository.retract`, a new version carrying a flag plus an
`AuditAction.RETRACTED` row. Both mechanisms are Phase 1's and both were
documented as doing exactly this, so there is no migration and
`AssumptionStatus` gains no member. See
[ADR 0031](docs/adr/0031-curation-is-supersedes-and-retraction.md).

*Never fires* is defined precisely and read off Phase 5's audit trail with no
parallel bookkeeping: status still `UNVERIFIED`, no event by
`AssumptionMonitor`, and `IDLE_DAYS` since the **first** version -- read from
the trail rather than the record, because `revise` overwrites `created_at` and
any unrelated write would otherwise reset the clock.

**The refusal that matters most:** an idle assumption a live `Decision` assumes
is *not* retired. It is an unverifiable belief under a live decision, which is a
finding for a person rather than dead weight for a curator.

**`AbstentionGate` refuses to conclude, and the refusal is the output.** Four
arithmetic checks -- never challenged, below the confidence floor, a quoting
finding kind citing no span, a withdrawn subject -- and **all four run**, so a
person is told every defect rather than sent back once per fix. `NEEDS_HUMAN`
is a feature and the number is reported plainly, the stance this project has
taken on every refusal since `BiasDetective` declined to speak below `n = 5`.

**Nothing about the routing is stored.** A disposition is recomputed every time
it is asked for, exactly as `praxis.agents.calibration` argues a calibration
factor must be: a stored disposition is stale the moment a challenge lands or a
subject is withdrawn, and a stale refusal reads as a decision somebody made.
That is also what keeps the whole phase migration-free. See
[ADR 0032](docs/adr/0032-abstention-is-arithmetic-and-is-never-stored.md).

**The concede rate cannot be earned offline, and the code says so rather than
working around it.** `MockProvider` answers a bare boolean true seven times in
ten, so an offline rate measures schema synthesis. `GovernanceScore` carries
`mock_provider` *inside* the score and `praxis govern` prints the caveat on the
same line as the number, so neither can be reported bare. No response schema was
reshaped to move it: `Judgement.upheld` mirrors `Verdict`'s own polarity.

## Evaluation *(Phase 4, built)*

`praxis/eval/` grades a run against `praxis/corpus/`'s answer key, in four
modules that fail in four different ways: `matching` decides *which* record
answers *which* item, by byte overlap and a total tie-break; `metrics` counts
the pairs and is arithmetic and nothing else; `harness` runs a corpus end to
end; `report` renders it and does no arithmetic, so a formatting change can
never move a number.

The claims are read back **out of SQLite**, not taken from the run's result:
the run is a report of what the agents produced, the store is what survived
being written.

Reported per `ItemKind`: precision, recall, F1, field accuracy and exact
matches, with distractor hits counted apart from ordinary false positives —
without labelled negatives only recall is measurable. Reported once for the
run: citation integrity and how it failed, and the recall over the
`estimated_as` edges the corpus labels, which is the only number that is a
claim about the thesis rather than about extraction.

Phase 7 added a sixth module and a fourth quarter. `calibration` grades what
the store's calibration says about itself, and it is the only part of the table
with **no answer key** — a calibration factor is not something a document can
state, so there is nothing to compare against and a ground truth would have to
be computed by the code being graded. What it checks instead is internal
consistency: that the refusal threshold held in both directions, that the
pass-through fired on exactly the groups that refused, and that the backtest is
reported with its denominator. Every number in it is a zero against a corpus
this size, and the section leads with the sentence saying why.

Phase 5 added a fifth module and a second half to the table. `memory` grades
what the store *remembers*: how much of what was extracted compiled into a
checkable predicate, what the monitor concluded against the verdicts the answer
key computes, and the `contradicts` edges against the pairs the corpus planted —
split by which stage settled them, under a blocking recall that is the ceiling
the others sit under. Records join to the key through the assumption pairing,
because the two sides allocate ids independently and nothing relates them but
the passage both point at.

Phase 6 added a sixth module and a third half to the table. `estimation` grades
what the store learned about its own *estimates*: whether they sit on the axis
calibration groups by and whether they sit on the right part of it, and which of
them an actual answered. Two of those numbers are printed as a pair with a note
ordering them, because the higher one is not the better one — an agent that
classifies everything wrongly scores 1.0 on the classified rate and 0.0 on class
accuracy, while one that classifies nothing scores 0.0 on both and that is the
safer failure. The match rate is printed with its ceiling, which the corpus fixes
below 1 by construction, and the unmatched rows are split by the stage that lost
them. `ItemKind.OUTCOME` joined `GRADED_KINDS` here, which is what Phase 4's
version of that line said would happen once something could write one.

`praxis/eval/adrs.py` is the one metric that grades the *project* rather than a
run: it recomputes ADR 0001's own first assumption from the files in
`docs/adr/` each time, so widening the grammar to make it hold would be visible
as a grammar change rather than invisible as a passing test.

Phase 10 added the **ablation ladder**. `stages` is the six booleans a graded
run is configured by, `ablation` runs one rung per component — the paragraph
floor with extraction alone, then each stage above it, up to the ordinary run —
and `ablation_report` renders the table. Every rung runs the same corpus in a
store of its own, so a difference between two adjacent rows is that component
and nothing else. See
[ADR 0035](docs/adr/0035-the-ablation-ladder-is-cumulative.md).

Three of the rungs write findings rather than claims, so extraction precision is
blind to them and the table carries a findings column for that reason. The
citation gate is **not** a rung: invariant 6 has no off switch, so its
contribution is counterfactual, from the refusal record (ADR 0034).

`praxis eval <corpus>` is the whole of it in one command, into a scratch store
rather than the configured one. `--ablate` runs the ladder instead.

## Orchestration *(Phase 5+)*

A small async state machine over a typed message bus, written for this project.
No agent framework. Determinism is the requirement that drives the design: two
runs over one corpus with one seed must produce identical numbers, or the
ablation table means nothing. See
[ADR 0004](docs/adr/0004-custom-async-orchestrator.md).

Phase 4 was re-scoped to Half A's agents; nothing about ADR 0004 changed except
when it is built. What Phase 4 did leave for it is `run_id`: every write
already takes one and threads it through the audit trail, and the orchestrator
is what will mint them.

## Model access *(Phase 2, built)*

```
        agent asks for a ROLE, never a model
                       │
                       ▼
        praxis/config/models.py   ← the only file with a model id
                       │
                       ▼
                 LLMProvider
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   MockProvider  AnthropicProvider  ReplayProvider
   (default,       (live)          (fixtures)
    offline)
```

Every call records agent name, model, prompt hash, token counts, latency, cost
and full input/output to the trace store — one row per *attempt*, failures
included, in a table of its own. The trace store records what an agent was
*told*; the audit trail records what it *changed*. They stay two tables. See
[ADR 0005](docs/adr/0005-offline-first-llm-provider.md) and
[ADR 0006](docs/adr/0006-model-routing-table.md).

`provider_for()` is the only code that reads `PRAXIS_LLM_PROVIDER`, so going
live is an edit to `.env` and nothing else. `MockProvider` does not return
fixed strings: it walks the response schema and builds an answer out of the
prompt, quoting sentences and span ids that really occur in it, so
`VerifierAgent` has something real to check offline — and can still reject it.
`praxis doctor` makes one such call rather than reading the setting, because a
configuration that looks credential-free and a pipeline that answers without a
key are different claims.

`praxis/llm/anthropic.py` is the only module in the repository that imports a
vendor SDK or an HTTP client, and `tests/test_boundaries.py` parses every
module to keep it that way.

## What is deterministic, and why it matters

These never call a model:

| Component | Why |
| --------- | --- |
| `SourceAdapter` | Normalisation is parsing |
| The block grid | Where a document *may* be cut is not a judgement call, and it is what makes the segmenter's answer checkable |
| `VerifierAgent` | A hallucination check that could hallucinate is not a check |
| `BiasDetective` | Bias, sample size and intervals are arithmetic |
| `ScoringAgent` | A backtest is a comparison of two log errors |
| `CalibratorAgent` | Its explanation is a template over numbers it was handed, not a generation — ADR 0025. Moved here in Phase 7 from ADR 0006's `extract` row |
| `praxis/agents/distribution.py` | The geometric mean, the log-space spread and the band. Takes a list of `Decimal` and knows nothing else |
| Predicate evaluator | A predicate whose truth depends on sampling is not a predicate |
| `praxis/agents/reconciliation.py` | `match_quality`, unit conversion and candidate selection. The module imports no provider, so invariant 3 holds by construction rather than by rule — ADR 0021 |
| `FusionBridge` | The judgement was made in Phase 4 and written down as an `estimated_as` edge; what is left is a walk, a multiplication and two predicate evaluations — ADR 0026. Moved here in Phase 8 |
| `CollateralAgent` | "An estimate missed — what rested on it?" is `Repository.impacted_by`, which Phase 1 built and called the fusion query — ADR 0027. Moved here in Phase 8 |
| `CuratorAgent` | A `CONTRADICTS` edge Phase 5 already paid a `reason` call for, two parsed predicates, two timestamps and an audit trail — ADR 0031. Moved here in Phase 9 |
| `AbstentionGate` | A confidence, a span count, two enums and a boolean. Asking a model whether one number is below another is what invariant 3 forbids — ADR 0032. Moved here in Phase 9 |

All of it is property-tested with `hypothesis`. `praxis doctor` fails if any of
these acquires a model route.

## Present layout

```
praxis/
  cli.py               typer app: version, config, doctor, init, ingest,
                       store stats, corpus generate
  cli_eval.py          praxis extract and praxis eval
  cli_monitor.py       praxis formalize, monitor, contradictions and why
  cli_estimate.py      praxis estimates: Half B over a store, in one command
  cli_calibrate.py     praxis calibrate: the store, or one new estimate
  cli_fuse.py          praxis fuse: where the two halves argue
  cli_govern.py        praxis govern: what the system refused to conclude
  cli_tables.py        what the CLI's output looks like
  config/settings.py   pydantic-settings; PRAXIS_* environment
  config/models.py     model ids, prices, roles, routing  ← the only place
  domain/ids.py        typed ids; sequential and content-addressed
  domain/records.py    the nine Pydantic records
  domain/links.py      the six edge types and their grammar
  domain/spans.py      the span/document integrity check
  store/location.py    where the database lives, and whether that is safe
  store/connection.py  pragmas, and the transaction every write sits inside
  store/schema/*.sql   numbered migrations: core, search, traces, prompts
  store/migrations.py  forward-only runner, checksummed ledger
  store/mapping.py     record ↔ row, one table driving both directions
  store/repository.py  add / revise / retract, reads. No update, no delete
  store/audit.py       audit writes, always inside the caller's transaction
  store/graph.py       edge reads and the two recursive walks
  store/reports.py     search, counting, and the calibration history
  store/errors.py      the store's exception vocabulary; where sqlite3 stops
  store/traces.py      the llm_trace table; append-only, exact decimal cost
  llm/types.py         request, response, usage, stop and outcome vocabulary
  llm/errors.py        the seam's exceptions; where a vendor SDK stops
  llm/hashing.py       the replay key: one identity per request
  llm/accounting.py    what a call cost, and the ceiling checked before it
  llm/trace.py         the trace row and the sink protocol
  llm/provider.py      the seam: route, price, time, trace, raise
  llm/synthesis.py     a plausible answer built from a schema and the prompt
  llm/mock.py          the offline default; deterministic, free, counted
  llm/replay.py        recorded fixtures, and a loud miss
  llm/anthropic.py     the only module that imports an SDK  ← the boundary
  llm/factory.py       the only code that reads PRAXIS_LLM_PROVIDER
  llm/structured.py    pydantic -> the API's dialect, and the repair loop
  ingest/adapters.py   bytes -> the exact text every offset addresses
  ingest/blocks.py     the deterministic grid a segmenter may group
  ingest/segmenter.py  the first agent: block numbers in, spans out
  ingest/verifier.py   the citation gate. No model, ever
  ingest/pipeline.py   adapter -> segmenter -> verifier -> store, traced
  ingest/errors.py     what can go wrong between a file and a span
  corpus/groundtruth.py the answer key's shape, and what makes it gradeable
  corpus/drafting.py   assembling a document while recording where it landed
  corpus/templates.py  four document shapes, four extraction problems
  corpus/topics.py     the material: eight engineering decisions
  corpus/measurements.py the world a monitoring run is graded in, derived
  corpus/generator.py  writes the corpus, then verifies it against itself
  prompts/library.py   versioned prompt files, read as packaged resources
  prompts/texts/*.md   <task>.v<n>.md; a version bump is a new file
  agents/offering.py   the numbered span listing every extraction cites through
  agents/citation.py   the one gate every extracted claim's citation passes
  agents/errors.py     the refusal vocabulary, and what each one costs
  agents/scout.py      high-recall pass: which windows hold a decision
  agents/structurer.py a candidate span -> a Decision record
  agents/extractor.py  a decision -> its assumptions, and the estimates in them
  agents/extraction.py scout -> structurer -> extractor, over a whole store
  agents/formalizer.py an assumption's prose -> a predicate that parses
  agents/formalization.py the formalizer over a store, and what it skips twice
  agents/blocking.py   which pairs are worth comparing. Deterministic
  agents/contradiction.py blocking -> intervals -> a model, in that order
  agents/detection.py  the detector over a store; edges written once
  agents/archaeologist.py why not X, answered out of the record only
  agents/results.py    what a run wrote, and everything it lost
  agents/estimator.py  spans -> the estimates a document states, cited
  agents/classifier.py which kind of work an estimate is about. Owns work_class
  agents/matcher.py    an estimate -> what actually happened to it
  agents/reconciliation.py the arithmetic half: selection, units, the band
  agents/estimation.py extract -> classify -> match, over a whole store
  agents/distribution.py a sample of ratios, summarised in log space
  agents/bias.py       a factor per group, or the reason there is none
  agents/calibrator.py a raw estimate -> a calibrated one, explained
  agents/scoring.py    would the correction have helped? Walked forward
  agents/calibration.py the three over a store; writes only on a change
  agents/fusion.py     an assumption, priced against its estimator's history
  agents/collateral.py an estimate missed -- what rested on it? Phase 1's walk
  agents/crossdoc.py   an actual in another document, one call per document
  agents/fusion_pass.py both directions over a store; writes only on a change
  agents/challenger.py the case against a finding, before a person sees it
  agents/curator.py    what the memory should stop carrying, and how
  agents/abstention.py refusing to conclude, and which evidence was missing
  agents/governance.py the three over a store; writes only on a change
  predicates/lexer.py  the tokens a predicate is made of
  predicates/parser.py recursive descent; a grammar small enough to read
  predicates/ast.py    the tree, its rendering, and the three-valued verdict
  predicates/evaluator.py total evaluation. Unknown is not false
  predicates/intervals.py what a predicate permits, for proving a conflict
  predicates/expiry.py when to look again, which is not whether it holds
  predicates/world.py  the facts and events a predicate is evaluated against
  predicates/errors.py what a malformed predicate raises, at parse time
  monitor/monitor.py   one assumption -> a verdict. No model can breach
  monitor/run.py       the monitor over a store; writes only on a change
  monitor/facts.py     measurements a person supplies, layered on the store's
  monitor/breach.py    the finding a violated predicate raises
  eval/matching.py     which record answers which item. Byte overlap, not equality
  eval/metrics.py      arithmetic over the pairs. No model, ever
  eval/harness.py      a corpus end to end, graded from what SQLite holds
  eval/memory.py       grading what the store remembers, not what it copied
  eval/estimation.py   grading what it learned about its own estimates
  eval/calibration.py  grading the threshold, the pass-through and the backtest
  eval/fusion.py       grading the fusion layer, mostly its refusals
  eval/adrs.py         the one metric that grades the project, not a run
  eval/report.py       the table and the JSON. No arithmetic
  eval/stages.py       which stages a graded run turns on
  eval/ablation.py     one graded run per rung, each adding one component
  eval/ablation_report.py
                       the ladder's table and JSON. No arithmetic
  obs/logging.py       structured JSON logging
tests/                 pytest + hypothesis
docs/adr/              decisions, in Praxis's own schema
docs/dogfood/          Praxis's predictions about its own construction
docs/reports/          one report per phase
.claude/hooks/         deterministic enforcement of the rules that matter
```
