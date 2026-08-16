---
id: ADR-0011
status: accepted
date: 2026-08-16
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0011 — Segment semantically, over a deterministic block grid

## Chosen

A document is cut deterministically into **blocks** — headings, paragraphs,
list items, whole tables, whole fenced code — and `SegmenterAgent` asks a model
which *contiguous runs of blocks* form one unit of meaning. The model answers
in **block indices**. It never supplies a byte offset, and every span is built
by slicing the document itself.

The blocks are also the floor: any block no valid group claims becomes its own
span, so the worst answer a model can give degrades the result to
paragraph-level segmentation rather than breaking it.

## Rejected

| Option | Why not |
| ------ | ------- |
| Sentence-level, fully deterministic | A decision and the assumption it rests on are never one sentence, and a claim citing a sentence fragment cites something that cannot be read on its own — which is what a human is shown when they ask why a finding was raised. It also multiplies span count, and scanning runs once per span, so cost per document (a reported Phase 10 metric) multiplies with it. |
| Paragraph-level, fully deterministic | Cheap, reproducible and genuinely good — which is why it is this design's degradation floor rather than absent. Rejected as the *primary* strategy because one paragraph in an ADR routinely holds a decision, its rationale and the assumption underneath it, and telling those apart is the work Phases 5 and 8 exist to do. Segmenting so that they always arrive fused makes that work harder for no saving. |
| Model emits byte offsets directly | The only variant where a hallucination produces a span that is *wrong* rather than merely badly grouped. Span integrity would then rest on `VerifierAgent` catching every fabricated offset, instead of on the offsets never having been the model's to invent. A guarantee that depends on a check is weaker than one that depends on a type. |
| Fixed-size overlapping windows, as retrieval pipelines use | Optimises for embedding recall, which is not the problem here. Overlap means one sentence lives in two spans with two ids, so the same claim extracted twice is two claims — and ADR 0008's content-addressed span ids exist precisely so that citation de-duplication is structural. |

## What was known at the time

The corpus is ADRs, meeting notes, status updates and JSON exports: short
documents, heavily structured, written by people who use headings and bullets.
`MockProvider` is the only provider available, so the *quality* of a real
model's grouping cannot be measured in this phase — only that the mechanism
holds when the answer is bad, which is testable now via
`MockProvider(malformed_share=...)`.

Phase 1 supplied `verify_span` and content-addressed span ids. Phase 2 supplied
the provider seam, and `SegmenterAgent` was already routed to the cheapest tier
(`SCAN`) in `praxis/config/models.py`, on the reasoning that segmentation is
the highest-volume call in the pipeline.

Not known: whether semantic grouping actually beats the paragraph floor on
extraction quality. Nothing in this phase can answer that — it needs the Phase
10 harness and the labelled corpus, which is why the assumption below is
written as a measurement rather than a belief.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Block grouping extracts better than the deterministic paragraph floor | `segmenter_f1 - paragraph_floor_f1 >= 0.05` | `when(phases_completed >= 10)` |
| 2 | A model never needs to cut *inside* a block to segment well | `spans_requiring_a_sub_block_boundary == 0` | `on_event("a source kind other than markdown, text or json lands")` |
| 3 | One window of blocks fits the scan tier's context with room for the answer | `max_window_prompt_tokens <= 8000` | `when(corpus_documents > 500)` |
| 4 | Grouping failures are recoverable, so a bad answer costs quality and never correctness | `spans_failing_verification_from_segmentation == 0` | `when(phases_completed >= 6)` |

## Consequences

**Accepted costs.** The grid decides what is possible, so a document whose
meaning genuinely crosses a block boundary mid-line cannot be segmented
correctly by anything above it — assumption 2 is written to fire if that turns
out to happen. Blocks are markdown-shaped, and each new source kind either
renders into something the markdown rules read correctly or needs its own rules.
And the LLM call is real cost on the highest-volume step in the pipeline, which
assumption 1 is the justification for and Phase 10 is the audit of.

**Reversal cost.** Near zero, and asymmetric in the useful direction. If Phase
10 measures the grouping at parity with the floor, the fix is to stop making
the call and emit the grid — a deletion, not a rewrite, because the floor is
built first and the pipeline already runs on it whenever a model answers badly.
Going the other way later would have been expensive, which is the reason the
offsets were never handed to a model in the first place.
