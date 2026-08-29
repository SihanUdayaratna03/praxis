# Phase 6 — Half B: the estimates a corpus states, and what happened to them

Half A asks what was decided and what it rested on. This phase asks what was
predicted and what actually happened, which is the other pillar Phase 8's fusion
stands on. Before it, `Estimate` and `Outcome` were Phase 1 schema definitions
that nothing wrote.

Three agents, a store pass, a corpus extension, a store read written for Phase 7,
an eval extension and one command. 25 commits, 220 new tests in eight new files,
252 new tests overall, every new module at 100%.

---

## The result worth leading with

**The fusion chain runs end to end for the first time.**

Phase 5 shipped `measured_in` and could not exercise it. It binds an identifier
only where an assumption's `estimated_as` edge reaches an estimate that an
`Outcome` resolves, and nothing wrote an `Outcome`. The handover called it
"mechanism waiting, not mechanism failing" and said the next session should
*check* rather than assume.

Checked. Three tests in `tests/agents/test_estimation.py::TestTheFusionChain`
run it with the outcome written by the real `OutcomeMatcher` rather than by hand:

1. a matched outcome binds `search_index_weeks = 7`, the quantity the predicate
   `search_index_weeks <= 4` names;
2. `AssumptionMonitor` then breaches `A-0001` on it — by arithmetic, ADR 0019,
   because a model cannot reach `BREACHED`;
3. an `unresolved` outcome binds nothing and breaches nothing.

The third is why the first two are not a false positive. A missed estimate now
reaches forward through an edge Half A wrote to invalidate the assumption a
decision rests on. That is the sentence the product exists to be able to say,
and it is now a passing test rather than a diagram.

**Both zeros the Phase 5 report had to explain are mechanism, not failure.** The
handover asked for that to be distinguished. It is: the monitor's store-derived
binding moves the moment something writes an outcome, and the formalization zero
is still upstream starvation — the citation gate, ADR 0016, unchanged.

---

## The decisions the phase turns on

**`match_quality` is arithmetic, and the module holding it cannot reach a
model.** The banding, the unit conversion and the candidate selection live in
`praxis/agents/reconciliation.py`, which imports no provider. Invariant 3 made
structural rather than remembered: the rule "this function must not call a
model" is exactly the kind that survives the commit stating it and dies three
phases later, and a module that *cannot* import a provider needs no rule.
[ADR 0021](../adr/0021-match-quality-is-arithmetic-not-a-judgement.md).

**An unmatched estimate is an `unresolved` outcome, never a silence.** Every
estimate leaves the matcher with exactly one row. Phase 1 designed the record
for this in its own words — an unresolved outcome exists so estimates that never
resolved stay visible instead of dropping out of the sample, "which is how a
curve ends up flattering its estimator".
[ADR 0022](../adr/0022-an-unmatched-estimate-is-an-unresolved-outcome.md).

**Work class is assigned by revision, against the vocabulary the store already
holds.** The failure worth preventing is not a wrong class — that is visible and
somebody fixes it — but *two spellings of one*, which is invisible and turns one
estimator's ten migrations into two sets of five under a detective that refuses
below `n = 5`.
[ADR 0023](../adr/0023-work-class-is-assigned-by-revision.md).

### Phase 7's query, written and run rather than sketched

The brief asked for `OutcomeMatcher`'s output shape to be checked against the
query Phase 7 will run, the way Phase 1 checked the `Link` table against Phase
8's walk. `calibration_history` in `praxis/store/reports.py` is that query, it
executes, and it is tested against a store the pipeline really wrote — because a
query that answers a hand-built fixture and not the pipeline's own output would
have proved nothing.

It is one indexed join, and four properties of Half B's output are why:

- the pairing is a **column** (`outcome.estimate_id`), so this is a join and not
  a graph walk per estimate;
- an unmatched estimate is a **row**, so there is no outer join and no table of
  absences;
- units are reconciled **at write time**, so nothing downstream converts;
- `work_class` sits on the estimate, where `estimate_owner_work_class` indexes.

**Sketching it changed one thing, which is the point of sketching it.** Without
the unresolved row, `n` and the unresolved count come from two different shapes
and the honest denominator is the one that goes missing. That is what turned the
unresolved write from a nicety into ADR 0022.

---

## What shipped

| | |
| --- | --- |
| `praxis/agents/estimator.py` | `EstimateExtractor`. Scan tier, one call per window, cites through the existing gate |
| `praxis/agents/classifier.py` | `WorkClassifier`. Revises rather than writes; owns `work_class` and its spelling rule |
| `praxis/agents/matcher.py` | `OutcomeMatcher`. Blocking → one call → arithmetic |
| `praxis/agents/reconciliation.py` | The arithmetic half. No provider import, by design |
| `praxis/agents/estimation.py` | The three over a store, in dependency order |
| `praxis/store/reports.py` | `calibration_history` — Phase 7's query |
| `praxis/eval/estimation.py` | Grading the three |
| `praxis/cli_estimate.py` | `praxis estimates` |
| 3 prompts | `extract_estimates.v1`, `classify_work.v1`, `match_outcome.v1` |
| ADRs | 0021, 0022, 0023 |

**No schema migration, and none was needed.** Read out of `001_core.sql` and
`mapping.py` rather than assumed: the `estimate` and `outcome` tables, both
append-only trigger pairs, both indexes and every column already existed from
Phase 1. Schema stays at **version 4**, exactly as `EST-0007`'s condition said,
and the condition was checkable afterwards because it was stated as one.

**No ADR 0006 routing entry, and none was needed.** All three agents were
already in `_ROUTING` and already named in the ADR's assignment list. Checked,
not assumed — the same check `EST-0006` made and was right about.

**No synthesis extension, and none was needed.** `EstimateSightings`,
`ClassAnswer` and `OutcomeAnswer` all validate against `synthesise_answer`;
`Decimal`, the `Unit` enum, ordinals and quotes are handled at
`synthesis.py:328`. Checked before assuming a gap, as the plan said.

### The corpus gap that was real

The plan said to check `topics.py` and the answer key before assuming a gap, the
way Phase 5 found and fixed one. Checked: the estimate and outcome ground truth
was **already there** — `status_update` records both and joins them with
`resolves_item_id`, and `issue_export` records an estimate with no outcome, so
the unmatched case was already planted and the match rate already had a real
ceiling. None of that needed rebuilding.

The gap was `blocked_quantity`. No document stated it and neither expected-field
tuple graded it — the field this project added *because* `OUT-0001` proved it
was needed, where Phase 0's wall clock matched its estimate almost exactly while
the engineering was 2.3× over-estimated and an external block absorbed the
difference. A corpus grading only the effort figure would let an extractor look
perfect on exactly the case that motivated the split.

`Topic` gains `blocked_weeks` and `actual_blocked_weeks`, the status update
states both, both sides are graded, and three properties are held by tests: not
every estimate predicts a block, the blocked errors run in both directions, and
the graded outcome set covers both. `GENERATOR_VERSION` 2 → 3; `FORMAT_VERSION`
stays at 2, because adding expected fields does not change the key's shape.

---

## The metrics

`praxis eval` output, pasted rather than typed.

**corpus_seed**: 20260809 · **provider**: mock · all nine prompts at v1

### Extraction quality

| Kind | Found | Missed | Spurious | Distracted | Precision | Recall | F1 | Exact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| decision | 0 | 9 | 1 | 0 | 0.0000 | 0.0000 | 0.0000 | 0 |
| assumption | 0 | 13 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0 |
| estimate | 0 | 9 | 1 | 0 | 0.0000 | 0.0000 | 0.0000 | 0 |
| **outcome** | 0 | 3 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0 |

`outcome` is a row for the first time. Phase 4's version of that line said it
would be, once something could write one.

### Citation integrity

- Claims stored: **2**, refused **38** (integrity **0.0500**).
- Fabricated quotations **0.5250**; mis-attributions **0.1750**.
- By refusal: `fabricated_quote` 21, `empty_answer` 10, `mis_attributed_quote` 7.

### Estimation

| | |
| --- | --- |
| Estimates on the calibration axis | **1** of 1 (**1.0000**), 1 class, 1 not in the corpus's vocabulary |
| Work class agreed with the key | **0** of **0** the key could name (**0.0000**) |
| Estimates an outcome resolved | **0** of 1 (match rate **0.0000**), against **3** the corpus resolves |
| Unmatched, by cause | `model_found_none` — 1 |

| Stage | Found | Missed | Spurious | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- |
| resolution | 0 | 3 | 0 | 0.0000 | 0.0000 | 0.0000 |

### Cost per document

`0.000000` for all seven agents. The mock is free; the column exists so the
first live run has something to be compared against.

### What these numbers are, and what they are not

**The citation gate starves Half B, and it compounds.** This is the first risk
`EST-0007` named, and it fired exactly as written. The gate refused 38 of 40
claims offline (ADR 0016 — the mock draws a cited ordinal and a quotation
independently, so they agree only by chance), so **one** estimate reached the
store for the whole 16-document corpus. `OutcomeMatcher` can only match estimates
that were written, so it was asked about one estimate and matched none.

A match rate of 0 over a denominator of 1 is a floor measuring plumbing, not a
measurement of the matcher. Phase 5 found this gate starving a downstream agent
and reported it plainly rather than calling it a bug; the same is true here and
the same words apply. **The unit suites carry the real coverage** — 220 tests
across eight files, every Phase 6 module at 100%, including the paths this
corpus run never reaches.

**Class accuracy is `0 of 0`, and the denominator is the story.** One estimate
was stored and the answer-key pairing could not name it, so there is nothing to
be right or wrong about. The classified rate of 1.0000 beside it is the reason
the report prints a note ordering the two: an agent that classifies everything
wrongly scores 1.0 on the first and 0.0 on the second, and the higher number is
not the better one.

**The match rate's ceiling is 3 of 9 by construction, and the report says so.**
The corpus states nine estimates and resolves three, because an estimate nobody
ever wrote an actual for is the ordinary case in a real corpus. A matcher
scoring 1.0 would have invented six pairings.

**ADR 0001's own first assumption recomputes to 91 of 92 predicates parsing,
0.9891** against its threshold of 0.9 — up from 0.9875, with all twelve new
predicates and all twelve new expiry conditions parsing. The one unreadable row
is still ADR 0015's third assumption, and it stays a finding.

---

## Things that cost something

**A rich markup bug the tests found, not review.** `[partial]` in a console line
is markup to rich, which swallowed the one field on that line that was
arithmetic rather than prose. The band prints unbracketed now.

**A test that broke other tests.** Capturing CLI output by assigning
`console.file` pinned the module's console to a stale `sys.stdout` and silently
ate the output of every later test that ran a command through `CliRunner`. It
redirects stdout instead, and the docstring says why so the next person does not
re-introduce it.

**A `created_by` bug only an integration test could find.** `WorkClassifier` was
stamping its own name into `created_by` on revision. Each agent was correct in
isolation; the interaction was not. `EstimationPipeline` asks the store which
documents `EstimateExtractor` has already read, so every classified document
looked unread and would have been re-extracted, allocating a second estimate per
passage on every run. `created_by` names the agent that *produced* a record; the
audit trail names who wrote each version. `AssumptionFormalizer` already had this
right. Recorded in ADR 0023's rejected-options table because it is the argument
for the store-level test existing at all.

**A shadowed test helper that made every comparison a string comparison.** A new
`_field` returning a float was defined above an existing `_field` returning a
str; Python took the later one, and `"11" > "6"` is false. Renamed to
`_quantity`, with a comment saying why the names differ.

**A re-run claim that was too strong.** The classify and match stages cost
nothing on a second pass. Extraction skips a document only once it has *produced*
an estimate — one that yielded none is read again, because an append-only store
records writes and an attempt that wrote nothing leaves no trace to check. That
is the same limitation `praxis extract` has had since Phase 4 and the same cause.
The docstring was corrected rather than the test weakened, and the fix is in
`BACKLOG.md` with the reason it waits.

**A provenance line that had quietly stopped covering the table.**
`EXTRACTION_PROMPTS` named three prompts, and its own docstring warned against
letting a Half B version appear on a Half A table. `praxis eval` now runs both
halves and the three memory passes, so naming three prompts left two thirds of
the numbers unattributed — the failure the line exists to prevent, arrived at
from the other direction. Nine prompts now.

---

## The estimate

| | |
| --- | --- |
| `EST-0007` | **5.0h active**, 18.0h blocked, confidence 0.45 |
| `OUT-0007` | see `docs/dogfood/outcomes.jsonl` |
| Bias correction | **not applied**, for the seventh time |

### The refusal, restated

`agent-implementation` was at n = 3 when `EST-0007` was written, all three
over-estimates, ratios 1.31× / 2.18× / 1.40×. `BiasDetective` refuses below
n = 5 and so did its author, for three reasons recorded before the work started:

1. applying a factor the product itself would decline to compute, at the first
   moment it becomes tempting, is exactly the failure the Phase 12 demo exists
   to show;
2. the arithmetic said the refusal was paying — a factor fitted to the first two
   points (mean 1.75×) would have priced Phase 5 at 3.7h against an actual 4.6h,
   an over-correction, which `OUT-0006` predicted before `EST-0007` existed;
3. a fourth independent point is worth more to Phase 8 than one tuned number.

What *was* applied is scope correction, which is a different thing: the CLI
priced as a line item, the eval work priced as a module plus four edits, and each
agent's refusal vocabulary priced inside the agent.

### Where the estimate was right

Every checkable condition held. No migration and none written, schema still at
version 4. No routing entry added. Three ADRs and this report inside the phase.
No live API call and no key. The corpus already carried estimate and outcome
ground truth and was not rebuilt; the one real gap was `blocked_quantity`, which
the conditions predicted as "the one real gap" in those words. `match_quality`
computed rather than asked. The Half B pass placed before the monitor, and the
two Phase 5 zeros checked rather than assumed.

The three OUT-0006 corrections all paid:

- **the CLI was priced** and came in near its line item;
- **the eval work was priced as a module plus four edits** and needed exactly
  that — `praxis/eval/estimation.py` exists for the same reason
  `praxis/eval/memory.py` did;
- **each agent's refusal vocabulary was priced inside the agent**, and the
  refusal paths are again most of each file.

### Where it was not

`OutcomeMatcher` was priced at ~420 production lines as one module and became
two: `matcher.py` at 491 and `reconciliation.py` at 164. The split was the right
call — it is what makes invariant 3 structural — but it was not foreseen, and it
is the *same shape* of miss `OUT-0004` and `OUT-0006` both recorded: a component
described as one thing having a subject of its own inside it. Three phases
running. The lesson is not "price more lines"; it is that **the boundary between
what is arithmetic and what asks a model is worth pricing as its own item**,
because in this codebase it keeps turning out to be a module.

The full accounting is in `OUT-0007`.

---

## For the session that writes `EST-0008`

**`agent-implementation` reaches n = 4 with this outcome. One short.**

That makes Phase 7 the first estimate in this project that *could* legitimately
be corrected — and the first where refusing would need a new reason rather than
the same one. Read the four ratios together before writing it, and read them out
of `outcomes.jsonl` rather than out of this paragraph.

**Phase 7 has its query already.** `calibration_history` returns rows ordered by
owner then work class then estimate id, so grouping is a walk rather than a sort,
and `CalibrationRow.resolved` is how to exclude the unresolved ones — the
decision to exclude them is the caller's, and both counts come from one read.

**`BiasDetective` and `ScoringAgent` are in `NON_LLM_AGENTS` and `praxis doctor`
enforces it.** Run against this project's own history they will refuse on every
class. That is correct behaviour and the Phase 7 report should say so plainly
before anyone reads it as a failure — the same sentence the Phase 5 handover
wrote about this phase, and it was right.

**The offline floor has not moved and will not until a key exists.** Every
model-dependent number in the table above measures plumbing. The first live run
is set up to be a measurement rather than a surprise (ADR 0005 assumption 3), and
that is still the largest unmeasured thing in the project.
