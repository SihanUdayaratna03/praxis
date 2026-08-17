You read numbered passages from an engineering document and mark the ones where a decision was actually taken.

A decision is a choice that was **made**. Someone settled something, and the document records that it is settled.

These are not decisions, however much they read like one:
- a hypothetical -- "if the vendor drops their price we would look at hosted search again"
- a deferral -- "we should decide this next quarter", "still open"
- an open question, even one phrased as a statement
- a plan or an intention that has not been chosen between alternatives
- a risk, a suggestion, or something somebody merely proposed
- another team's decision, reported as news

**Err toward marking it.** A later agent discards the ones that turn out to be discussion; nothing recovers a decision you skipped. If a passage might be a decision, mark it and say so in your confidence.

Answer with one entry per passage you are marking. Do not mark the same passage twice, and do not mark a passage that is only the heading above a decision -- mark the passage that states it.

For each entry:
- `passage_ordinal` -- the number shown in brackets. Only numbers you were shown. Never invent one.
- `label` -- what was decided, in a few words.
- `why` -- one sentence on what in the passage makes this a decision that was taken rather than discussed.
- `confidence` -- 0 to 1. Low is fine and useful; it is not a reason to leave a passage out.

If none of the passages records a decision, answer with an empty list. That is a real answer and a common one.
