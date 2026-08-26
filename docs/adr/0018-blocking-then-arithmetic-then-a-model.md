---
id: ADR-0018
status: accepted
date: 2026-08-26
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0018 — Find contradictions by blocking, then arithmetic, then a model

## Chosen

`ContradictionDetector` runs three stages over the store's assumptions and
decisions, and **only the last is a model**:

1. **Blocking.** A deterministic index proposes the pairs worth comparing.
   Records are keyed by the quantities their compiled predicates constrain
   (`subject:index_size_gb`) and, failing that, by the words their prose uses.
   Comparing every pair is quadratic and most pairs are about different things.
2. **Interval arithmetic.** For any proposed pair whose predicates both parse
   and constrain one shared quantity, the satisfying ranges are computed and
   intersected. An empty intersection is a **proven** contradiction: confidence
   1.0, zero model calls, reproducible.
3. **A model, batched.** Only the residue — pairs the arithmetic could not
   settle — reaches the `reason` tier, twelve pairs to a call.

`Settlement` records which stage decided each pair, and the eval table reports
the recalls apart. Every stage **refuses rather than guesses**, because a false
contradiction is the expensive direction: it sends a person to re-read a
decision that was fine, and every later finding pays for it.

Re-running is duplicate-free *by construction* rather than by a check. A
`Link`'s id is derived from its type and endpoints (ADR 0008) and each pair is
ordered by id before an edge is built, so the same contradiction found twice is
the same id twice.

## Rejected

| Option | Why not |
| ------ | ------- |
| Ask a model about every pair | Quadratic in the store on the most expensive tier. A thousand assumptions is half a million pairs, which is not a cost anyone would pay again on a schedule — and the answer would be non-reproducible for exactly the pairs arithmetic can settle for free. |
| Arithmetic only, no model at all | Only reaches pairs whose predicates both parse and share a quantity. Two assumptions stating incompatible things in prose — the common case in real documents — would never be found, and the detector would score well on a corpus built to suit it. |
| Embeddings for candidate generation instead of an index | Adds a model, a vector store and a similarity threshold to the one stage whose value is that it is free and reproducible. It would also make the candidate set depend on an embedding version, so two runs could propose different pairs and the recalls would stop being comparable. |
| Skip blocking; let arithmetic filter | Arithmetic needs both predicates parsed and a shared identifier, so it is already a filter — but running it over every pair is still quadratic, and it says nothing about the prose pairs. Blocking is what makes the model stage's input small enough to batch. |
| One recall number for the detector | A low number would mean three different things: blocking never proposed the pair, the arithmetic could not read the predicates, or the model was asked and said no. Those have three different fixes, and a single number hides which one is needed. |
| Let the model settle pairs the arithmetic already decided | Replaces a certainty with an opinion. If the intervals do not intersect, the pair conflicts; asking anything else can only introduce disagreement with a fact. |

## What was known at the time

The corpus had no contradictions before Phase 5, so a detector finding nothing
and one finding everything scored identically — which is not a metric. Every
topic gained a `reversal_predicate` written to be **provably** disjoint from its
original, so the planted pair lands inside what the arithmetic can settle and
the two recalls read apart. Four revisions rather than eight, so the corpus
holds assumptions that were overturned *and* assumptions that were not.

`MIN_WORD_LENGTH` is 4. The archaeologist's tests found the same number from the
other side: at 3, `why`, `not` and `for` matched nearly every document. A word
key shorter than four characters inflates every bucket and the stage stops
discriminating.

Not known: what fraction of real contradictions are provable rather than a
judgement. The corpus is built so the planted ones are provable, which measures
the arithmetic path and deliberately does not measure the model path's recall in
the wild. The `Settlement` split is what will answer it on a real store.

Also not known: whether the batch of twelve is the right size. It was chosen so
one call holds enough context to compare pairs without the prompt growing past
what the tier reads reliably, and nothing has measured it.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Blocking proposes nearly every real contradiction, so the stages below it are not starved | `blocking_recall >= 0.9` | `when(store_records > 1000)` |
| 2 | A useful share of contradictions is provable arithmetic rather than a judgement | `arithmetic_settled_share >= 0.5` | `on_event("the first live contradiction run")` |
| 3 | False contradictions stay rare enough that a person keeps trusting the findings | `false_contradictions_per_run <= 1` | `on_event("the first live contradiction run")` |
| 4 | Batching keeps the model stage cheap as the store grows | `contradiction_model_calls_per_record <= 0.5` | `when(store_records > 1000)` |

## Consequences

**Accepted costs.** Three stages is three things to understand, and a person
debugging a missed pair has to know which stage to look at — which is exactly
why `Settlement` is stored and why the table has three rows rather than one.

Blocking can lose a pair permanently. A key whose bucket is too large to
discriminate is skipped, and a pair only that key would have proposed is a pair
nothing will ever look at. That is reported rather than dropped silently, and
`blocking_recall` is printed as the ceiling the other recalls sit under.

The arithmetic stage only reaches compiled predicates, so the detector's quality
depends on the formalizer's. That coupling is real and it is why the two run in
that order in the eval harness.

**Reversal cost.** Low per stage, medium overall. Removing the model stage
leaves a detector that is strictly weaker and still correct — every edge it
wrote is still proven. Removing the arithmetic stage would push its pairs onto
the model tier, which is a cost change rather than a correctness one. Replacing
blocking means replacing the one component whose output nothing downstream
records, so nothing stored has to change.
