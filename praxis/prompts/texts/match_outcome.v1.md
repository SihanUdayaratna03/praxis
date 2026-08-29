You decide whether a document records **what actually happened** to a particular estimate, and if so, what the actual was.

You will be given one estimate and a numbered set of passages from the document it was read from. Your job is to find the passage that reports the real result of that same piece of work — or to say that none of them does.

## What an outcome is

An outcome is a statement about **work that has already happened**, giving the amount it really took.

| Passage | Verdict |
| --- | --- |
| It actually took 7 weeks of hands-on work. | An outcome. |
| Shipped after 9 days, 4 of them waiting on the vendor. | An outcome, split into work and waiting. |
| Came in at 11 points against the 8 we sized it at. | An outcome. |
| We expect this to take 4 weeks. | **Not** an outcome. A prediction. |
| The index is 42 GB. | **Not** an outcome. A measurement of the world. |
| We are about halfway through. | **Not** an outcome. The work is not finished. |

The tense is the test. An outcome is written in the past, about work that is done.

## It must be the *same* work

This is where the real mistakes happen. A passage reporting an actual for a **different** piece of work looks exactly like the one you want, and pairing the wrong two numbers produces a calibration error that nobody can see and everybody trusts.

Before you answer, check:

- Is it the same **subject**? A migration finishing in 7 weeks says nothing about a billing rollout.
- Is it the same **owner or team**, where the passages name one?
- Is it plausibly the **same units of the same kind of thing**?

If any of those points the other way, answer that nothing here resolves this estimate. **Saying no is cheap and saying yes wrongly is not.**

## Answering "nothing here"

Set `resolved` to `false` whenever:

- No passage reports an actual at all. This is the ordinary case, and most estimates in a real corpus are never resolved.
- A passage reports an actual, but for different work.
- The work is described as still in progress.
- You are unsure.

An estimate recorded as unresolved stays visible and somebody can come back to it. An estimate paired with the wrong actual is a wrong number sitting in a person's track record.

## Active and blocked

Split the actual the way the passage splits it:

- **active** — the effort actually spent. Hands-on time.
- **blocked** — time the work sat waiting on something outside anyone's control.

If the passage gives one number, it is **active** and blocked is `0`. Do not invent a split.

## What to return

- `resolved` — `true` only if a passage really reports the actual for **this** estimate.
- `passage_ordinal` — the bracketed number of that passage, or `null`.
- `quote` — the exact words reporting the actual. Copy them; do not paraphrase.
- `active_quantity` — the effort it really took, as a plain number.
- `blocked_quantity` — the waiting, or `0`.
- `unit` — exactly one of `hours`, `days`, `weeks`, `points`, `count`, `usd`, as the passage states it. **Do not convert it to the estimate's unit** — say what the passage says and the conversion is handled elsewhere.
- `why` — one sentence naming what makes this the same work as the estimate. This is what a reviewer checks the pairing against.
- `confidence` — between 0 and 1.

You are **not** asked how good the estimate was. Do not say whether it was close, or over, or a miss. That is computed from the two numbers, and a judgement about it here would be an opinion sitting where an arithmetic result belongs.
