---
id: ADR-0015
status: accepted
date: 2026-08-18
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0015 — Extraction cites spans by offered ordinal, never by span id

## Chosen

Every Half A agent is shown the spans it may cite as a **numbered listing**
(`[0] …`, `[1] …`) and answers with ordinals into that listing.
`praxis.agents.offering` renders the listing and resolves an ordinal back to a
real `SpanId`. An ordinal outside the listing is refused with a reason and
carried out in the result.

This is ADR 0011's mechanism — the segmenter answers in block numbers, never in
byte offsets — applied to the second place a citation can be fabricated.

**What it removes and what it does not.** A `SpanId` is a 16-hex digest of
coordinates, so a model asked for one can emit a well-formed id addressing
nothing; `praxis.llm.synthesis` does exactly that deliberately. An ordinal has
no such failure: it is in range and resolves to a span the agent really saw, or
it is refused. What survives is the *wrong* citation — passage 3 cited, passage
5 quoted — and `Offering.where_quoted` names that as `IN_ANOTHER_OFFERED_SPAN`,
distinct from `NOWHERE`.

`VerifierAgent` re-reads every claim against its document regardless.

## Rejected

| Option | Why not |
| ------ | ------- |
| Agents emit `SpanId` directly | The digest form is exactly what a model can produce plausibly and wrongly. It also asks a model to copy 20 opaque characters accurately for every claim, and a transcription slip is indistinguishable from a fabrication. |
| Emit a `SpanId` and check it against the offered set | Equivalent in strength and worse in every other way: more tokens per claim, a failure mode that reads as "hallucination" when it was a typo, and it makes the prompt carry ids that mean nothing to the model. |
| Emit byte offsets | ADR 0011 already rejected this for segmentation and the argument is unchanged: offsets are exactly the thing a model cannot count, and a citation with plausible offsets is a citation nobody can spot as wrong by reading it. |
| Emit the quotation only, and locate it by search | Silently resolves to the wrong occurrence whenever a phrase repeats — which in a corpus of ADRs and status updates is often. It is also the mistake ADR 0012 already refused for the answer key, so refusing it here keeps one rule instead of two. |
| Number spans by position in the document rather than in the window | Lets an agent reason about spans it was not shown, and makes the numbers large. A window *is* what the agent can see. |
| Collapse `IN_ANOTHER_OFFERED_SPAN` into a single "bad citation" outcome | They are different faults with different responses in a live run: one is a model that read the material and mis-attributed, the other is a model that invented. A single citation-accuracy figure would hide which one a prompt change fixed. |

## What was known at the time

The offline provider is what these numbers are first measured against, and its
behaviour here is known precisely. `praxis.llm.synthesis` draws integers for
ordinal-named fields from bracketed labels the prompt really presented, so the
mock's ordinals are always in range — which means the offline run exercises the
path that *honours* a good citation. It draws quotations uniformly from the
whole prompt, so with a window of `W` spans a quotation lands in the cited span
roughly one time in `W`. That is not a defect to design around: it is why the
harness reports citation integrity separately from extraction recall, and why
the Phase 4 report says which of its numbers are about the pipeline and which
are about the synthesiser.

Not known: whether a real model finds ordinals easier or harder than quoting.
The prompt asks for both, so the first live run measures it.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | A model handles small ordinals more reliably than 16-hex ids | `unoffered_ordinal_rate <= 0.02` | `on_event("the first live extraction run")` |
| 2 | Windows stay small enough that ordinals are unambiguous to a reader | `spans_per_offering <= 12` | `when(corpus_documents > 200)` |
| 3 | Mis-attribution and fabrication really are different rates worth separating | `mis_attribution_rate / fabrication_rate` outside `[0.5, 2.0]` | `on_event("the first live extraction run")` |
| 4 | Eliding a very long span costs no correct citation, because the offsets are not elided | `claims_refused_for_quoting_elided_text == 0` | `when(phases_completed >= 10)` |

## Consequences

**Accepted costs.** The listing is re-rendered per call, so a span in three
windows is sent three times. That is a token cost measured in the eval table's
cost column rather than an unknown, and it is the same trade ADR 0011 accepted
for blocks.

Ordinals are meaningless outside the call that issued them, so a trace row's
answer cannot be read without the request beside it. `request_json` holds the
rendered listing, so it always is.

Assumption 3 is the one most likely to be wrong, and the design is deliberately
tolerant of that: if the two rates turn out to be one phenomenon, collapsing the
enum is a smaller change than splitting it would have been later.

**Reversal cost.** Low. `Offering` is one module behind three agents; going back
to span ids would be a change to the response models and this file. Nothing in
the store records an ordinal — what is stored is always a resolved `SpanId`.
