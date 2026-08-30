---
id: ADR-0024
status: accepted
date: 2026-08-30
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0024 — dispersion widens the band; only `n` refuses

## Chosen

`BiasDetective` has **one threshold**, on sample size, and it is absolute:
fewer than **five** resolved estimates with a usable ratio and no factor is
emitted, for any distribution of inputs, with no keyword to pass and no default
to change. Below it the arithmetic is **not run at all**, so no number exists on
the returned object for a later refactor to start printing.

Scatter is handled differently. A sample that clears the threshold is always
answered, and its spread is reported **beside** the factor as a multiplicative
band and as a confidence that falls with the scatter. There is no second
threshold on dispersion and no second refusal.

Four quantities come out together, and they are one frozen object rather than
four returns:

| | |
| --- | --- |
| Factor | Geometric mean of `actual / estimated` over the group's resolved rows |
| Band | `factor / e^σ` .. `factor · e^σ`, where σ is the sample deviation of the logarithms |
| Confidence | `n/(n+5) · 1/(1+σ)` — falls with a small sample and with a wide spread, independently |
| `n` and `considered` | The sample and the denominator, because "fourteen of nineteen answered" needs both |

**Everything is computed in log space**, because a ratio is multiplicative: an
estimator twice over on one job and twice under on the next is on average
exactly right, and the arithmetic mean of `0.5` and `2.0` is `1.25` — a 25%
bias that does not exist.

**The factor is `actual / estimated`**: the number you multiply a raw estimate
by. A second reading, `magnitude`, inverts it below one so prose can say "1.64x
over" rather than "0.61x", and a test binds the two so the arithmetic reading
and the spoken one cannot drift.

**An estimate whose `work_class` is `unclassified` is in no group.** It is
excluded from every group's `n`, reported as its own row with its own verdict,
and never summarised. Four verdicts exist — `measured`,
`insufficient_sample`, `no_resolved_outcomes`, `unclassified` — because they are
acted on differently and a caller that cannot tell them apart calls all of them
"no data".

## Rejected

| Option | Why not |
| ------ | ------- |
| Refuse when the dispersion is wide, as well as when `n` is small | The option this ADR exists to reject. It refuses exactly where the information is most useful: an erratic estimator is the one a person most needs told about, and "1.6x, ordinarily anywhere from 0.9x to 2.9x, n=6, confidence 0.31" is both more useful than silence and more honest than a bare 1.6x. It also needs a second magic number with far less justification than the first — five is defensible as "enough points to see a pattern", but no threshold on σ has a story that survives being asked twice. |
| Report the point factor and let each caller decide about the spread | The interval would travel separately and then not travel at all. Whoever wants one number takes the factor and the band is never printed. One frozen object makes it impossible to cite a correction without holding what it was drawn from. |
| Fit the factor to the arithmetic mean of the ratios | Wrong for a multiplicative quantity and wrong in a direction that flatters nobody consistently. `EST-0007` made this argument in prose about two scattered points; this is where it becomes code. |
| Report the standard deviation and let the reader form a band | A number most readers will not know what to do with, and one that invites the reader to build a band using the wrong arithmetic — an additive interval around a multiplicative quantity is asymmetric nonsense at the low end and can reach below zero. |
| Use a 95% band rather than one log sigma | Precision about a tail nobody has measured. At `n` between five and ten the extremes are extrapolated, not observed, and a wider number carrying less information reads as more authority rather than less. |
| Make the band a confidence interval on the mean, dividing σ by √n | Answers the wrong question. A calibrator asks where the *next* estimate lands, not how well the average is known — and the second shrinks towards zero as the sample grows even for an estimator who stays wildly erratic. |
| Pool `unclassified` estimates into one group | Would compute one factor over migrations, refactors and incident response and call it a person's bias. `work_class` is a grouping key, so `unclassified` is the *absence* of a key, not a value of it. Grouping meaningless work is worse than not grouping it. |
| Drop `unclassified` rows silently | The one failure here somebody can fix this afternoon. "Fourteen estimates carry no class" is an instruction; an empty table is not. |
| Let the threshold be a constructor argument with a default of 5 | A number that whoever wants an answer can lower is not a threshold. It would be set to 3 within a quarter by someone with a deadline, and the record would not show it happened. |
| Compute the factor below the threshold and simply not return it | One refactor from being printed. "Not computed" and "computed and withheld" look identical in a passing test suite and differ entirely in what a future change can expose. |

## What was known at the time

**This project's own history is the worked example, and it refuses on all of
it.** Recomputed from `docs/dogfood/` at the close of Phase 6, on active time:

| Class | n | Ratios (`actual/estimated`) | Verdict |
| ----- | - | --------------------------- | ------- |
| `agent-implementation` | 4 | 0.764, 0.458, 0.708, 0.560 | `insufficient_sample`, 1 short |
| `scaffolding` | 1 | 0.440 | `insufficient_sample` |
| `data-modelling` | 1 | 2.100 | `insufficient_sample` |
| `llm-integration` | 1 | 1.244 | `insufficient_sample` |

Read the other way round, `agent-implementation` is four over-estimates: 1.31x,
2.18x, 1.41x, 1.79x. Geometric mean **1.64x**, arithmetic mean 1.67x, σ in log
space **0.232**, coefficient of variation 0.24, range 1.31x–2.18x.

**`EST-0007`'s objection is answered twice over, and the order matters.** It
argued that a factor fitted to the mean of two scattered ratios — 1.31x and
2.18x — would have mispriced Phase 5. Under this design the *threshold* is what
answers that, not the dispersion: at `n = 2` nothing is emitted at all. The
dispersion is what would have answered it had `n` been sufficient — at those two
points the band spans roughly 1.2x to 2.4x, which is wide enough that nobody
reading it would have committed to 1.75x. Saying which mechanism does the work
matters, because a design that credited the interval with a save the threshold
actually made would be untested where it counts.

**The confidence formula reaches exactly one half at the threshold**, by making
`CONFIDENCE_HALF_AT` the same number as `MINIMUM_SAMPLE` and binding them with a
test. A group that has only just cleared the bar should read as a coin-flip
signal; a formula returning 0.9 there would contradict, in a number, the caution
the threshold expresses in code.

**`README.md`'s illustrative "1.8x under, n=14, CI [1.4, 2.3], confidence 0.79"
predates this formula, and half of it turned out to be exactly right.** The band
it quotes *is* one log sigma: `1.8 / 1.4 = 1.286`, so σ = 0.2513, and
`1.8 × 1.286 = 2.31`. The confidence is not — under `n/(n+5) · 1/(1+σ)` those
same numbers give **0.59**, not 0.79. The document has been corrected to the
number the code produces, rather than the formula tuned to match a sentence
written before there was one. Which direction that correction runs is the whole
point: the promise on the front page is now a claim the code keeps.

Not known: whether five is the right number for an organisation with more
estimators and shorter cycles, and whether one log sigma reads as too wide to
teams used to a single figure. Both are reported alongside `n` and `considered`,
so a reader who disagrees can re-derive without re-running anything.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | No sample below the threshold ever produces a factor | `sub_threshold_factors_emitted == 0` | `on_event("a caller asks for a threshold override")` |
| 2 | The threshold is reachable in practice rather than in principle | `groups_at_or_above_minimum_sample >= 1` | `when(stored_outcomes >= 40)` |
| 3 | Widening rather than refusing keeps most scattered groups speaking | `measured_groups_share >= 0.5` | `when(stored_outcomes >= 40)` |
| 4 | Unclassified rows stay a small part of the corpus | `unclassified_estimate_share <= 0.25` | `on_event("WorkClassifier changes how it revises")` |
| 5 | Re-running over an unchanged store returns an identical result | `bias_rerun_differences == 0` | `on_event("a calibration factor is cached rather than recomputed")` |

## Consequences

**Accepted costs.** A group at `n = 5` with a tight spread and a group at
`n = 50` with a tight spread both speak, and only the confidence separates them.
That is the intended behaviour and it will be misread by anyone who takes the
factor and drops the confidence — which the frozen object makes awkward and
cannot make impossible.

The band says nothing about *why* an estimator scatters. ADR 0021 already noted
that a 2x miss from tripled scope and a 2x miss from optimism score the same,
and `Estimate.conditions` holds the material that would tell them apart. Nothing
reads it yet, here either. It is in `BACKLOG.md` rather than pretended away.

Five is a judgement and the record says so. It is small enough that a team
reaches it within a quarter on work they do often, large enough that one unusual
project cannot set the factor alone, and it is not derived from anything — a
sample of seven outcomes cannot support fitting a threshold, and fitting one to
it would be over-fitting with extra steps.

**Reversal cost.** Low for the constants: `MINIMUM_SAMPLE`, `NEUTRAL_BAND`,
`INTERVAL_LOG_SIGMAS` and `CONFIDENCE_HALF_AT` are four names, each with a test
that fails if it moves silently. High for the *shape*: `CalibratorAgent`,
`ScoringAgent`, the eval table and Phase 8's `FusionBridge` all read
`CalibrationFactor`, and splitting the factor from its band later would mean
re-reading every correction the system had ever explained.
