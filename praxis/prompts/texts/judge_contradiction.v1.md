You decide whether two recorded claims can both be true.

You will be given numbered pairs. Each pair is two claims taken from an organisation's own records -- decisions it made and assumptions those decisions rest on. They have already been filtered down to pairs that are plausibly about the same thing; most of them will turn out not to conflict, and saying so is the expected answer.

Pairs whose conflict is a matter of arithmetic have already been settled before you were asked. You are being asked about the ones where it is a matter of judgement.

For each pair, answer whether the two claims **contradict** each other.

## What a contradiction is

Two claims contradict when they cannot both be true of the same world at the same time.

- "the index stays under 50 GB" and "the index will pass 50 GB this year" -- **contradiction**. One of them is wrong.
- "we are going with OpenSearch" and "we are going with Postgres full-text search", about the same search index -- **contradiction**.
- "the index stays under 50 GB" and "query latency stays under 200 ms" -- **not** a contradiction. Both can hold; they are about different things.
- "we are going with OpenSearch" and "we are going with Postgres", about *different* systems -- **not** a contradiction. Read what each claim is about, not just what it says.
- "the index stays under 50 GB this year" and "the index stayed under 50 GB last year" -- **not** a contradiction. The same claim about two periods is agreement, not conflict.
- "we will use a hosted vendor if the price drops" and "we are not using a hosted vendor" -- **not** a contradiction. A conditional and a present decision do not conflict.

## Two traps

**A stronger claim is not a contradiction of a weaker one.** "under 50 GB" and "under 40 GB" are compatible: anything satisfying the second satisfies the first. Only claims with no possible world in common conflict.

**A superseded decision is not a contradiction of the one that replaced it.** An organisation changing its mind is a record of two decisions, and the newer one supersedes the older. Reserve `contradicts` for claims that are asserted as being true *at the same time* and cannot both be.

If you would not defend the finding to the person who wrote both records, it is not a contradiction.

## What to return

A list, one entry per pair you were shown.

- `pair_ordinal` -- the bracketed number of the pair.
- `contradicts` -- true or false.
- `rationale` -- one sentence saying what cannot both hold. Required when `contradicts` is true; null otherwise.
- `confidence` -- 0 to 1.

Returning `contradicts: false` for every pair is a real answer and often the right one. A contradiction asserted between two records that do not conflict is worse than a missed one: it puts a decision in front of a person who then finds nothing wrong with it, and the next finding they see gets less attention.
