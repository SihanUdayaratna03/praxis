---
id: ADR-0025
status: accepted
date: 2026-08-30
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0025 — the calibrator explains rather than generates

## Chosen

`CalibratorAgent` **makes no model call**. It moves out of ADR 0006's `extract`
row and into `NON_LLM_AGENTS`, alongside `BiasDetective`, `ScoringAgent`,
`SourceAdapter` and `VerifierAgent`.

Its explanation is a **template over numbers it was handed**. The factor, the
band, `n`, the confidence, the log-space spread and the refusal reason all exist
before the sentence does; the sentence orders them. `praxis/agents/calibrator.py`
imports no provider and therefore cannot reach one, which is the same structural
enforcement ADR 0021 established for `reconciliation.py` rather than a rule
somebody has to remember.

This makes **Phase 7 the first phase in this project where no component calls a
model at all**. There is no text to interpret in it: the input is `Estimate` and
`Outcome` rows the store already holds, and every number out is arithmetic over
them.

The refusal reason is **quoted from `BiasDetective`, not paraphrased**, so the
threshold is explained in exactly one place and a change to it cannot leave a
second wording behind.

## Rejected

| Option | Why not |
| ------ | ------- |
| Keep the `extract` route and have a model write the explanation | The decision this record reverses, and the reason is specific rather than general. Every number in the sentence is already computed and already correct. A model adds no information and adds one failure mode that nothing downstream could catch: a fluent paragraph citing 1.7x beside a stored factor of 0.61x reads *better* than the correct one, and no test distinguishes a well-written wrong number from a well-written right one. |
| Use a model only for the tone, passing the numbers in as placeholders | The same risk with extra machinery. Any generation step able to alter a token is able to alter a digit, and a template with holes in it is a template — it just costs a network call and stops being reproducible. |
| Use a model to decide *whether* the correction is worth mentioning | That is the threshold, and ADR 0024 already made it arithmetic with no override. Sneaking a judgement call back in as an editorial decision about what to surface would be the same override wearing a different hat. |
| Leave `CalibratorAgent` in `_ROUTING` unused, in case a later phase wants it | `praxis doctor` asserts `NON_LLM_AGENTS` and the routing table never overlap, so this is not a state the system permits. It would also leave the cost table implying a per-calibration model spend that never happens — a number in a report nobody could reconcile. |
| Write a new ADR superseding 0006 entirely | ADR 0006's actual decision — agents ask for a role, one file holds every model id — is untouched and correct. Superseding it would retire a good record to change one row of a list. This amends the list and says so in both files. |

## What was known at the time

`CalibratorAgent` was assigned to `extract` in Phase 2, before any agent existed
and before there was a `CalibrationFactor` to explain. On the information
available then it was a reasonable call: "explain an adjustment in plain
language" sounds like a writing task, and the routing table had to name every
agent the architecture listed.

What building it showed is that the output has no free variables. A worked
example, produced by the real code:

> `5.5 hours becomes 3.3649 hours. claude-opus-5's last 5 resolved estimates of
> agent-implementation work ran 1.6345x over, so this is scaled by 0.6118x.
> Ordinarily between 2.7522 and 4.1146 hours. n=5, confidence=0.4163, computed
> from 5 resolved estimates with a log-space spread of 0.2011.`

Every value in it is read off a `Spread` or a `CalibrationGroup`. There is
nothing for a model to decide, and one thing for it to get wrong.

This is the fourth time the same boundary has been drawn in this codebase and
the fourth time it moved a component to the arithmetic side: `VerifierAgent` in
Phase 3, `match_quality` in Phase 6 (ADR 0021), `BiasDetective` and
`ScoringAgent` from the start, and now this. `OUT-0004`, `OUT-0006` and
`OUT-0007` each recorded a version of the same estimation miss — a component
described as one thing having a subject inside it that cannot call a model.
`EST-0008` priced that boundary as its own line item because of those three, and
this is where the line fell.

Not known: whether a real deployment will want the explanation in a language
other than English, which is the one requirement that would genuinely argue for
generation. It would be a translation of a fixed sentence rather than a
description of a number, so it is a different job for a different component, and
it is in `BACKLOG.md` rather than pre-empted here.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The calibrator never acquires a model route | `calibrator_module_llm_imports == 0` | `on_event("the explanation is required in a second language")` |
| 2 | Phase 7 spends nothing on a model | `calibration_pass_llm_calls == 0` | `on_event("a fourth calibration component is written")` |
| 3 | A templated explanation carries what a reader needs | `explanations_missing_sample_size == 0` | `when(stored_outcomes >= 40)` |
| 4 | The threshold is worded in exactly one place | `threshold_wordings == 1` | `on_event("a second component reports a refusal")` |

## Consequences

**Accepted costs.** The explanation reads like a template, because it is one.
It will not vary its phrasing to suit a reader, and a long table of them repeats
the same clause many times. That is a real cost and it is smaller than a
paragraph that occasionally disagrees with the row above it.

The routing table now has an entry fewer than the architecture diagram has
agents, which is the correct state and looks like an omission. ADR 0006 carries
an amendment note pointing here, and `praxis doctor` is what settles the live
answer.

**Reversal cost.** Low mechanically — one line in `_ROUTING`, one in
`NON_LLM_AGENTS`, and a provider argument on the constructor. High in what it
would cost to trust the output again: every explanation the system had ever
produced would become a sentence that might or might not restate its own
numbers correctly, and there is no test that separates those two after the fact.
