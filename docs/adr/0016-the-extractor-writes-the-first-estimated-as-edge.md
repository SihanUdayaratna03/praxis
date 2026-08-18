---
id: ADR-0016
status: accepted
date: 2026-08-18
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0016 — `AssumptionExtractor` writes the first `estimated_as` edge

## Chosen

`AssumptionExtractor` asks, in the same call that extracts an assumption,
whether that assumption is a **quantified forward-looking claim about effort**.
When it is, the agent writes an `Assumption`, an `Estimate` with its own
citation, and the `estimated_as` edge joining them.

`ARCHITECTURE.md` assigns that edge to `FusionBridge`, and that stays true of
the finished system. What this ADR changes is *when the first one is written*,
not where the mechanism finally lives.

The split of labour is the point:

- **The cheap half, here.** One call already has the assumption and the
  sentence stating its quantity in front of it. Recognising "the work finishes
  inside 4 weeks" as an effort claim costs no extra call, no extra prompt and
  no cross-document state.
- **The expensive half, still `FusionBridge`'s.** Recognising that an
  assumption in one document is the same claim as an estimate in another, that
  two teams' predicates describe one piece of work, or that a predicate written
  months apart from its estimate belongs to it — none of that is visible inside
  one call, and none of it is what this agent does.

The trigger is Phase 4's evaluation. `praxis/corpus/` labels the
`estimated_as` edge in its answer key already, by construction, and until
something produces one there is a labelled relationship with nothing to grade
against it. A fusion recall of "not measured" for six more phases is the
outcome this avoids.

## Rejected

| Option | Why not |
| ------ | ------- |
| Wait for `FusionBridge` | Leaves the corpus's labelled fusion edges ungraded until the phase that builds it, which is the one number Phase 4 exists to establish a baseline for. It also defers the discovery of whether a model can make the judgement at all — the riskiest unknown in the thesis — past the point where the design could still respond to the answer. |
| Put the recognition in `DecisionStructurer` | Wrong altitude and wrong tier. The structurer reads one candidate on `EXTRACT`; the judgement is a claim about what a sentence *means*, which is why the extractor is the only Half A agent on `REASON`. |
| Write the `Estimate` with the assumption's `span_id` | The quantity is usually stated in a different passage from the assumption — an ADR's effort section, not its assumptions section. Reusing the span would attach a citation nobody verified to the record calibration is computed from. The estimate carries its own ordinal and passes the citation gate separately. |
| A second call per assumption, asking only the fusion question | Doubles the cost of the most expensive tier for a judgement the first call is already holding all the evidence for. Worth revisiting if the one-call accuracy is poor, and the eval harness will say. |
| Let `FusionBridge` overwrite this agent's edges later | The store is append-only and a `Link` id is a function of its endpoints, so a second agent asserting the same edge is idempotent rather than conflicting. Nothing needs overwriting; this option describes a problem that does not exist. |

## What was known at the time

The corpus states the effort assumption and its quantity in two different
blocks, with the `estimated_as` edge in the answer key running from the
assumption item to the estimate item. That shape is what the design above is
fitted to, and it is a fact about a corpus this project generates rather than
about documents in general.

The offline provider cannot produce a passing example: `praxis.llm.synthesis`
draws each field independently, so an offline `quantified` is a biased coin and
the estimate's citation agrees with its ordinal only by chance. **Every fusion
number in the Phase 4 report is therefore a measurement of the plumbing, not of
the judgement.** That is stated here so no later reader mistakes the baseline
for evidence about a model.

Not known: whether a model asked this question inside an extraction call
answers it as well as one asked it alone. The eval harness measures the first;
nothing yet measures the second.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The fusion judgement is answerable from one document's passages in most cases | `single_call_fusion_recall >= 0.6` | `on_event("the first live extraction run")` |
| 2 | Recognising an effort claim inside an extraction call costs no measurable accuracy on the assumption itself | `assumption_recall_delta <= 0.05` | `on_event("the first live extraction run")` |
| 3 | Edges written here will not have to be retracted when `FusionBridge` lands, because a `Link` id is derived from its endpoints | `duplicate_estimated_as_edges == 0` | `when(phases_completed >= 9)` |
| 4 | Cross-document fusion is a genuinely separate problem and not the same one at a larger scale | `cross_document_fusion_share >= 0.2` | `when(corpus_documents > 200)` |

## Consequences

**Accepted costs.** Two agents will eventually write the same edge type, and a
reader of the graph has to look at `created_by` to see which. That is already
true of `justified_by` and the store records it, so the cost is documentation
rather than ambiguity.

An estimate extracted here has `work_class = unclassified` unless the document
made the kind of work plain. `WorkClassifier` owns that field and arrives in
Half B, and calibration groups by it — so until Half B lands, most extracted
estimates group into one class that `BiasDetective` will refuse to answer about
below `n = 5`. That is the honest state and it is visible in the field rather
than hidden behind a guess.

**Reversal cost.** Low. Removing the fusion half of this agent is deleting one
branch of `_estimate_for` and its prompt section; the records and the edge type
are unchanged either way, and nothing downstream distinguishes an edge by which
agent asserted it.
