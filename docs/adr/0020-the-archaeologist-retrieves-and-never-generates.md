---
id: ADR-0020
status: accepted
date: 2026-08-26
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0020 — `ArchaeologistAgent` retrieves and grounds, and never generates

## Chosen

`ArchaeologistAgent` answers "why not X" from what the store recorded. **The
model selects; the store speaks.** It is asked exactly two things — which
recorded decision, and which rejected option, both by reference into a numbered
listing — and the answer is then assembled from the stored `reason`, the
decision's own fields and the current status of the assumptions it rested on.
No text the model produced appears in the answer. Its `note`, explaining why
that decision answers the question, sits beside the answer and is never spliced
into it.

An invented option cannot be expressed. The named option is checked against the
decision's own `rejected` tuple, matched no looser than case and whitespace: a
fuzzy match would let "Postgres" answer for a decision that rejected "Postgres
full-text search", which would usually be right and would attach the wrong
reason as a quotation.

Retrieval is FTS5 over the store, and the question is reduced to quoted terms
of at least four characters before it is used as a match expression. A question
the record does not cover is **refused** rather than answered — a refusal is the
cheap outcome and a wrong match is the expensive one.

**This ADR is the review surface for a contract that was never specified.**
Three fragments existed when the agent was built — `RejectedOption`'s docstring,
a note in the corpus generator, and ADR 0006's routing entry — and nothing else.
The contract above is a judgement, recorded here so it can be disagreed with.

## Rejected

| Option | Why not |
| ------ | ------- |
| Let the model write the answer from the retrieved records | The failure this agent exists to prevent. A fluent paraphrase of a decision's reason is indistinguishable from the reason, and a person asking "why not X" is asking what was actually said — not what a model thinks was probably said. Every other guarantee in the system is about provenance, and this would be the one place it was surrendered at the last step. |
| Fuzzy-match the rejected option's name | Usually right, and the failure case attaches a real quotation to the wrong option. "Postgres" matching "Postgres full-text search" is the exact shape of a plausible wrong answer, and it would be presented with a citation. |
| Answer from the document text rather than the records | The records are what was extracted, verified and versioned; the document is what someone wrote. Answering from the document would bypass the citation gate and would also lose the assumption statuses, which are the part that makes this worth having. |
| Skip the model and rank by search score alone | FTS5 ranks by term overlap, which cannot tell "the decision about search indexing" from "a decision that mentions search". Choosing among a handful of retrieved candidates is a judgement, and it is the one judgement here that is safe to delegate because it is a reference into a closed list. |
| Answer without the assumptions' current status | "We chose this because we assumed that" and "we chose this because we assumed that, and that assumption broke in June" are different answers, and the second is the reason a system that also monitors is worth asking. |
| Return the best guess when nothing matches | A confident wrong answer about why a decision was made is worse than no answer, because there is no way for the asker to tell. The refusal names what was missing. |

## What was known at the time

`Repository.search` passes its argument to FTS5 unchanged, so a bare question
mark is a malformed match expression rather than a search. Reducing the question
to quoted terms first is what makes a natural-language question safe to hand to
the index at all.

`MIN_TERM_LENGTH` was 3 and the terms are `OR`-joined. The tests found the
defect: `why`, `not` and `for` matched almost every document, so a question
about zeppelins retrieved the entire search index — a model call per question,
with the refusal left to a prompt to produce. Raised to 4, which is the floor
`blocking.py` already uses for the same reason.

The agent is tested at 99%. One test asserts every line of the answer contains a
stored field; another feeds the model an invented sentence and asserts it
reaches the `note` and never the `answer`.

Not known: whether a person asking a real question phrases it in terms the
index shares with the decision. Every question tested so far was written by
someone who had read the corpus. A refusal rate measured against real questions
is the number that would say whether retrieval or the contract is the limit, and
nothing has measured it.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | No answer ever names an option the decision did not record | `archaeologist_invented_options == 0` | `when(store_decisions > 200)` |
| 2 | Every line of every answer comes from a stored field | `archaeologist_answer_lines_from_store_rate == 1.0` | `when(store_decisions > 200)` |
| 3 | Retrieval finds the decision often enough that refusals stay a minority | `archaeologist_refusal_rate <= 0.4` | `on_event("the first live why question")` |
| 4 | Answering a question costs about one model call, so asking is cheap enough to be habitual | `archaeologist_retrieval_calls_per_question <= 1` | `on_event("the first live why question")` |

## Consequences

**Accepted costs.** The answers read like records, because they are records.
They are stiffer than a generated paragraph and they will sometimes be less
helpful than one a model could have written. That is the trade, made
deliberately, and it is the reason the `note` exists at all — a person who wants
the model's framing can read it, clearly labelled, next to the answer.

The agent refuses more than a generative one would. A question phrased in words
the index does not share with the decision gets nothing, and the person has to
rephrase. Every refusal names what was missing so rephrasing is possible.

The quality of an answer is bounded by the quality of the extraction beneath it.
A decision whose `reason` was extracted poorly produces a poor answer with a
citation attached, and the citation makes that visible rather than hiding it.

**Reversal cost.** Low in code, high in kind. Letting the model write the answer
is deleting the assembly step and printing its text instead — an hour's work.
But it changes what the system *is* from a record that can be checked into an
assistant that sounds right, and every answer given afterwards would carry a
different warrant than every answer given before. That is why the contract is
written down here rather than left in a module docstring.
