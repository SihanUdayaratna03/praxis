# Handover — start of Phase 7

Read this first, then `CLAUDE.md`. Written at the close of Phase 6 so the next
session can start working instead of re-deriving state.

---

## Where we stopped

Phase 6 is merged, tagged and green. Nothing is in flight. **Both halves are
populated.**

| | |
| --- | --- |
| `main` | `ef0bb2d`, local and remote identical |
| Tag | `v0.6-phase-6` → `ef0bb2d` (dereferenced through the remote, not assumed) |
| CI | 5/5 green on [#14](https://github.com/SihanUdayaratna03/praxis/pull/14) |
| Open PRs | none |
| Remote branches | `main` only |
| Working tree | clean |
| Suite | **2431 passed**, coverage **98.80%** (gate 85%) |
| Schema | version 4 — Phase 6 needed no migration |

```bash
cd "C:\Users\sihan\OneDrive\Desktop\Praxis Agents"
git checkout main && git pull
uv sync --all-groups
uv run praxis doctor            # expect OK, including one offline model call
uv run pytest                   # expect 2431 passed

# the whole pipeline, end to end and offline
uv run praxis init
uv run praxis corpus generate .praxis-tmp/corpus
uv run praxis ingest .praxis-tmp/corpus/documents
uv run praxis extract           # Half A
uv run praxis estimates         # Half B: extract -> classify -> match
uv run praxis formalize
uv run praxis monitor
uv run praxis contradictions
uv run praxis why "why not Postgres full-text search"
```

**Every one of those still reports near-zeros offline, and that is still
correct.** The citation gate refuses 38 of 40 claims against the mock (ADR
0016), so one estimate reaches the store for the whole corpus and everything
downstream is starved. What the run verifies is that the pipeline holds together
without credentials, not that the numbers are good. `docs/reports/phase-6.md`
says this in the report itself rather than only in conversation.

**Shipped in Phase 6:** `EstimateExtractor`, `WorkClassifier`, `OutcomeMatcher`
and the arithmetic half they lean on (`praxis/agents/reconciliation.py`);
`praxis/agents/estimation.py` running the three over a store;
`calibration_history` in `praxis/store/reports.py` — Phase 7's own query, written
and executed now; `praxis/eval/estimation.py` and the third half of the metrics
table; `praxis estimates`; the corpus's `blocked_quantity` ground truth. 220 new
tests in eight files, 252 new overall, all seven new modules at 100%. ADRs
0021–0023.

---

## The result Phase 6 earned

**The fusion chain runs end to end.** A matched outcome binds
`search_index_weeks = 7`, and `AssumptionMonitor` breaches `A-0001` on it by
arithmetic — a missed estimate reaching forward through an edge Half A wrote to
invalidate the assumption a decision rests on. Three tests in
`tests/agents/test_estimation.py::TestTheFusionChain` hold it, including the one
that matters most: an `unresolved` outcome binds nothing and breaches nothing,
which is why the other two are not a false positive.

Both zeros the Phase 5 report had to explain are answered. The monitor's
store-derived binding was mechanism waiting, and it moves. The formalization zero
is still upstream starvation and unchanged.

---

## Phase 7 — calibration

`ARCHITECTURE.md`'s Half B column has three names left: `BiasDetective` (no LLM),
`CalibratorAgent`, `ScoringAgent` (no LLM). Five things worth knowing before
planning it.

**Your query already exists and is tested.** `Repository.calibration_history`
returns one row per estimate beside the outcome standing against it, ordered by
owner then work class then estimate id — so grouping is a walk rather than a
sort. `CalibrationRow.resolved` is how to exclude unresolved rows, and *whether*
to exclude them is your decision: both counts come from one read, deliberately,
because "eleven estimates, four never answered" and "seven estimates" are
different facts. Do not write a second query; if this one is awkward, that is a
finding worth reporting rather than routing around.

**`BiasDetective` and `ScoringAgent` are deterministic and `NON_LLM_AGENTS`
enforces it.** Invariant 3, and `praxis doctor` fails if either acquires a route.
Phase 6 added a fourth member to that argument in practice:
`praxis/agents/reconciliation.py` holds `match_quality` and imports no provider,
so the rule holds by construction. Consider the same shape here.

**`BiasDetective`'s whole point is refusing, and it will refuse.** Run against
this project's own history it declines on every class — `agent-implementation` at
n = 4, three others at n = 1. That is correct behaviour and the Phase 7 report
should say so plainly *before* anyone reads it as a failure. The Phase 5 handover
wrote that sentence about Phase 6 and was right.

**The corpus grades both quantities now.** `blocked_quantity` is stated in prose
and graded on both the estimate and the outcome, with tests holding that not
every estimate predicts a block and that the blocked errors run in both
directions. `ScoringAgent` comparing active against active has something to be
graded on that is not the effort figure alone.

**A schema migration is unlikely but check rather than assume.** Phase 6 needed
none. `FindingKind.CALIBRATION_BIAS` already exists from Phase 1; whether a
calibration factor needs somewhere to live that is not a `Finding` is the
question to answer against `praxis/domain/records.py` and `001_core.sql` before
pricing. `EST-0006` and `EST-0007` both made this a checkable condition and both
were right; keep doing that.

---

## Before writing `EST-0008`

**`agent-implementation` is at n = 4 and one short of the threshold.**

Recompute from the two files rather than from this paragraph — but as of the
close of Phase 6 the four ratios are **1.31× / 2.18× / 1.40× / 1.79×, every one
an over-estimate, mean 1.67×**. No other class has more than one point.

This is the seventh phase in a row with no bias correction applied, and the
reasons have been recorded each time. Phase 7 is where that changes shape:
`BiasDetective` answers at n = 5, so the fifth outcome is the first the product
itself would speak about. Whatever `EST-0008` does, it should say which of these
it is doing and why — applying a factor, refusing one more time on a stated
reason, or noting that the threshold has not yet been crossed. A silent decision
is the one thing that would waste six phases of discipline.

**Three carry-forward lessons, in the order they will cost you.**

1. **Price the arithmetic/judgement boundary as its own line item.**
   `OutcomeMatcher` was priced as one module and became two, because that
   boundary deserved a module. `OUT-0004`, `OUT-0006` and `OUT-0007` are the same
   shape of miss three phases running: a component described as one thing having
   a subject of its own inside it. In this codebase that subject is repeatedly
   "the part that cannot call a model".
2. **Price the CLI.** It worked this time. Keep doing it.
3. **Price each agent's refusal vocabulary inside the agent.** Bought by
   `OUT-0005`, confirmed by `OUT-0006` and `OUT-0007`. Still the single most
   reliable correction in this record.

**Blocked time is still not an engineering prediction.** Four attempts now, all
missed, in both directions and by wide margins: 20.0/12.8, 16.0/72.3, 24.0/17.3,
18.0/0.9. It measures when the author sleeps. Log it because the record has the
field, and compare active against active.

---

## Rules that have not changed

- **Log `EST-0008` before writing any Phase 7 file**, on the phase branch. A
  phase with no prediction is a hole in the Phase 12 demo.
- **`main` only moves through a reviewed, CI-green merge commit.** Never squash.
- **Long text goes to a file**, never inline: `--body-file`, `-F`. The shell here
  has a ~965-byte parse limit and PowerShell 5.1 mangles embedded quotes.
- **Commit after every component**, then push, then append to this file. Never
  batch. Phase 6 was cut in half by the session limit and lost nothing, because
  every component was committed and logged before the next began.
- Progress explanations to the product owner are in **Sinhala**; everything in
  the repository is in English.

One thing to carry forward: **four defects this phase were found by tests rather
than by review, and three only by tests above the unit level.** Every agent was
correct in isolation; the interactions were not. The `created_by` bug in
particular would have re-extracted every classified document forever and no unit
test could have seen it. Write the store-level test.

---

## Phase 6 progress log

One line per component, appended as it lands. Written so a session that dies
mid-phase can be resumed from the last line rather than from a diff.

- `3801800` **EST-0007 logged** — 5.0h active, 18.0h blocked, uncorrected for the
  seventh time (`agent-implementation` at n = 3, threshold 5). Branch
  `feat/phase-6-half-b-agents` cut from `main` at `931055c`, pushed.
- `0a5f910` **`EstimateExtractor`** — `praxis/agents/estimator.py`, prompt
  `extract_estimates.v1`, 32 tests, module at **100%**. Scan tier, one call per
  window, cites through the existing `CitationGate` unchanged. Refuses a
  quantity with no unit, never invents an owner, never sets `work_class`. An id
  is spent only after the citation survives. Merged `--no-ff` as `033019b`.
- `11bdaa3` **`WorkClassifier`** — `praxis/agents/classifier.py`, prompt
  `classify_work.v1`, 41 tests, module at **100%**. Revises rather than writes,
  so it also repairs the `unclassified` rows Phase 4 left. The store's existing
  vocabulary is offered to the model and `proposed` is computed against the
  store, not taken from the answer. `work_class_of` moved here from
  `extractor.py` — one spelling rule, one place, property-tested total and
  idempotent. Merged `--no-ff` as `114202a`.
- `00dad80` **`OutcomeMatcher`** — `praxis/agents/matcher.py` plus
  `praxis/agents/reconciliation.py`, prompt `match_outcome.v1`, 64 tests, both
  modules at **100%**. Three stages, only the middle a model. `match_quality`
  and unit conversion live in `reconciliation.py`, which imports no provider —
  invariant 3 made structural. Every path leaves exactly one `Outcome` per
  estimate; an unmatched one is `unresolved`, never dropped. The bands
  reproduce 5 of this project's 6 hand-scored outcomes and `OUT-0002` is pinned
  as a deliberate disagreement. Cross-document matching deferred to
  `BACKLOG.md` under ADR 0015. Merged `--no-ff`.
- `53f5a80` **`EstimationPipeline`** — `praxis/agents/estimation.py`, 24 tests,
  module at **100%**. Extract → classify → match over a store, writing through
  the Phase 1 `Repository`. A second pass costs zero calls, for three different
  reasons. `Stage` gains `ESTIMATE`/`CLASSIFY`/`MATCH`; `StoreAllocator` and
  `spans_by_document` promoted out of `extraction.py`. **Found and fixed a real
  bug**: `WorkClassifier` was overwriting `created_by` on revision, which made
  every classified document look unread and would have re-extracted it next
  run. `created_by` is the producing agent; the audit trail is who revised.
- `81a2067` **Corpus: blocked time** — checked first, and the estimate/outcome
  ground truth was already there (`status_update` joins them; `issue_export`
  plants an unmatched estimate). The real gap was `blocked_quantity`, stated by
  no document and graded by neither field tuple. `Topic` gains
  `blocked_weeks`/`actual_blocked_weeks`, the status update states both, both
  sides are graded, and three properties are held by tests. `GENERATOR_VERSION`
  2 → 3; `FORMAT_VERSION` stays 2. Corpus: 9 estimates, 3 resolved, **6
  unmatched** — the match rate's ceiling is 0.33 by construction.
- `3aa8aa0` **Phase 7's query, executed** — `calibration_history()` in
  `praxis/store/reports.py` (the only place SQL lives), delegated from the
  repository, 15 tests, module at **100%**. One indexed join, because the
  pairing is a column, an unmatched estimate is an `unresolved` row, units are
  reconciled at write time, and `work_class` is on the estimate. Sketching it
  is what made the unresolved row a design decision rather than a nicety: the
  denominator and the sample now come out of one read. The current-version
  filter is load-bearing here — `WorkClassifier` revises onto this axis.
- `ec98402` **Eval extension** — `praxis/eval/estimation.py` plus edits to
  `harness`, `metrics` and `report`; 21 + 8 tests; all three modules at
  **100%**. `ItemKind.OUTCOME` joins `GRADED_KINDS`, closing the line Phase 4
  left open. Pass order is now extract → **estimate/classify/match** →
  formalize → monitor → detect, and that order is load-bearing.
  **Both Phase 5 zeros move**, checked by three end-to-end tests: a matched
  outcome binds `search_index_weeks = 7`, the monitor breaches `A-0001` on it,
  and an unresolved outcome binds and breaches nothing.
- `779ed90` **`praxis estimates`** — `praxis/cli_estimate.py`, 20 + 5 tests,
  module at **100%**. Priced as a line item, which is OUT-0006's first lesson.
  One command, not three. Tests found three real defects: rich swallowed a
  bracketed `[partial]` as markup; a test assigning `console.file` pinned the
  module console to a stale stdout and silently ate later CLI output; and the
  re-run claim was too strong — extraction skips a document only once it has
  *produced* an estimate, the same limitation `praxis extract` has had since
  Phase 4. Corrected, tested, and in `BACKLOG.md`.
- **Synthesis: no gap.** Checked before assuming, per the plan.
  `EstimateSightings`, `ClassAnswer` and `OutcomeAnswer` all validate against
  `synthesise_answer` — `Decimal`, the `Unit` enum, ordinals and quotes are
  already handled at `synthesis.py:328`. No extension written.
- `a4793d8` **ADRs 0021–0023** — match quality is arithmetic (and lives in a
  module that cannot reach a provider); an unmatched estimate is an
  `unresolved` outcome; work class is assigned by revision against the store's
  own vocabulary. ADR 0001's first assumption recomputes to **91 of 92,
  0.9891** (was 0.9875) — all 12 new predicates and 12 expiry conditions parse.
  ADR 0015's third assumption remains the one unreadable row, still a finding.

---

## Phase 7 progress log

One line per component, appended as it lands. Written so a session that dies
mid-phase can be resumed from the last line rather than from a diff.

- `877c6f9` **EST-0008 logged** — 5.5h active, 18.0h blocked, confidence 0.50,
  uncorrected for the eighth time. `agent-implementation` recomputed from both
  JSONL files at **n = 4**: 1.31× / 2.18× / 1.41× / 1.79×, all over, geomean
  1.64×, σ 0.40, **CV 0.24**. The irony is explicit — this phase builds the
  component that refuses below n = 5 while its own class sits at n = 4 — and it
  changes nothing: the threshold is a fact about the sample, not the calendar;
  the dispersion argues against correcting even at n = 5; the one real
  sub-population question (these three components call no model, the prior four
  phases' agents all did) is priced into confidence rather than quantity; and
  `EST-0009` becomes the first estimate `BiasDetective` can legitimately speak
  about. Branch `feat/phase-7-calibration-math` cut from `main` at `d91a864`.
- `d2eeed4` **`calibration_history` narrows in SQL** — merged `--no-ff` as
  `2c414bb`. Found by sketching Phase 8's fusion query against the Phase 6
  shape, which is what that sketch is for. Phase 6 wrote both narrowings and
  applied them in a generator over `fetchall()`: every answer right, every test
  green, and `estimate_owner_work_class` never used — a full scan of both tables
  per question, in the one read `BiasDetective` runs per group and
  `FusionBridge` will run per assumption. Composed from literal fragments with
  bound values rather than `(? IS NULL OR ...)`, which the planner cannot index.
  `calibration_query` is public so the **plan** can be asserted rather than
  described, with the unnarrowed case as the control that makes the assertion
  mean something. 10 new tests, 27 in the file.
- `6e04e96` **`distribution.py`** — merged `--no-ff` as `2740fa7`. The arithmetic
  under every calibration number, in a module that knows nothing about
  estimates, work classes or a store, so hypothesis hands it a distribution
  directly and the properties are about arithmetic rather than about a fixture.
  A ratio is multiplicative, so the centre is a **geometric mean**, the spread a
  standard deviation of **logarithms**, and the band one you divide and multiply
  by — 0.5 and 2.0 cancel to exactly 1.0, where an arithmetic mean would report
  a 25% bias that does not exist. Three constants carry their reasoning and a
  test each: one log sigma not two (a 95% band over five points is precision
  about an unmeasured tail), a **prediction** band so the deviation is not
  divided by √n, and `n-1` so a small sample is not flattered. Confidence
  multiplies a sample term by an agreement term, hits exactly 0.5 at the
  threshold, and can never return 1.0. `Decimal` throughout inside a **pinned**
  `localcontext`, with a test that moves the ambient precision and asserts
  nothing changes. 33 tests, module at **100%**.
- `7c9b7ba` **`BiasDetective`** — `praxis/agents/bias.py`, 44 tests, module at
  **100%**, merged `--no-ff` as `de8a5c1`. Most of it is about refusing, and
  that is the product: against this repository's own history it declines on
  every class. **The threshold takes no argument** — a test reads the signature
  to say so. **Below it the factor is not computed, not withheld**: a property
  test replaces `spread_of` with a function that raises and asserts every
  sub-threshold sample still returns, so there is no hidden number for a later
  refactor to print. Dispersion widens the band and never refuses. An
  unclassified group is reported as its own row and never summarised, and
  `factor_for` short-circuits **before** the read — checked by breaking the
  store. Four verdicts, not a boolean. **Phase 8's fusion query is executed
  here, not sketched**: `factor_for` is one indexed read plus O(n) arithmetic,
  returns a populated object in every case so an absence is never an exception,
  and a test renders ARCHITECTURE.md's own sentence off its fields. A property
  found a real defect — `magnitude` keyed on the direction rather than the
  factor printed below one inside the neutral band. Added to
  `DETERMINISTIC_MODULES`, so invariant 3 is checked against the file.
- `808bc84` **ADR 0024** — merged `--no-ff` as `064cd68`. Dispersion widens the
  band and never refuses; `n` is the only threshold and takes no override. Nine
  rejected options including the three genuinely tempting ones. **EST-0007's
  objection is answered twice over and the ADR says which mechanism does the
  work**: at n = 2 the *threshold* refuses, so the interval gets no credit for a
  save it did not make — though at those two points it would have given a band
  of 1.18x–2.43x, wide enough that nobody commits to 1.75x. Unclassified rows
  are in no group and get their own row, because "fourteen estimates carry no
  class" is an instruction and an empty table is not. **`README.md` corrected in
  the same commit**: its "n=14, CI [1.4, 2.3], confidence 0.79" was half right —
  the band is exactly one log sigma (σ 0.2513), the confidence is 0.59 under the
  shipping formula. The document moved to the code's number, not the reverse.
  ADR 0001's first assumption recomputes to **96 of 97, 0.9897** (was 0.9891).
- `c642439` **`CalibratorAgent`** — `praxis/agents/calibrator.py`, 26 tests,
  module at **100%**. **Pass-through designed as the primary path**, because it
  is: six of this project's own seven estimates would take it, so a design
  treating correction as normal has the frequencies backwards. It comes back
  fully populated with the detective's verdict and a sentence saying in as many
  words that it is correct rather than a stage that failed, and it gets as many
  tests as the correction path. Traceability is held in its **strongest** form —
  a property recomputes the corrected figure from the factor the result cites,
  so an explanation quoting one number while the arithmetic used another would
  fail rather than read plausibly. A correction is always positive: at four
  places a small enough estimate times a small enough factor rounds to zero, and
  a zero estimate predicts nothing, so it is held at the floor.
- `9da7c82` **`CalibratorAgent` becomes deterministic** — merged `--no-ff` as
  `98ad5b1`. ADR 0025. Out of ADR 0006's `extract` row, into `NON_LLM_AGENTS`.
  **Phase 7 is now the first phase where no component calls a model at all.**
  The explanation has no free variables — every value is read off a `Spread`
  before the sentence exists — so a model adds nothing and one failure mode:
  a fluent paragraph citing 1.7x beside a stored 0.61x reads *better* than the
  correct one and no test separates them. Fourth time this codebase has drawn
  this boundary, fourth time it moved a component to the arithmetic side. ADR
  0006 **amended, not superseded** — its real decision is untouched, so the
  Phase 2 list stays with a pointer and `praxis doctor` is the live answer.
  `doctor` passes. ADR 0001's first assumption: **100 of 101, 0.9901**.
- `c3ac70d` **`ScoringAgent`** — `praxis/agents/scoring.py`, 26 tests, module at
  **100%**, merged `--no-ff` as `5cf1a00`. **The backtest is prequential**: each
  row is scored using only the rows before it, because a factor fitted over a
  whole history and applied inside it has already seen the answer it is graded
  on. Error in log space, so 2x over and 2x under are the same miss. **`graded`
  is a field**, because a backtest that scored nothing and one that scored badly
  both print 0.0 and only one is a grade. Three known-answer histories keep it
  honest: a consistent 1.8x under-estimator is corrected on every scored row
  (mean log error 0.58 → 0.03), a well-calibrated estimator is never "improved"
  — the control that stops the first test measuring its own arithmetic — and
  undirected scatter scores no better than chance. **Against this project's own
  seven outcomes it scores exactly nothing**, and that is the finding: walking
  forward no group ever reaches n = 5, so no correction is ever formed to grade.
  A test makes the next phase concrete — with OUT-0008 the class reaches five,
  and the estimate *after* that is the first this project will ever have
  backtested. `grouped` and `precise()` promoted out of `bias`/`distribution`
  rather than copied. A hypothesis run found a real precision floor (a factor of
  0.0001 has nothing underneath it, so its band rounds to a point); recorded
  with its own test rather than papered over.
- `979a5d2` **`calibrate_store`** — `praxis/agents/calibration.py`, 32 tests,
  module at **100%**, merged `--no-ff` as `22867a2`. **A store pass, not a
  per-document step**: calibration runs over accumulated history, so a store
  that has ingested nothing since the last run can still answer differently
  because an outcome landed. **Zero model calls, reported as zero** rather than
  omitted. Writes only on a change, compared on the prosecution text — a second
  pass writes no version and no audit row, a moved factor writes version 2 with
  version 1 still readable, and the third pass is quiet again.
  **Scope item 4 answered**: no migration, and `Finding` is *not* the carrier of
  the factor. `subject_id` is one graph node and a factor belongs to a *group*,
  so a finding is filed against an **anchor** — the group's lowest estimate id,
  stable as the group grows — with the prosecution naming the group first so the
  anchor is never read as the biased row. What `Finding` cannot hold is the
  *query*: `prosecution` is prose, so Phase 8 recomputes through
  `BiasDetective` instead, which also means the factor can never go stale.
  A calibrated estimator raises nothing: "nothing is wrong" in the same queue as
  a breach is how a queue stops being read. Severity is graded on `magnitude`,
  so over- and under-estimation are symmetric.
- `de41689` **`praxis calibrate`** — `praxis/cli_calibrate.py`, 22 tests, module
  at **100%**, merged `--no-ff` as `4dbd52e`. Priced as a line item for the
  third phase running. **One command, two modes**: no arguments calibrates the
  *store* (and writes findings); `--owner/--work-class/--quantity` calibrates
  *that estimate* and writes nothing — asserted against the store, not the
  output. **It leads with the refusals**, named with the sample they wait on and
  sorted closest-first, because against this project's history every group
  refuses and a measured-only table would look broken. Unclassified estimates
  get their own line: a different fix from "one more outcome". A backtest score
  never prints without its denominator, and the zero model calls are printed
  rather than omitted. Tests found three real defects: `open_repository` is in
  `store.repository` not `store.location`; `get_settings` is `lru_cache`d so a
  CLI test that skips `cache_clear()` seeds the *previous* test's directory; and
  rich wraps at terminal width, so asserting on a sentence was pinning where the
  wrap fell rather than what was said.
