# Phase 8 — the fusion layer

> The phase the project exists to reach. Both halves were built and verified
> independently in Phases 3–7; this is where they argue with each other.

## What shipped

| | |
| --- | --- |
| Branch | `feat/phase-8-fusion-layer`, cut from `main` at `8797d1f` |
| Commits | 13, one logical change each |
| Suite | **2777 passed**, coverage **98.9%** (gate 85%) |
| New modules | 6, every one at **100%** |
| ADRs | 0026, 0027, 0028 |
| Schema | version 4 — **no migration**, for the third phase running |

`praxis/agents/fusion.py` (`FusionBridge`), `collateral.py` (`CollateralAgent`),
`crossdoc.py` (`CrossDocumentMatcher`), `fusion_pass.py` (`fuse_store`),
`praxis/cli_fuse.py` (`praxis fuse`), `praxis/eval/fusion.py`, and
[`docs/FUSION.md`](../FUSION.md).

## The result the phase earned

Against a store holding a history that runs 1.8× long, `praxis fuse` prints:

> `A-0002 assumed migration_weeks = 4; migration work is 1.8000x under, n=5,`
> `confidence=0.5000 implies 7.2000 (band 7.2000 to 7.2000) -- flipped`

That is `ARCHITECTURE.md`'s pitch, executing. It took eight phases to be able to
print it and the sentence is now produced by code rather than promised by a
document.

## The finding that matters most: how little Phase 8 had to build

The scope as written describes a component that recognises an assumption is
secretly an estimate. **That component was built in Phase 4** and the eval
harness has graded it since. Checked against `extractor.py:508`,
`extraction.py:297`, `templates.py:130`, `harness.py:197` and `facts.py:174`
before a line of Phase 8 was written, rather than assumed.

So the phase decomposed into things that already existed:

| The question | Answered by | Since |
| ------------ | ----------- | ----- |
| Is this assumption an estimate? | `AssumptionExtractor`, ADR 0016 | Phase 4 |
| What is this group's factor? | `BiasDetective.factor_for` | Phase 7 |
| Does the calibrated value violate the predicate? | `praxis.predicates`, ADR 0019 | Phase 5 |
| Which decisions rest on this estimate? | `Repository.impacted_by` | Phase 1 |
| Which misses count? | `MatchQuality`, ADR 0021 | Phase 6 |
| How severe is it? | `praxis.monitor.breach.severity_for` | Phase 5 |

`FusionBridge` and `CollateralAgent` are what is left after all six: a graph
walk, an indexed read, a multiplication and two predicate evaluations. **Both
make zero model calls** and both left ADR 0006's `reason` row — the most
expensive tier in the table (ADR 0026, ADR 0027).

**This is the fifth and sixth consecutive component to move to the arithmetic
side**, and ADR 0027 stops treating that as a run of good corrections. Six is
evidence about `ARCHITECTURE.md` itself. Two readings fit; the one this project
takes is that **the judgement is real and is being pushed steadily earlier**,
into extraction, where it is paid for once and recorded as an edge. The honest
cost of a fusion finding therefore includes the `reason`-tier call Phase 4 made.
That reading also predicts where it stops: `ChallengerAgent` and
`ReviewTriageAgent`, whose input is prose nobody has reduced to a record, should
keep their routes.

## The decision the phase forced: a projection is not a breach

`ARCHITECTURE.md` said step 5 emits an `AssumptionBreach`. **It does not, and
the file was changed.**

A breach is a predicate evaluated false against facts a monitoring run was
*given*: something happened. A fusion flip is a predicate evaluated false against
a number **nobody has observed yet**, projected from a track record: nothing has
happened and the work may still land on time. Storing both as one kind would let
a triage queue rank the projection above the measurement, and a person cannot
un-see "this assumption is breached" about work that has not started.

So the forward direction files `STALE_DECISION` and opens its prosecution with
*"Nothing has been measured yet"*; the backward direction files
`COLLATERAL_IMPACT`, which is a measurement. ADR 0028. Both kinds, and
`LinkType.COLLATERAL_OF`, had existed since Phase 1 with nothing writing them —
which is some evidence the Phase 1 vocabulary was drawn from the right five
allegations rather than five that sounded plausible.

## Cross-document matching: the risky item, and why it cost nothing

`EST-0009` priced this as the phase's riskiest line and gave an explicit
instruction: if it cannot be built without widening the offering, **cut it to
the backlog rather than spend ADR 0015**.

It did not need spending, because **the invariant is about one call and not
about one estimate**. `CrossDocumentMatcher` never widens a listing; it runs the
existing single-document matcher once per candidate document. Every ordinal
still resolves inside the document it was offered from and `VerifierAgent` still
re-reads each quotation against that one document. What grows is the number of
calls — a token cost in the column the eval table already has, and the same
trade ADR 0015's own accepted-costs section makes for re-rendering a listing per
window.

Cost is bounded by a deterministic document selector and a budget of three
documents per estimate; the home document is read first and the walk stops at
the first resolution, so the common case costs exactly what it cost in Phase 6.
`tests/agents/test_crossdoc.py` asserts the invariant against `offering_of`
itself, so weakening it later is a visible deletion rather than a side effect.

Keeping the walk going after a resolution — to catch two documents claiming
different actuals — was considered and rejected: it multiplies spend on the
common case to buy a contradiction this corpus cannot produce, and noticing that
two stored records disagree is `ContradictionDetector`'s job.

## What the numbers say, and what they do not

**Offline, every fusion number is zero, and that is correct twice over.**

The citation gate refuses 38 of 40 claims against the mock provider (ADR 0016),
so almost nothing survives extraction and the store holds **no `estimated_as`
edges at all**. `praxis fuse` therefore reports nothing to price. That is the
gate working, not the layer failing, and the CLI says so in those words — a
correction made only because the pipeline was run end to end rather than read.

There is a **second** reason, independent of the first, and it is the more
interesting one: a flip needs a factor, a factor needs five resolved estimates in
one group, and this corpus resolves three outcomes across nine estimates. **No
group could reach five even fully populated.** A run reporting flips against a
corpus this size would have broken the threshold, not beaten it.

What *is* measured offline is the pair of internal claims that need no answer
key: `refusals_hold` (no edge reported a factor for a group `BiasDetective`
refuses) and `flips_hold` (every finding came from a predicate that really
moved). Both are asserted in both directions, because a boolean that is never
false proves nothing.

## Defects found, and how

**Three of the four real bugs this phase produced were found by tests, not by
review**, which continues Phase 7's pattern.

1. **`Repository.get` returns a retracted record, not `None`.** Retraction is a
   statement *about* a record rather than its disappearance, and `list_all`
   excludes retracted rows while `get` does not. A component testing only for
   absence prices a withdrawn estimate as though it still stood. Fixed in three
   places. The store's foreign key is what makes retraction the *only* reachable
   form of "the target is gone", asserted as a control.
2. **Batched `next_id` hands every record the same id.** `fuse_store` built all
   its findings before writing any, and `next_id` reads the highest ordinal *in
   the store*. This is precisely what `StoreAllocator`'s docstring describes, and
   it was walked into anyway. Ids are now allocated at write time, so a pass over
   a settled store spends none.
3. **`FusionVerdict.UNDECIDED` is unreachable through the bridge.** `subject_of`
   accepts a predicate only when `constraints_of` yields exactly one name, and
   `constraints_of` yields names only for simple `name OP literal` comparisons —
   so every predicate an evaluator could leave `UNKNOWN` is refused as
   `NO_SUBJECT` several steps earlier. The guard is kept and tested directly
   through `moved`, and ADR 0026 assumption 4 expires the day the language grows.
4. **Two report sections headed `## Fusion`**, and a shared renderer saying "no
   groups in the store" under a heading about edges. Found by running the
   harness.

## Against `EST-0009`

`EST-0009` was **the first estimate in this project's history to apply a bias
correction**: a raw 6.5h built bottom-up, multiplied by the measured factor of
0.5556 (1.7999× over, n=5, band 1.35×–2.41×, confidence 0.3875), logged as
**3.6h active** with a plausible range of 2.7h–4.8h.

It also corrected `OUT-0008`'s named mistake rather than repeating it: the whole
adjustment went into the **quantity**, and confidence moved **down** (0.50 →
0.45) rather than up. The sub-population objection — that
`agent-implementation` contains both an all-deterministic Phase 7 at 2.62× over
and a model-heavy Phase 5 at 1.41× — was filed as a *condition* and the
class-splitting escape refused in writing, because splitting would drop every
sub-class below the threshold and silence the detective.

**`OUT-0009`: 3.5h active against 3.6h predicted — `EXACT`.** The first exact
match in this project's history, and also the first bias-corrected estimate.
Both facts are worth stating together and worth not over-reading.

Had the raw 6.5h been logged uncorrected, as the previous eight were, the phase
would have come in at **1.857× over** — which sits almost exactly on the fitted
factor of 1.7999× over, in the middle of the 1.35×–2.41× band, and would have
been the sixth consecutive over-estimate. So the correction did not merely help:
**the raw estimate missed by close to the amount the estimator's own history
predicted it would.**

`OUT-0009` also takes `agent-implementation` to six, which is the first moment
this project's history can be *backtested* — a prequential walk needs one point
more than `MINIMUM_SAMPLE`. Walked forward, the single scoreable row reports
`improved=1, worsened=0`, log error **0.6190 → 0.0313**.

**And that number was wrong the first time, which is the most useful thing this
outcome produced.** Closing `OUT-0009` initially reported the correction as
*harmful* (`improved=0, worsened=1`). The cause was not the maths. `EST-0009` is
the first row in the log whose `active_quantity` is **already corrected**, and a
prequential backtest asks whether applying the correction *would have* helped —
so it must be handed the uncorrected figure. Handed the corrected one, it applied
the factor a second time. The raw number was never lost, but it lived only in
`calibration_note` prose where nothing could read it; it is now
`raw_active_quantity`, and `dogfood_rows` prefers it.

Worth being precise about the blast radius: **this is a defect in the dogfood
log, not in the product.** `CalibratorAgent` is a pass-through that explains
(ADR 0025) and `calibrate_store` writes findings, never revised estimates, so
Praxis does not store corrected estimates back into the history it calibrates
from. The self-referential loop was introduced by hand, by the author, in a JSONL
file — which is exactly the class of error dogfooding exists to surface, and it
surfaced on the first estimate that could possibly have triggered it.

Every condition `EST-0009` set was met, with one deliberate deviation:
`ReviewTriageAgent` is not in this phase, which the estimate said explicitly
before pricing rather than discovering during it. Blocked was predicted at 12.0h
against **0.1h** actual — the sixth consecutive miss, and the sixth confirmation
that it measures when the author sleeps rather than when the work waits.

## Carried forward

- **`Repository.get` and `list_all` disagree about retracted records.** Any later
  component that resolves a record before acting on it must check `.retracted`.
- **`next_id` is not an allocator.** Anything building a batch of records before
  storing them needs `StoreAllocator` or write-time allocation.
- **Price the arithmetic/judgement boundary as its own line item.** Fifth and
  sixth time; `EST-0009` predicted the outcome in advance for the first time.
- **Every property needs a control.** Held here: the flip claim gets a store
  where nothing flips, the refusal claim gets one where everything speaks, and
  the projection heading is checked against the measurement heading.
- **Run the pipeline, do not read it.** Two of the four defects above were
  invisible from the code and obvious from one command.
- **A corrected estimate is not a raw one, and a backtest needs the raw one.**
  Every future phase that applies a correction must log `raw_active_quantity`
  beside `active_quantity`, or the prequential walk will double-correct. The
  pinned tests now hold this open with a test whose only job is to fail if the
  fixture ever reads the corrected figure again.
