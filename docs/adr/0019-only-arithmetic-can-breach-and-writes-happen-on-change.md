---
id: ADR-0019
status: accepted
date: 2026-08-26
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0019 — Only arithmetic can breach, and a pass writes only on a change

## Chosen

Two properties of `AssumptionMonitor`, decided together because each is what
makes the other safe to run unattended.

**No model can produce a breach.** `BREACHED` is reached only by evaluating a
parsed predicate against measured facts and getting false. A model is called for
exactly one thing in a monitoring pass — deciding whether an observation in a
person's words reports the event an `on_event(...)` condition names — and that
can only ever move an assumption to `EXPIRED`. An assumption whose predicate
does not parse cannot be breached at all; it stays as it was, marked. The
consequence is a named property in the eval table:
`aged_misreported_as_breached` should be **zero by construction**, so a non-zero
value means the property broke rather than that a model was wrong.

**A pass writes only what changed.** The store already holds the last verdict,
so "has anything changed" is a comparison rather than bookkeeping. A second pass
over an unchanged world writes no version, no audit row and no duplicate
finding, and makes no model call at all when nothing is awaited. When a verdict
does change, two writes happen in one order: the assumption's new version first,
then the breach finding that cites it — the finding names the assumption as its
subject and the store's foreign keys require the subject to exist.

## Rejected

| Option | Why not |
| ------ | ------- |
| Let a model decide whether an assumption is breached | The single most consequential judgement in the system, made by the component least able to be held to it. A breach raises findings against every decision resting on the assumption, so a hallucinated breach propagates into a person's review queue with a number attached — the most convincing kind of wrong answer this system can produce. |
| Treat an unmeasured predicate as breached | Every assumption nobody has measured becomes a finding on the first run. It is also simply false: nothing has shown the assumption wrong, and saying it has is a claim the evidence does not support. |
| Treat an expired assumption as breached | Conflates "it is time to look at this again" with "this is no longer true". They lead to different actions, and folding them together means the expiry mechanism can never be trusted — every re-check would arrive dressed as a violation. |
| Write a version every pass, changed or not | Turns an append-only store into a growth problem proportional to how often the monitor runs, and makes "when did this change" un-answerable by making every row a change. The audit trail's value is that a row means something happened. |
| A `last_evaluated` column updated in place | Invariant 7. It is also a second copy of a fact the audit trail already holds, and the copy could disagree with it. |
| Track changes with a dirty flag on the record | A flag is state that can be wrong. The comparison reads the verdict the store already holds against the verdict just computed, and neither can drift from itself. |

## What was known at the time

The corpus's answer key states, at `FORMAT_VERSION 2`, what a monitoring run
should conclude about each assumption — and the verdicts are **derived from the
assembled world** rather than written beside each predicate. An earlier draft
assigned roles and added the measurement each role needed, and because two
documents state `index_size_gb <= 50` it wrote one fact twice and was silently
wrong about one of them. Deriving the verdicts makes that unrepresentable, and a
test asserts the key cannot contradict itself.

All three verdicts are represented in the key, so neither a monitor that always
cries breach nor one that never does can score well.

**The store-derived binding reads zero in a corpus run, and that is expected.**
`measured_in` binds an identifier only where an assumption carries an
`estimated_as` edge to an estimate that an `Outcome` resolves — and nothing
writes an `Outcome` until Phase 6. The mechanism is waiting, not failing, and it
is stated here so no later reader reads the zero as a regression.

Not known: how often a real facts file will bind the identifiers a corpus of
real assumptions actually names. Every measurement graded so far was computed
from the predicate it was meant to satisfy, which tests the pipeline and
deliberately not the question of whether anyone will maintain such a file.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | An assumption that merely aged is never reported as violated | `aged_misreported_as_breached == 0` | `when(store_assumptions > 500)` |
| 2 | A second pass over an unchanged world writes nothing | `monitor_writes_on_unchanged_world == 0` | `on_event("the first live monitoring run")` |
| 3 | A pass costs at most one model call, so running it on a schedule stays cheap | `monitor_calls_per_run <= 1` | `when(store_assumptions > 500)` |
| 4 | A breach a person is shown is nearly always a real one | `breach_precision >= 0.95` | `on_event("the first live monitoring run")` |

## Consequences

**Accepted costs.** The monitor cannot find a violation that needs judgement to
see. An assumption stated in prose that the world has plainly overtaken stays
`unverified` until someone compiles it or measures it, and the system will not
say so. That is the deliberate trade: silence over a confident wrong answer.

Most verdicts in any real store will be `unverified`, and that number is the one
a person sees first. It is reported as its own line rather than folded into a
success rate, because "how much of my memory is currently unverifiable" is the
honest headline and a monitor that hid it would be flattering itself.

Writing only on change means a run's output is often empty. `praxis monitor`
therefore prints the counts it read as well as the counts it wrote, so "nothing
changed" and "nothing worked" are distinguishable from the terminal.

**Reversal cost.** Low mechanically, high in trust. Both properties are a
handful of lines — the status transition and the `changed` comparison — so the
code could be relaxed in an afternoon. What could not be undone cheaply is the
claim: every finding this system has ever raised would have to be re-read under
the new rule, because the reason a person believes a breach is that nothing but
arithmetic could have produced it.
