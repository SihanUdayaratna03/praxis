---
id: ADR-0012
status: accepted
date: 2026-08-16
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0012 — Ground truth as byte ranges, labelled negatives and typed edges

## Chosen

The synthetic corpus ships an answer key in one `ground_truth.json` beside the
documents. Every entry carries **byte offsets and the exact quotation at those
offsets**, a stable `item_id`, the **typed edges** between entries, per-field
expected values with a declared **comparison mode**, and an `is_distractor`
flag for text that looks extractable and must not be extracted.

The generator emits it **by construction**: each document is assembled from
fragments and the byte range of every fragment is recorded as it is appended.
Nothing is ever located by searching the finished text.

## Rejected

| Option | Why not |
| ------ | ------- |
| Grade on extracted text, string-compared | Cannot award partial credit, and cannot tell "found the decision, cited three words too few" from "found nothing". Since the entire span mechanism exists so that a claim points at a place, grading that ignores the place would measure everything except the thing the architecture is about. |
| Hand-label a corpus of real documents | The labels would be better and there would be far fewer of them, and the effort recurs on every corpus change. It is also unavailable: Praxis's own history is the only real corpus to hand, and grading a system on the documents it was designed around measures the design and not the system. Real documents come back as a *held-out* set once there is a harness to run them through. |
| Locate ground truth by regex over the generated text | The obvious shortcut and a quiet disaster: it silently mislabels every time a fragment's wording repeats elsewhere in the document, which is exactly what a generator drawing from fixed phrase menus makes common. Recording the offset at the moment of writing cannot be wrong. |
| Positives only | Then only recall is measurable, and a harness that measures recall alone rewards an agent that extracts every sentence. The distractors are where precision comes from, so they are part of the format rather than an afterthought. |
| Store expected values as typed JSON per record kind | A union of shapes to keep in step with `praxis.domain.records`, revised every time a record gains a field. Values are held as strings with a declared comparison mode instead, so the *format* is stable and the grader owns the semantics. |

## What was known at the time

Phase 10 builds the eval harness and nothing before it consumes this file, so
the format is being designed against a consumer that does not exist. That is
the risk, and it is the reason the format is written down as an ADR now rather
than settled by whatever Phase 10 happens to find convenient — a format
discovered late is a corpus regenerated late, and a regenerated corpus makes
every number recorded before it incomparable.

What is known: the metrics Phase 10 owes are extraction precision and recall
per record kind, citation accuracy, and edge precision and recall. Each of
those is computable from the fields above and none is computable without one of
them. The corpus is also Phase 3's own test fixture, so it has a consumer
today, which is what stops this being speculative design.

Not known: whether byte-overlap scoring wants a threshold or a graded score,
and what tolerance a numeric field comparison should carry. Both are the
grader's business, and both are why the format carries the *data* rather than
the verdict.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The format carries everything Phase 10's metrics need | `eval_metrics_needing_a_format_change == 0` | `when(phases_completed >= 10)` |
| 2 | Generated documents are close enough to real ones that agents tuned on them work on real ones | `held_out_vs_synthetic_f1_delta <= 0.2` | `on_event("a held-out set of real documents is first graded")` |
| 3 | Ground truth offsets stay valid, because they are written and re-verified rather than searched for | `ground_truth_items_failing_reverification == 0` | `when(corpus_documents > 200)` |
| 4 | Distractors are hard enough to separate the good agents from the eager ones | `distractor_false_positive_rate >= 0.05` | `when(phases_completed >= 10)` |

## Consequences

**Accepted costs.** The generator is now a piece of software with a test suite,
and it has to stay honest: a bug there produces an answer key that is wrong in
a way no downstream test can detect on its own. That is what `verify_corpus`
answers — it re-reads every offset against the document and refuses a corpus
whose key has drifted, which is the same check `VerifierAgent` performs on
extractions, turned on the answers.

Assumption 2 is the uncomfortable one, and it is the same shape as ADR 0005's
assumption 3 about mock realism. A system tuned entirely on generated documents
may be tuned on the generator's habits. The predicate is written so that the
first held-out grading is a measurement rather than a surprise.

**Reversal cost.** Low but not free, and it rises with time. The format is a
file and a generator, so changing it is a regeneration — but every metric
recorded against the old corpus becomes incomparable at that moment, which is
precisely why it is settled before there are metrics rather than after.
