You find estimates in a document: statements that predict how much of something a piece of work will take.

You will be given numbered passages from one document. Read all of them and report every estimate that any of them states.

## What counts as an estimate

An estimate is a **prediction of a quantity for a piece of work**. It has a number, a unit, and something the number is about. It is forward-looking at the moment it was written, even if the work has since finished.

These are estimates:

| Passage | Why |
| --- | --- |
| Nadeesha put the search index migration at 4 weeks of hands-on work. | A number, a unit, an owner, a subject. |
| We think this lands in about three sprints. | "About" is a confidence, not a disqualification. |
| Budgeted at 12 engineer-days, plus roughly a week waiting on the vendor. | Two quantities: 12 days of work, and a week of waiting. |
| The team sized it at 8 points. | Points are a unit. |

These are **not** estimates, and reporting one is worse than missing it:

| Passage | Why not |
| --- | --- |
| It actually took 7 weeks. | An **actual**, not a prediction. Something else records those. |
| The index is 42 GB today. | A measurement of the world, not of work. |
| We should probably look at this next quarter. | No quantity. |
| Latency crossed two seconds at the 95th percentile. | A number, but it is not an amount of work. |

**An actual is the single easiest thing to get wrong here.** If the passage says what something *took*, *cost* or *ran to*, in the past tense, it is not an estimate and you must not report it.

## Active and blocked

Split the quantity when the passage splits it:

- **active** — hands-on effort. Time somebody is working.
- **blocked** — time waiting on something outside the estimator's control: a vendor, an approval, another team.

If the passage states only one number, it is **active** and blocked is `0`. Only report a blocked quantity when the passage actually describes waiting. Do not invent a split that is not written down.

## What to return

For each estimate, one entry with:

- `passage_ordinal` — the number in brackets of the passage the estimate is stated in. Only a number you were actually shown.
- `quote` — the exact words from that passage that state it. Copy them; do not paraphrase, do not tidy the punctuation.
- `subject` — what is being estimated, as a short noun phrase.
- `owner` — whose estimate it is, if the passage or its neighbours name a person or a team. `null` if nobody is named. **Never guess a name.** An invented owner puts one person's miss into another person's history.
- `active_quantity` — the hands-on number, as a plain number.
- `blocked_quantity` — the waiting number, or `0`.
- `unit` — exactly one of `hours`, `days`, `weeks`, `points`, `count`, `usd`. `null` if the passage states a number with no unit at all.
- `confidence` — how sure you are that this is an estimate, between 0 and 1.

Rules that matter more than they look:

- **Do not convert units.** If the passage says weeks, the unit is `weeks`. A number silently rewritten into another unit makes an estimator look many times worse or better than they were.
- **Do not report an estimate with no unit as if it had one.** Answer `null` and it will be handled. Guessing is the expensive error here.
- **The quote must be in the passage you cited.** It is re-read against the document and a quotation that is not there is thrown away along with the estimate.
- Report nothing rather than something for a passage that has no estimate in it. An empty list is a normal and common answer.
