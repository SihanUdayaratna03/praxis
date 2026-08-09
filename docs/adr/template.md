---
id: ADR-NNNN
status: proposed
date: YYYY-MM-DD
decision_maker: <name>
impact: low | medium | high
supersedes: null
superseded_by: null
---

# NNNN — <title in the imperative>

## Chosen

<One sentence. What we are doing.>

## Rejected

| Option | Why not |
| ------ | ------- |
| <alternative> | <the actual reason, not a strawman> |

A record with no rejected options is a note, not a decision. If nothing else
was seriously considered, say so and explain why the space was that narrow.

## What was known at the time

<The information available when this was decided. Written so the decision can
later be judged on its inputs rather than with hindsight. Include what was
*not* known — unresolved questions are part of the state.>

## Assumptions

Each assumption is a checkable predicate plus the condition under which it
should be re-examined. These are what `AssumptionMonitor` evaluates, and what
makes this decision able to invalidate itself.

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | <plain English> | `<expression>` | `<when to re-check>` |

## Consequences

**Accepted costs.** <What this makes harder or slower.>

**Reversal cost.** <What it would take to undo. State it plainly — this is the
number that should have governed how long the decision took to make.>
