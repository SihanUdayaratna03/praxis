# Phase 7 — calibration maths: where Half B's numbers become a verdict

Half B has been extracting and structuring since Phase 6: estimates, the kind of
work each covers, and what actually happened to them. This phase is where those
rows turn into an answer — a factor per estimator per class of work, a
correction that mostly declines to correct, and a backtest that asks whether
correcting would have helped.

Three components, a store pass, a CLI command, an eval extension, two ADRs and a
store fix. 25 commits, 9 merges, **199 new tests in seven new files** plus 10
added to an existing one, every new module at **100%**.

**This is the first phase in this project where no component calls a model at
all**, and one of the two ADRs is about how it got that way.

---

## The result worth leading with

**The manual discipline and the code now agree, and the property tests prove
it.**

For eight consecutive estimates this project has hand-simulated one rule:
refuse below `n = 5`, don't force a correction. Every refusal was recorded with
its reason in `docs/dogfood/estimates.jsonl`. Until this phase, that was a
habit. Now it is a function, and `tests/agents/test_bias.py` holds it as a
property over generated distributions rather than as a handful of examples:

> No sample smaller than the threshold produces a factor, for any distribution
> of inputs — tight, scattered, or entirely unanswered.

Three things make that claim worth more than it looks.

**It is checked in both directions.** A refusal property is satisfied trivially
by a detective that refuses everything, so there is a paired property asserting
that a sample at or above the threshold *does* speak. Without it, the headline
claim would be unearned in exactly the way the phase estimate's third risk
predicted.

**The factor is not computed below the threshold, not computed and withheld.** A
property test replaces `spread_of` with a function that raises and asserts every
sub-threshold sample still returns normally. "Not computed" and "computed and
hidden" look identical in a passing suite and differ entirely in what a future
refactor can expose.

**The threshold takes no argument, and a test reads the signature to say so.**
`summarise(group, rows)` — there is no keyword to pass, no default to change at
a call site. A number that whoever wants an answer can lower is not a threshold;
it would be 3 within a quarter and the record would not show it happened.

Run against this repository's own history, `BiasDetective` declines on every
class. That is the correct output, and it is the first thing `praxis calibrate`
prints.

---

## The decisions the phase turns on

### Dispersion widens the band; only `n` refuses — [ADR 0024](../adr/0024-dispersion-widens-the-band-and-only-n-refuses.md)

The one decision that could reasonably have gone the other way, and the scope
brief asked for it explicitly.

Sample size and scatter are different facts and get different treatment. Too
little evidence means silence, absolutely. Scatter is a fact about the
*estimator* — and an erratic estimator is precisely who needs telling, so a wide
sample gets a wide interval and a low confidence, never a second refusal on a
second magic number that has no story surviving being asked twice.

**`EST-0007`'s worked example is answered twice over, and the ADR says which
mechanism does the work.** It argued that a factor fitted to the mean of `1.31x`
and `2.18x` would have mispriced Phase 5. Under this design the *threshold*
answers that — at `n = 2` nothing is emitted at all. The interval is what would
have answered it had `n` been sufficient: those two points give a band of
`1.18x` to `2.43x`, wide enough that nobody reading it commits to `1.75x`.
Saying which one did the work matters, because a design that credited the
interval with a save the threshold actually made would be untested where it
counts.

Everything is computed in **log space**, because a ratio is multiplicative. An
estimator twice over on one job and twice under on the next is on average
exactly right, and the arithmetic mean of `0.5` and `2.0` is `1.25` — a 25% bias
that does not exist. Three constants carry their reasoning and a test each: one
log sigma rather than two (a 95% band over five points is precision about an
unmeasured tail), a *prediction* band so the deviation is not divided by `√n`,
and `n-1` so a small sample is not flattered.

**An unclassified estimate is in no group.** `work_class` is a grouping key, so
`unclassified` is the absence of one; pooling those rows would compute a factor
across migrations, refactors and incident response and call it a person's bias.
They are reported as their own row — "fourteen estimates in no class" is what
somebody can fix this afternoon — and never summarised. `factor_for`
short-circuits before the read, which a test proves by breaking the store.

### The calibrator explains rather than generates — [ADR 0025](../adr/0025-the-calibrator-explains-rather-than-generates.md)

`CalibratorAgent` was assigned to the `extract` tier in Phase 2, before any
agent existed. On the information available then that was reasonable: "explain
an adjustment in plain language" sounds like a writing task.

Building it showed the output has no free variables:

> `5.5 hours becomes 3.3649 hours. claude-opus-5's last 5 resolved estimates of
> agent-implementation work ran 1.6345x over, so this is scaled by 0.6118x.
> Ordinarily between 2.7522 and 4.1146 hours. n=5, confidence=0.4163, computed
> from 5 resolved estimates with a log-space spread of 0.2011.`

Every value is read off a `Spread` before the sentence exists. A model adds
nothing and adds one failure mode, and it is the worst kind: a fluent paragraph
citing `1.7x` beside a stored factor of `0.61x` reads *better* than the correct
one, and no test separates a well-written wrong number from a well-written right
one.

**ADR 0006 is amended, not superseded.** Its actual decision — agents ask for a
role, one file holds every model id — is untouched and correct, and retiring a
good record to change one row of a list would destroy the provenance this
directory exists to keep. The Phase 2 list stays with a note pointing here.

This is the **fourth** time this codebase has drawn the arithmetic/judgement
boundary and the fourth time it moved a component to the arithmetic side, after
`VerifierAgent`, `match_quality` (ADR 0021), and the two agents
`NON_LLM_AGENTS` named from the start.

### `Finding` can carry the allegation but not the query

Scope item 4, checked against `praxis/domain/records.py` and `001_core.sql`
before any migration was considered — the same check Phase 6 ran for `Estimate`
and `Outcome`, and found unnecessary. **No migration**, and two conclusions
rather than one.

`Finding.subject_id` is a `NodeRef`: one graph node. A calibration factor is a
property of an `(owner, work_class)` **group**, and no such node exists. So a
finding is filed against an **anchor** — the group's *earliest* estimate, which
is stable as the group grows, where the most recent would move on every write
and turn one continuing allegation into a trail of findings about different
rows. The prosecution names the group in its first clause, so nobody reads the
anchor as "this estimate is the biased one".

`FindingKind.CALIBRATION_BIAS` has existed since Phase 1 and `evidence_span_ids`
is already documented as empty for exactly this case, so the record holds the
*allegation* well. What it cannot hold is the *query*: `prosecution` is prose,
and Phase 8 asking for the factor, `n` and the confidence in one call against a
text column would be a parse. So the finding is what a person reads, and
`BiasDetective` recomputing from `calibration_history` is what `FusionBridge`
calls — which also means a stored factor can never go stale, and a stale
calibration factor is precisely the failure this product exists to catch.

### Phase 8's fusion query, executed rather than sketched

`ARCHITECTURE.md` describes the mechanism as: given a work class and an owner,
get the factor, `n` and the confidence. That is `factor_for(owner=,
work_class=)` — one indexed read plus `O(n)` arithmetic, walking nothing. It
**never returns `None` and never raises**, so Phase 8 never has to tell an
absence from an error, and a test renders the document's own sentence off its
fields:

> `migration work is 1.8000x under, n=6, confidence=0.5455`

**Sketching it is what found the bug it exists to find.** Phase 6 wrote both
narrowings of `calibration_history` and applied them in a generator over
`fetchall()`. Every answer was right, every behavioural test passed, and
`estimate_owner_work_class` — added in Phase 1 for exactly this question — was
never used. A full scan of both tables *per question*, in the one read
`BiasDetective` runs per group and `FusionBridge` will run per assumption.

Fixed by composing the statement from literal fragments with bound values, and
`calibration_query` is public so the **plan** can be asserted rather than
described. Three tests: the narrowed query uses the index, the unnarrowed one
does not — the control without which the first proves nothing — and a class
without an owner falls back to a scan, recorded rather than repaired because it
is a left-prefix rule and no hot reader asks that way.

---

## What shipped

| Module | What it is | Tests |
| ------ | ---------- | ----- |
| `praxis/agents/distribution.py` | Geometric mean, log-space spread, band, confidence. Takes a list of `Decimal` and knows nothing else | 34 |
| `praxis/agents/bias.py` | `BiasDetective`: groups, refuses, or measures | 44 |
| `praxis/agents/calibrator.py` | `CalibratorAgent`: applies a factor, or explains why it did not | 26 |
| `praxis/agents/scoring.py` | `ScoringAgent`: a prequential backtest | 26 |
| `praxis/agents/calibration.py` | The three over a store; writes only on a change | 32 |
| `praxis/cli_calibrate.py` | `praxis calibrate` | 22 |
| `praxis/eval/calibration.py` | The fourth quarter of the metrics table | 15 |
| `praxis/store/reports.py` | The Phase 6 read, narrowing in SQL | +10 (27 in file) |

`praxis/agents/distribution.py` is split from `bias.py` on the line this phase
draws. `reconciliation.py` made the same split in Phase 6 to keep a provider out
of reach; there is no provider anywhere here, so the split buys something else
and it is worth naming: a total function over a list of numbers can be handed a
distribution by `hypothesis` directly, so the properties this phase rests on are
claims about arithmetic rather than claims about a fixture that happened to be
built a particular way.

`Decimal` throughout — `ln`, `exp` and `sqrt` inside a **pinned** `localcontext`.
Invariant 4 names money and applies with more force here: `Decimal` at a fixed
precision is what makes "re-running over an unchanged store gives an identical
answer" testable rather than hoped for. The precision is pinned because
`Decimal.ln` reads a thread-local global, and one test changes that global and
asserts nothing moves.

### Calibration is a store pass, not an ingestion stage

It operates over accumulated history rather than over one document, so there is
nothing per-document to iterate — a store that has ingested nothing since the
last run can still answer differently, because an outcome may have landed. A
test runs it against a store with no documents at all.

It writes only on a change, the rule `praxis.monitor.run` established. The
comparison is on the prosecution text, which carries the factor, the band, `n`
and the confidence, so any movement in the numbers is a movement in the text.
Three tests hold it in both directions: a second pass writes no version and no
audit row, a new outcome that moves the factor writes version 2 with version 1
still readable, and the third pass is quiet again. Re-runnability is not a
one-shot property.

It costs **zero model calls**, and reports that zero rather than omitting it: a
cost column with a missing row reads as unmeasured, not as free.

---

## The metrics

From `praxis eval` against a freshly generated corpus, pasted rather than typed.

### Calibration

| | |
| --- | --- |
| Groups read | 1 |
| With enough history to speak | **0** (0.0000) |
| By verdict | 1 `no_resolved_outcomes` |
| Refusal threshold held | **yes** |
| Pass-through fired on exactly the refusing groups | **yes** (1 passed through, 0 corrected) |
| Calibration findings standing | 0 |
| Backtest | **nothing to score** — 0 resolved estimates, none in a group that reached the threshold |

### The other three quarters, unchanged from Phase 6

| | |
| --- | --- |
| Citation integrity | 0.0500 — 38 of 40 claims refused |
| Fusion edges | 0 of 3 |
| Estimates on the calibration axis | 1 of 1 |
| Match rate | 0.0000 of 1, against 3 the corpus resolves |
| Cost per document | 0.000000 for every agent |

### What these numbers are, and what they are not

**Every zero in the calibration table is a correct zero, and they have two
different causes that must not be confused.**

The first is the same starvation Phases 4, 5 and 6 all reported. The citation
gate refuses 38 of 40 claims against the mock (ADR 0016), so one estimate
reaches the store for the whole corpus and everything downstream is starved.
That is a measurement of plumbing, not of a model.

The second is **not** starvation and must not borrow that excuse. Even with a
fully populated corpus, this corpus resolves three outcomes across nine
estimates — so no group would reach five, and `BiasDetective` would still refuse
on all of them. A run reporting a factor against a corpus this size would have
**broken the threshold, not beaten it**. The report's calibration section leads
with that sentence rather than following it, because a reader who meets a column
of zeros before the explanation has already formed the wrong conclusion.

**What the eval actually grades here is internal consistency, and that is
deliberate.** There is no answer key: the corpus labels estimates and outcomes,
and a calibration factor is not something a document can state, so there is
nothing to compare against. Approximating a ground truth would mean computing it
with the same code being graded. So the graded claims are that the threshold
held (in both directions), that the pass-through fired on exactly the groups
that refused (equality, not "mostly"), and that the backtest travelled with its
denominator.

**Model routing: no entry was added, and one was removed.** None of the three
components needs a provider call. `CalibratorAgent` moved out of the routing
table entirely, which is ADR 0025 and a real change rather than an empty row.

---

## Things that cost something

**A property test found a real defect in `magnitude`.** The prose reading of the
factor — "1.64x over" rather than "0.61x" — was keyed on the *direction* rather
than on the factor. Inside `NEUTRAL_BAND` the direction is `none` while the
factor is still a shade under one, so the field whose entire job is to be above
one came back at `0.9502`. A hand-written example would have skipped that case
exactly.

**Hypothesis found a real precision floor.** `REPORTED_PLACES` is four decimals,
so a factor near `0.0001` has nothing underneath it and its band rounds up onto
the centre however wide the sample scatters. Recorded with its own test rather
than papered over, and left alone on purpose: more places would print precision
the sample does not have, and refusing small factors would be a second threshold
on magnitude with no story behind it.

**The CLI tests found three defects, none in the CLI.** `open_repository` lives
in `praxis.store.repository` and not in `location`; `get_settings` is
`lru_cache`d, so a CLI test that invokes `init` without clearing it seeds a
store in the *previous* test's directory; and rich wraps at terminal width, so a
test asserting on a sentence was pinning where the wrap fell rather than what
was said.

**Two helpers were promoted rather than copied.** `grouped` out of `bias.py` and
`precise()` out of `distribution.py`. Two copies of a grouping walk are two
chances to split a group differently, and a backtest grouped differently from
the factor it grades measures something nobody asked for. Two modules at two
decimal precisions would put a rounding difference underneath a result.

**One helper was written and deleted.** `group_names` in `calibration.py`, unused
speculative surface for a CLI that had not been built yet.

**`README.md` was corrected, and the direction matters.** Its illustration —
"1.8x under, n=14, CI [1.4, 2.3], confidence 0.79" — turned out to be half
right: the band it quotes *is* exactly one log sigma (σ = 0.2513). The
confidence is not; those numbers give **0.59** under the formula now shipping.
The document moved to the number the code produces, rather than the formula
being tuned to match a sentence written before there was one.

---

## The estimate

`EST-0008`: **5.5h active**, 18.0h blocked, confidence 0.50, uncorrected for the
eighth time.

### The irony, and why it changed nothing

After `OUT-0007` closed, `agent-implementation` stood at **n = 4** — one short of
the `n = 5` threshold that `BiasDetective`, the component *this phase builds*,
refuses below. The estimate had to say whether that changed the pricing. It
said no, in four parts:

1. **The threshold is a fact about the sample, not the calendar.** `n = 4` is
   `n = 4` whether or not the code that would decline it exists yet. A rule that
   bends in the phase that implements it was never a rule — and this is the
   phase whose property tests are supposed to *prove* the manual discipline and
   the code agree. They cannot prove it about an author who just made an
   exception for himself.
2. **The dispersion argues against correcting even at `n = 5`.** The four ratios
   scatter 1.31x–2.18x, CV 0.24. A mean-fitted 1.67x on 5.5h gives 3.3h while
   the sample's own range gives 2.5h–4.2h — a spread over half the point
   estimate. Pricing this phase on the mean would commit, in the estimate
   itself, the exact error ADR 0024 was being written to reject.
3. **There is one real sub-population question and `n = 4` cannot answer it.**
   All four prior `agent-implementation` phases built agents that call a model;
   these three do not. That removes the largest variance source of Phases 3–6
   (model behaviour, prompt iteration, citation-gate starvation) and adds a
   different one (hypothesis finding counterexamples that force a design
   change). Splitting on that axis leaves `n = 4` and `n = 0`. So it was priced
   into **confidence** — 0.50, up from 0.45 — and not into the quantity.
4. **The arrival is worth more than the correction.** When `OUT-0008` closes,
   `agent-implementation` reaches `n = 5`.

That fourth point is now executable rather than rhetorical. A test in
`tests/agents/test_scoring.py` walks this project's own history forward and
asserts that it scores **exactly nothing** — no group ever reaches the
threshold, so no correction is ever formed to grade — and a second test adds the
eighth and ninth outcomes and shows the ninth is the first row that gets
backtested.

**`EST-0009` is the first estimate in this project's history that
`BiasDetective` can legitimately speak about.** Phase 7 builds the component;
Phase 8 is the first phase it can price.

### The dogfood backtest, as a curiosity and not a result

Seven outcomes across four classes, with the only multi-point class at four.
A backtest showing a correction "would have helped" on that is noise with a
decimal point on it. The honest answer the code produces without being told to:
`considered = 7`, `scored = 0`, `graded = false`.

The log is read by the *test*, not by anything in `praxis/`. The package has no
business knowing where this repository keeps its own diary, and Phase 12 is
meant to ingest those files through the ordinary path.

---

## For the session that writes `EST-0009`

Three things that will matter.

**The threshold will be crossed, for the first time.** `OUT-0008` takes
`agent-implementation` to five. `EST-0009` is therefore the first estimate this
project can legitimately apply a factor to — and applying it is not
automatically the right call. The band and the confidence exist to be read: at
`n = 5` the confidence caps at 0.5 by construction, which is the design saying
"treat this as a coin-flip-grade signal". Whatever `EST-0009` does, it should
run `praxis calibrate --owner claude-opus-5 --work-class agent-implementation
--quantity <raw>` and quote what came back, because that is the product
speaking about its author for the first time and the Phase 12 demo is built out
of exactly these moments.

**Price the arithmetic/judgement boundary again, and expect it somewhere new.**
It has now moved four phases running. In Phases 3–6 it fell between a model call
and the arithmetic around it. In Phase 7 there was no model call, so it fell
between *computing* a factor and *deciding whether to speak it* — and that
turned out to be a module-sized subject, exactly as `EST-0008` priced it. Phase
8 has both halves in one component for the first time, so the boundary is likely
to fall between *recognising* that an assumption is an estimate (judgement) and
*evaluating* the calibrated value against the predicate (arithmetic, and ADR
0019 already says only arithmetic can breach).

**Blocked time is still not an engineering prediction.** Five attempts now, all
missed, in both directions: 20.0/12.8, 16.0/72.3, 24.0/17.3, 18.0/0.9, and
`OUT-0008` will make six. It measures when the author sleeps. Log it because the
record has the field, and compare active against active — which is, not
coincidentally, exactly what `ScoringAgent` was built this phase to do.
