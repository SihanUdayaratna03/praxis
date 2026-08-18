A decision has been extracted from the numbered passages below. Find the assumptions it rests on.

The decision is **$chosen**, stated in passage $decision_ordinal. Assumptions are rarely written beside it -- an ADR puts them under a heading of their own, meeting notes put them under "what we are carrying" -- so read every passage.

An assumption is a claim about the world that has to hold for the decision to stay right, and that could turn out to be false. "We chose OpenSearch" is the decision, not an assumption. "The index stays under 50 GB" is an assumption: it is a claim, it can be checked, and if it fails the decision is worth re-reading. A statement that cannot fail is not one.

For each assumption, fill:

- `statement` -- the assumption in the document's own words.
- `predicate` -- the same claim as an expression something could evaluate, e.g. `index_size_gb <= 50`. If the document already writes one, copy it exactly. If it does not, write one in the same style: a snake_case subject, a comparison, a number.
- `expiry_condition` -- when it should be re-checked, e.g. `when(indexed_documents >= 10000000)` or `on_event("the work ships")`. If the document states one, copy it. If not, write the condition under which this claim would stop being safe to rely on.
- `confidence` -- 0 to 1, how sure you are this is an assumption the decision rests on.
- `evidence_ordinal` -- the bracketed number of the passage the assumption is stated in.
- `evidence_quote` -- text copied **exactly** from that passage. It will be re-read against the passage you named. Copy it; do not paraphrase, do not tidy, do not join two sentences.

Then one more question about each, and it is the one this whole system is built around.

**Is this assumption a quantified, forward-looking claim about how much work something will take?** "The work finishes inside 4 weeks" is one. "The index stays under 50 GB" is not -- it is quantified, and it is about the world rather than about effort. If it is, the assumption is an estimate wearing an assumption's clothes, and you should also fill:

- `quantified` -- true.
- `estimate_subject` -- what is being estimated, in a few words.
- `estimate_owner` -- whose estimate it is. The person the document names, or the team. Calibration is per estimator, so a wrong name here is worse than `not stated`.
- `estimate_work_class` -- the kind of work, as lower-case-hyphenated words, e.g. `data-migration`. Leave null if the document does not make it clear; do not guess.
- `estimate_active_quantity` -- hands-on effort, as a number.
- `estimate_blocked_quantity` -- predicted time waiting on someone else. Null if the document does not say, which is usually.
- `estimate_unit` -- `hours`, `days`, `weeks`, `points`, `count` or `usd`.
- `estimate_ordinal` and `estimate_quote` -- the passage the *quantity* is stated in and text copied exactly from it. Often a different passage from the assumption: the assumption says the work fits in the time, and the effort section says how long it is.

Otherwise set `quantified` to false and leave every `estimate_` field null.

If the passages state no assumptions at all, return an empty list. Most passages do not state one, and an empty list is a real answer. An invented assumption is worse than a missing one: it is a claim nobody made, carrying a predicate that will expire and demand attention.
