# Phase 5 — the memory: predicates that expire, and the agents that read them

> Merged as [#12](https://github.com/SihanUdayaratna03/praxis/pull/12), tagged
> `v0.5-phase-5`. 2179 tests, 98.68% coverage, CI green.

Phase 4 turned spans into records. Phase 5 turns records into a **memory** — one
that can tell you an assumption you made in March stopped being true in June,
and which decisions leaned on it. That is the half of the thesis provenance was
always for, and it is now built.

Half A is closed.

---

## The result worth leading with

**ADR 0001's first assumption is answered, and it holds.**

On 2026-08-09, before a parser existed, this was recorded:

> The predicate DSL built in Phase 5 can parse predicates written by hand before
> it existed — `adr_predicates_parsed / adr_predicates_total >= 0.9`, expiring
> `on_event("Phase 5 predicate DSL is implemented")`.

That event has now happened. Across `docs/adr/` there are **80 hand-written
predicates; 79 parse — 0.9875** against a threshold of 0.9 — and **all 80 expiry
conditions parse.**

This is the first assumption in this project recorded in Praxis's own schema and
then actually *answered*, by code that recomputes it from the files rather than
by a test that asserts it. That distinction is the whole exercise: an author who
wanted the number to hold could widen the grammar until it did, and recomputing
it makes that visible as a grammar change instead of invisible as a passing
test.

**The one row that does not read stays a finding.** ADR 0015's third assumption
is `mis_attribution_rate / fabrication_rate outside [0.5, 2.0]`, which is
English wearing an expression's clothes. A test names it by file, so adding an
interval-membership operator for a single instance would fail rather than
quietly turn a measurement into a feature request answered by editing the thing
being measured.

---

## The decisions the phase turns on

Four, each with an ADR, and all four are really the same decision applied in
four places: **be precise about which half of a judgement is arithmetic.**

**[ADR 0017](../adr/0017-a-small-total-predicate-language.md) — a small total
language, evaluated three-valued.** Nothing raises at evaluation time; a
malformed predicate fails at *parse* time. An identifier nothing has measured
makes a predicate `unknown`, never false — because treating unmeasured as
breached would flood a person with findings on their first run and teach them
the findings are noise. `render(parse(s))` round-trips, which is what lets a
stored predicate be normalised so two spellings of one claim land in one place.

**[ADR 0018](../adr/0018-blocking-then-arithmetic-then-a-model.md) — blocking,
then intervals, then a model.** Three stages, and only the last is a model.
Deterministic blocking proposes the pairs worth comparing; interval arithmetic
settles every pair whose conflict is a *fact* (confidence 1.0, zero calls); only
the residue is batched to the reason tier. `Settlement` records which stage
decided, so a low recall can be traced to the stage that lost the pair rather
than argued about.

**[ADR 0019](../adr/0019-only-arithmetic-can-breach-and-writes-happen-on-change.md)
— only arithmetic can breach, and a pass writes only on a change.** Two
properties in one record because each makes the other safe. `BREACHED` is
reachable only by evaluating a parsed predicate against measured facts; the
single model call in a monitoring pass can only ever move an assumption to
`EXPIRED`. And the store already holds the last verdict, so re-runnability is a
comparison rather than bookkeeping.

**[ADR 0020](../adr/0020-the-archaeologist-retrieves-and-never-generates.md) —
the model selects, the store speaks.** `ArchaeologistAgent` is asked only which
decision and which rejected option, both by reference into a listing. The answer
is assembled from stored fields; nothing the model wrote reaches it. This ADR is
unusual and says so: **the agent's spec was never written down.** Three
fragments existed — `RejectedOption`'s docstring, a note in the corpus
generator, ADR 0006's routing entry — so the contract it states is a judgement,
recorded to be disagreed with.

---

## What shipped

| | |
| --- | --- |
| `praxis/predicates/` | Lexer, parser, three-valued evaluator, interval arithmetic, expiry grammar, world state |
| `praxis/agents/formalizer.py` | Prose → a predicate that parses. Never drops what will not compile |
| `praxis/agents/formalization.py` | The formalizer over a store, with two different skip reasons |
| `praxis/agents/blocking.py` | Which pairs are worth comparing. Deterministic |
| `praxis/agents/contradiction.py` | Blocking → intervals → a model, in that order |
| `praxis/agents/detection.py` | The detector over a store; edges written once |
| `praxis/agents/archaeologist.py` | "Why not X", answered out of the record only |
| `praxis/monitor/` | The verdict, the facts, the breach finding, the store pass |
| `praxis/corpus/measurements.py` | The world a monitoring run is graded in, derived |
| `praxis/eval/memory.py` | Grading what the store remembers, not what it copied |
| `praxis/eval/adrs.py` | The one metric that grades the project rather than a run |
| `praxis/cli_monitor.py` | `praxis formalize`, `monitor`, `contradictions`, `why` |

Four prompts, four ADRs, **638 new tests**, and no schema migration — the
schema stays at version 4, exactly as `EST-0006` predicted.

### Three things worth reading the code for

**Nothing that fails to compile is dropped.** An assumption the formalizer
cannot turn into a predicate is stored anyway, marked, with its confidence
capped at `UNCHECKABLE_CEILING` and the audit row saying why. The monitor
refuses to breach on a predicate it cannot read, and a person can fix what they
can see. Silence cannot be fixed by anyone. This is the same instinct
`DecisionScout` set and the segmenter's floor repeated: degrade quality, never
correctness.

**Two skip reasons, and they are different questions.** The formalization pass
skips an assumption whose predicate already parses — nothing to compile — and,
separately, one this agent has already failed at, which is read off the *audit
trail*. Without the second, a re-run would pay the reason tier for every
uncompilable assumption forever. `--force` exists because the first check is
about the text and the second about the history, and a better prompt deserves
another attempt at the same text.

**The corpus's expected verdicts are derived, not written down.** The first
draft of the answer key assigned roles and added the measurement each role
needed — and because two documents state `index_size_gb <= 50`, it wrote one
fact twice and was silently wrong about one of them. Choosing the world first
and computing every verdict from it makes that unrepresentable, and a test
asserts the key cannot contradict itself.

---

## The metrics

`praxis eval .praxis-tmp/corpus5`, pasted rather than typed. Sixteen documents,
seed 20260809, four planted contradiction pairs.

**corpus_seed**: 20260809 -- **extract_assumptions**: v1 -- **provider**: mock -- **scan_for_decisions**: v1 -- **structure_decision**: v1

| Kind | Found | Missed | Spurious | Distracted | Precision | Recall | F1 | Fields | Exact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| decision | 0 | 9 | 1 | 0 | 0.0000 | 0.0000 | 0.0000 | -- | 0 |
| assumption | 0 | 13 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | -- | 0 |
| estimate | 0 | 9 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | -- | 0 |

### Citation integrity

- Claims stored: **1**, refused: **38** (integrity **0.0256**).
- Fabricated quotations: **0.5385**; mis-attributions: **0.1795**.

| Refusal | Count |
| --- | --- |
| fabricated_quote | 21 |
| empty_answer | 10 |
| mis_attributed_quote | 7 |

### Fusion

`estimated_as` edges found: **0** of 3 the corpus labels (recall **0.0000**).

Documents graded: 16. Model calls: 64. Windows never answered about: 0.

### Formalization

- Assumptions stored: **0**, predicates that parse: **0** (**1.0000**).
- Checkable — predicate *and* expiry parse: **0** (**1.0000**).

### Monitoring

- Expectations: **9**, agreed: **2** (accuracy **0.2222**).
- Breaches: precision **0.0000**, recall **0.0000**.
- **Aged, misreported as breached: 0.**

### Contradictions

| Stage | Found | Missed | Spurious | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- |
| contradiction | 0 | 4 | 0 | 0.0000 | 0.0000 | 0.0000 |
| arithmetic | 0 | 4 | 0 | 0.0000 | 0.0000 | 0.0000 |
| model | 0 | 4 | 0 | 0.0000 | 0.0000 | 0.0000 |

Pairs the corpus planted: **4**, of which blocking proposed **0** (recall
**0.0000**). Assumptions the answer key could name: **0**.

> Every number above was produced by the offline provider, which draws each
> field of an answer independently — so a cited passage and a quotation agree
> only by chance. These measure the pipeline, not a model. See ADR 0016.

### What these numbers are, and what they are not

**Read the extraction rows first, because everything below them is downstream of
one number.** The citation gate refused 38 of 39 claims, so **one record reached
the store for the whole corpus.** That is Phase 4's known offline behaviour
(ADR 0016) and it is unchanged: `praxis/llm/synthesis.py` draws a cited ordinal
and a quotation independently, so they agree by chance. It is the gate working
exactly as designed against a synthesiser that cannot cite.

Every memory row therefore reports on **a store with no assumptions in it.**
The formalization row is `0 of 0`. The contradiction rows are four misses
because the four planted pairs need eight extracted assumptions that do not
exist. `AssumptionFormalizer`, `AssumptionMonitor` and `ContradictionDetector`
do not appear in the cost table at all, because with nothing to work on they
made **zero model calls**. Those zeros measure the extraction gate, not the
agents beneath it.

**This corrects something the working log claimed.** The Phase 5 log asserted
that the offline provider now answers a `predicate` field from the passage, so
"the formalizer's offline path genuinely runs — unlike Phase 4's all-zeros". The
first half is true and the second is misleading about *this table*. The mock
does now quote a predicate out of the passage, and given an assumption the
formalizer offline compiles it correctly:

```
predicate: 'index_size_gb <= 50'
expiry   : 'when(indexed_documents >= 10000000)'
checkable: True
```

But the corpus run never gets that far, because extraction stores nothing for it
to compile. The formalizer's offline path is exercised in
`tests/agents/test_formalizer.py` and `test_formalization.py` — 100% covered —
and **not** by the number in the table above. Two different reasons for a zero,
and the table alone cannot tell them apart.

**The monitor's store-derived binding reads zero, and that one is expected for a
different reason.** `measured_in` binds an identifier only where an assumption
carries an `estimated_as` edge to an estimate that an `Outcome` resolves — and
**nothing writes an `Outcome` until Phase 6.** That is mechanism waiting, not
mechanism failing, and `EST-0006` predicted it in those words.

**`aged_misreported_as_breached` is 0, and that is the one number here that
means what it says.** It is zero by construction: the monitor reaches `BREACHED`
only through arithmetic on facts, so a non-zero value would mean the property
broke rather than that a model was wrong. It is reported by name so a table
cannot omit it by not thinking of it.

The monitoring accuracy of 0.2222 is nine expectations against a store that
evaluated none of them; the two that agreed are the two the corpus expected to
be `UNVERIFIED`, which is what an assumption nothing measured really is.

**What is actually measured here** is the plumbing: sixteen documents ingested,
64 calls made, the three memory passes running in order without error, the graded
join working, and every table rendering. The harness is the deliverable. The
numbers are a floor and will stay one until a live provider runs.

---

## Things that cost something

**The tests found a real defect in the archaeologist.** `MIN_TERM_LENGTH` was 3
and the terms are `OR`-joined, so `why`, `not` and `for` matched almost every
document — a question about zeppelins retrieved the entire search index, one
model call per question, with the refusal left to a prompt to produce. Raised to
4, which is the floor `blocking.py` already uses for the same reason. Two
components arrived at the same number from opposite directions, which is the
kind of agreement worth noticing.

**The corpus had no contradictions at all before this phase**, so a detector
finding nothing and one finding everything scored identically — which is not a
metric. Every topic gained a `reversal_predicate` written to be *provably*
disjoint from its original, and a test asserts all four are provable rather than
trusting they read as opposites. Four revisions rather than eight, so the corpus
holds assumptions that were overturned *and* assumptions that were not.

**`FORMAT_VERSION` went to 2 and a version 1 key is refused** rather than read
as one with no expectations — otherwise "the monitor found nothing" and "the
corpus expected nothing" would be the same number.

**One module shipped with no tests and was closed first this session.**
`praxis/agents/formalization.py` was committed under a stop signal with ruff and
mypy green and no tests. That is a rule this project does not otherwise break,
and the handover said so explicitly so the next session could not miss it. It is
now at 100%, and the tests assert the provider was never called on a skip —
because a skip that still pays the reason tier is exactly what the audit-trail
check exists to prevent, and a count alone would not have caught it.

---

## The estimate

`EST-0006` predicted **6.5h active** and 24.0h blocked. The actual is **≈4.6h
active** and **≈17.3h blocked** — an over-estimate of about **1.4×** on the
measure the estimate was stated in.

Active is the sum of four session windows bounded by commit timestamps, each
extended backwards by the reading that preceded its first commit — the same
methodology `OUT-0002` onward used, and the same caveat: this is an estimate of
an estimate's error and nothing here measures keystrokes.

| Session | Commits | Span | Active |
| --- | --- | --- | --- |
| 1 | 17:14 → 18:13, 2026-08-25 | predicates, formalizer, monitor | ≈1.3h |
| 2 | 20:57 → 21:14 | blocking, contradiction, archaeologist | ≈0.5h |
| 3 | 23:30 → 00:08, 2026-08-26 | corpus, traces, `eval/adrs.py`, metrics | ≈0.9h |
| 4 | 12:27 → close, 2026-08-26 | formalization tests, eval, CLI, ADRs, docs | ≈1.9h |

Blocked is elapsed wall clock between those windows — 2.7h, 2.3h and 12.3h. As
`EST-0006`'s own `blocked_note` said before the phase began, it measures when
the author slept rather than when the work waited, and 24.0h against 17.3h is
not a number anyone should read as an engineering prediction.

### Where the estimate was right

Every checkable condition held:

- **No schema migration**, and the schema is at version 4. The estimate said
  that explicitly and said it would be wrong by a migration if false.
- **No routing table entry** — all four agents were already in
  `praxis/config/models.py`, which the estimate says was checked rather than
  assumed.
- **Four ADRs and the phase report** are inside the phase.
- **No live API call and no key**, and every model-dependent number is reported
  as a floor in those words beneath the table.
- **The monitor's store-derived binding binds nothing** in the corpus run and is
  reported as not-yet-exercised rather than as a failure — predicted exactly.
- **The metrics table is `praxis eval` output pasted in**, not typed.

The six-line-item pricing of the predicate DSL is the correction `OUT-0005`
bought, and it worked: the parser came in near its line-item reading rather than
at the 2× a one-line-item pricing produced in Phase 3.

Both risks in the risk note fired, and both were reported rather than papered
over. ADR 0001's assumption was fired and answered — and the one predicate that
does not parse was left as a finding instead of being fixed by widening the
grammar, which is precisely what the note said would happen. The corpus work was
a dependency of the metric rather than tidying, and the contradiction row exists
because it was done.

### Where it was not

The estimate priced the eval extension as four line items against the four
existing modules and said it would *add* to them. It became **five** modules:
`praxis/eval/memory.py` is new. The reason is legible — grading what the store
remembers needs the record-to-item join, and putting that in `harness.py` would
have pushed it past the 400-line guideline the style guide sets. That is the
same shape of miss as `OUT-0004`'s corpus generator, one size smaller: a
component described as an extension turned out to have a subject of its own.

The phase also shipped `praxis/cli_monitor.py` and its 18 tests, which the
estimate does not mention at all. Phase 4 did the same thing with `cli_eval.py`.
Two phases running, the CLI surface for a phase's agents has been built and not
priced — that is now a pattern rather than an oversight, and `EST-0007` should
carry a line item for it.

### The pattern, at n = 3

Agent-implementation now has three outcomes and **all three are
over-estimates**: 1.31×, 2.18×, 1.40×. It is the only class in this corpus with
a direction rather than a scatter.

**No bias correction was applied to `EST-0006`, for the sixth time**, and the
reason is the same one it has always been: `BiasDetective` refuses below n = 5,
and applying a factor at n = 2 would have been the author breaking his own
product's rule at the exact moment it first became tempting. That judgement now
looks better rather than worse — the three ratios span 1.31× to 2.18×, so a
correction fitted to the first two would have over-corrected this one.

Two more outcomes in this class and the demo has something real to say.

---

## For the session that writes `EST-0007`

Half A is closed and Phase 6 is Half B: `EstimateExtractor`, `WorkClassifier`,
`OutcomeMatcher`, `BiasDetective`, `CalibratorAgent`, `ScoringAgent`.

Three things this phase leaves it:

1. **`Outcome` is the missing piece, and two zeros in this report are waiting on
   it.** The monitor's store-derived binding and the fusion chain both work and
   both bind nothing until something writes an `Outcome`. Phase 6 turns those
   into real numbers, and it should check them rather than assume.
2. **Price the CLI.** Two phases running it has been built and not estimated.
3. **`BiasDetective` is the first agent whose whole point is refusing.** It
   answers with a factor, an `n` and an interval, and refuses below n = 5 — and
   this project's own corpus is at n = 3 in one class and n = 1 in three others,
   so the first thing it will do on real data is decline. That is correct, and
   the report should say so before anyone reads it as a failure.
