# Phase 4 — Half A: the agents that read a decision out of a document

> Merged as [#11](https://github.com/SihanUdayaratna03/praxis/pull/11), tagged
> `v0.4-phase-4`. 1541 tests, 98.46% coverage, CI 5/5 green.

Phase 3 turned documents into spans that can be trusted. Phase 4 turns spans
into records — a decision, the assumptions it rests on, and the estimate hiding
inside one of those assumptions — and then measures how well it did it.

The phase was re-scoped at its start. `NEXT.md` had assigned Phase 4 to
orchestration; the owner moved that later and made this Half A's agents plus
the first evaluation harness. [ADR 0004](../adr/0004-custom-async-orchestrator.md)
still stands unchanged; only its date moved.

---

## The decision the phase turns on

**An agent cites a passage by its number in a list it was shown.** Not by span
id, not by byte range, not by quoting and hoping.

`praxis/agents/offering.py` builds a numbered listing of the spans an agent may
read from, and every extracted claim carries the ordinal it came from. The
resolution back to a `span_id` happens in code the model cannot reach. See
[ADR 0015](../adr/0015-extraction-cites-spans-by-offered-ordinal.md).

The same argument as ADR 0011 one level up: a fabricated byte offset is not a
thing the pipeline can express, so invariant 6 does not rest on a checker
catching every fabrication. The checker runs anyway —
`praxis/agents/citation.py` is the single gate, and **the order of its checks
is the substance**:

1. An ordinal that was never offered beats everything downstream. The agent
   answered about a passage it was not shown, and nothing about the quotation
   is worth reading.
2. A span that will not resolve beats a bad quotation. This one was learned the
   hard way, below.
3. Only then is the quotation judged, and a quotation found in *some other
   offered passage* is named `MIS_ATTRIBUTED_QUOTE` rather than lumped in with
   a fabrication — because "the model read the right document and cited the
   wrong paragraph" and "the model invented a sentence" call for two different
   fixes.

That vocabulary is the reason the metrics table below can say something useful
about a run in which almost nothing was extracted.

---

## What shipped

| Module | Lines | What it is |
| ------ | ----: | ---------- |
| `prompts/library.py` | 177 | Versioned prompt files, read as packaged resources |
| `agents/offering.py` | 274 | The numbered listing every extraction cites through |
| `agents/citation.py` | 152 | The one gate every claim's citation passes |
| `agents/errors.py` | 74 | The refusal vocabulary, and what each one costs |
| `agents/scout.py` | 299 | `DecisionScout` — which windows hold a decision |
| `agents/structurer.py` | 412 | `DecisionStructurer` — a candidate span to a `Decision` |
| `agents/extractor.py` | 564 | `AssumptionExtractor` — and the first `estimated_as` edge |
| `agents/extraction.py` | 375 | The three, in order, over a whole store |
| `agents/results.py` | 133 | What a run wrote, and everything it lost |
| `eval/matching.py` | 276 | Which record answers which item. Byte overlap, not equality |
| `eval/metrics.py` | 211 | Arithmetic over the pairs. No model, ever |
| `eval/harness.py` | 269 | A corpus end to end, graded from what SQLite holds |
| `eval/report.py` | 229 | The table and the JSON. No arithmetic |
| `cli_eval.py` | 267 | `praxis extract` and `praxis eval` |

Plus migration 004 (`prompt_id` and `prompt_sha` on `llm_trace`), four prompt
texts, and ADRs [0014](../adr/0014-prompts-as-versioned-stored-artefacts.md),
[0015](../adr/0015-extraction-cites-spans-by-offered-ordinal.md) and
[0016](../adr/0016-the-extractor-writes-the-first-estimated-as-edge.md).

### Three things worth reading the code for

**The scout has no floor to degrade to.** Every other stage in this codebase
has one: a segmentation the model refused becomes one span per block, a
document that fails to parse is recorded as failed. The scout's equivalent
would be "assume every passage holds a decision", and that would pay the
structurer for the entire corpus. A window nothing was ever learned about is
recorded as **blind** and reported as blind, because an unanswered question and
a negative answer are not the same fact and the metrics table has a column for
each.

**The extractor's window is 3 behind and 8 ahead**, which is 12 passages —
exactly ADR 0015's ceiling. It is asymmetric because assumptions are written
*after* a decision in both corpus shapes; reaching equally would spend half the
listing on a title block and still stop short of the effort section. Widening
either half breaches a recorded assumption rather than editing a constant,
which is the point of recording it.

**The pipeline settles three things that would otherwise be settled twice.**
Ids come from a counter seeded off the store, because one extractor call builds
three assumptions before any of them is written. A document that already holds
decisions is recognised rather than redone, because sequential ids would fork
it and content-addressed `Link` ids would collide. And the extractor runs once
per *verified* decision, so a refused one is never paid for twice.

---

## Evaluation, in four modules that fail four ways

`EST-0005` priced the harness as four line items rather than one, which is the
lesson `OUT-0004` paid for. It stayed four:

- **`matching`** decides *which* record answers *which* item. A match is byte
  overlap, not equality — the key's ranges are cut by construction and the
  spans by the block grid, and demanding identical coordinates would grade the
  segmenter's boundaries instead of the extraction. Pairing is greedy by
  overlap with a **total** tie-break, because a metric that depends on
  tie-breaking order is a metric two runs can disagree about.
- **`metrics`** counts the pairs, and does nothing else. Invariant 3 in its
  Phase 4 form. Two conventions are stated rather than inferred: precision over
  zero records is **0** and not the vacuous 1, because a table where a broken
  extractor ties with a perfect one is unreadable; recall over zero expected
  items is 1, which cannot flatter a failure because the corpus owns that
  denominator.
- **`harness`** runs a corpus end to end. **The claims are read back out of
  SQLite**, never taken from the run's result: the run is the pipeline's report
  of itself, and the store is what survived being written.
- **`report`** renders, and does no arithmetic. That is what lets a test assert
  the table says what the metrics say, which is a different claim from
  asserting the metrics are right.

Distractor hits are counted apart from ordinary false positives. Without
labelled negatives only recall is measurable — [ADR 0012](../adr/0012-machine-gradeable-corpus-ground-truth.md)'s
argument — and counting them separately is what makes that label pay.

---

## The metrics

Produced by `praxis eval` over the default 12-document corpus at seed 20260809,
and pasted in rather than typed — which `EST-0005` made a condition of itself.

**corpus_seed**: 20260809 -- **extract_assumptions**: v1 -- **provider**: mock -- **scan_for_decisions**: v1 -- **structure_decision**: v1

| Kind | Found | Missed | Spurious | Distracted | Precision | Recall | F1 | Fields | Exact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| decision | 0 | 9 | 1 | 0 | 0.0000 | 0.0000 | 0.0000 | -- | 0 |
| assumption | 0 | 9 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | -- | 0 |
| estimate | 0 | 9 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | -- | 0 |

- Claims stored: **1**, refused: **29** (integrity **0.0333**).
- Fabricated quotations: **0.5667**; mis-attributions: **0.1667**.

| Refusal | Count |
| --- | --- |
| fabricated_quote | 17 |
| empty_answer | 7 |
| mis_attributed_quote | 5 |

`estimated_as` edges found: **0** of 3 the corpus labels (recall **0.0000**).

Documents graded: 12. Model calls: 49. Windows never answered about: 0.

### What these numbers are, and what they are not

**They are not a measurement of a model.** `PRAXIS_LLM_PROVIDER=mock` is the
default, no key exists, and `praxis/llm/synthesis.py` builds an answer by
walking the response schema: it draws the cited ordinal from the bracketed
labels in the prompt and the quotation from the passages **independently**. The
two agree only by chance, so the citation gate refuses almost everything. That
is the gate working exactly as designed on an adversary that fabricates 57% of
the time.

`EST-0005` predicted this in its risk note and said the report would have to
say so in those words. It does, and so does the table itself: `praxis eval`
prints the caveat beneath the numbers whenever the provider is offline, because
a metrics table outlives the conversation in which everyone knew what it meant.

**They are a measurement of the plumbing, and the plumbing reads clean.** Every
number above is a fact about the pipeline rather than about a language model:

- 49 model calls over 12 documents, none of them repeated for a document
  already extracted.
- 0 blind windows — the scout got a usable answer about every window it asked
  about.
- 1 claim survived the gate, and it was **spurious rather than a hit**: it
  landed on a passage the answer key does not mark. A pipeline that scored one
  false positive as a true positive would be a broken grader, and this is the
  run that shows the grader is not.
- 29 refusals, sorted into three named defects rather than one. That
  distribution is the baseline a real provider gets compared against, and it is
  the only reason the first live run will be legible.

**The floor is now measurable, which is the deliverable.** Phase 10's ablation
table needs a number that moves when a prompt changes. Producing 0.0000 with a
provider that answers at random is the correct reading of a floor, and the
harness that produced it is deterministic: two runs over one corpus render
byte-identical JSON, asserted in `tests/eval/test_harness.py` and again through
the CLI in `tests/test_cli.py`.

---

## Things that cost something

**A moved document is not a fabricating model.** `verify_claim` judged the
quotation before asking whether the cited span still resolved, so a document
that had been re-ingested at different offsets came back `ABSENT` and was
reported as `FABRICATED_QUOTE`. `SPAN_DOES_NOT_RESOLVE` existed in the
vocabulary and nothing ever returned it. The fix is an ordering: resolution is
asked about first. The cost of getting it wrong would have been a metrics table
blaming a model for a store's bookkeeping.

**Two extraction agents grew two copies of the same citation check**, and the
second copy was caught before it diverged. `agents/citation.py` is that lift.
The general shape is worth naming: the second implementation of a rule is the
last moment at which extracting it is free.

**The prompt library's own gate caught a prompt.** `tests/prompts/test_library.py`
pins every prompt's digest, so editing a shipped prompt in place fails rather
than silently re-labelling every trace that names it. It fired on the
assumption prompt during this phase, which is the only evidence that a
digest-pinning test is worth its maintenance.

**A provenance line has to be ASCII.** `praxis eval` prints a markdown table,
and a person redirecting it into a file on Windows gets the console codepage
rather than UTF-8 — so a middot separator arrived as a replacement byte, and
the corrupted line was the one naming the provider, the seed and the prompt
versions. The separator is now ASCII and a test encodes the whole rendering to
prove it. A table whose provenance line is mojibake is a table that cannot be
compared with the next one, which is the only thing a metrics table is for.

**Rich folds a markdown table at the console width.** Printed with
`soft_wrap=True` and no markup interpretation, so what the terminal shows is
byte-identical to what `--markdown` writes. The status lines (`wrote x`) go to
stderr, so `praxis eval corpus > table.md` produces the artefact and nothing
else.

---

## The estimate

| | |
| --- | --- |
| `EST-0005` | 6.0 hours active, 16 hours blocked, confidence 0.40, class `agent-implementation` |
| `OUT-0005` | see `docs/dogfood/outcomes.jsonl` |

### Where the estimate was right, and where it was not

| | Estimated | Actual |
| --- | ---: | ---: |
| Production lines | ~1,650 across the priced components | **3,799** |
| Test lines | ~700 for the harness, larger than each agent for the agents | **3,869** |

The four-line-item pricing of the eval harness — the correction `OUT-0004`
bought — landed close: `matching`, `metrics`, `harness` and `report` came to
998 production lines against roughly 550 predicted, against 1,205 lines of
tests where 700 were priced. Both are ordinary misses rather than the 2× that
a one-line-item pricing produced last phase, and the ratio between them is the
one the estimate got right.

The agents ran over. `EST-0005` priced them at roughly 200, 300 and 350
production lines; they came in at 299, 412 and 564. The direction is consistent
and the reason is the same in all three: the failure paths are where the lines
are, and there were more distinct ways to refuse a claim than the estimate
imagined. `agents/citation.py`, `agents/offering.py` and `agents/errors.py` —
500 lines between them — are not in the estimate at all, because when it was
written the citation gate was assumed to live inside each agent.

The conditions that were checkable held: migration 004 was the only schema
change, no live API call was made, three ADRs were recorded, and the metrics
table above is `praxis eval` output pasted in.

### The pattern, at n = 2

`agent-implementation` now has two outcomes rather than one, and it is the
first work class in this corpus with a history instead of a point.

| Phase | Class | Direction |
| --- | --- | --- |
| 0 | `scaffolding` | over 2.3× |
| 1 | `data-modelling` | under 2.1× |
| 2 | `llm-integration` | under 1.24× |
| 3 | `agent-implementation` | over 1.3× |
| 4 | `agent-implementation` | see `OUT-0005` |

**Still no bias correction, for the fifth time.** `BiasDetective` refuses below
`n = 5` and so does its author. Two points in one class is not a bias; it is
two points. The value of writing it down is that by Phase 8 or so one class
reaches five, and the demo becomes Praxis telling its author which way he
leans — which is worth more than any single phase's estimate being right.

---

## For the session that writes `EST-0006`

- **Price the failure paths, not the happy path.** Every agent in this phase
  overran, and every overrun was in the code that says no. An agent's happy
  path is one function; its refusal vocabulary is the rest of the file.
- **A rule's second implementation is the last free moment to extract it.** The
  citation gate was caught at two copies. Watch for the same shape in Half B —
  scoring, and whatever the outcome recorder ends up sharing with it.
- **Half B has no floor to measure against yet.** Half A's numbers are offline
  and therefore a floor. Half B's — calibration, scoring, bias detection — are
  deterministic arithmetic and will be *exact*, which is a different kind of
  claim and a much easier one to state. Do not price them like these.
