---
id: ADR-0021
status: accepted
date: 2026-08-29
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0021 — `match_quality` is arithmetic, and the module holding it cannot reach a model

## Chosen

`Outcome.match_quality` is **computed from two numbers** and is never asked of a
model. `OutcomeMatcher`'s prompt supplies the actual quantity and the passage it
came from; the band — `exact`, `close`, `partial`, `miss` — is derived from the
ratio of the larger active quantity to the smaller:

| Band | Ratio |
| ---- | ----- |
| `exact` | < 1.10 |
| `close` | < 1.50 |
| `partial` | < 2.50 |
| `miss` | ≥ 2.50 |

A zero on one side has no ratio: an estimate of no hands-on effort answered by
real hands-on effort is a `miss` with **no ratio reported**, because reporting
one would be inventing it. Both zero agree exactly.

The prompt says in as many words that the judgement is not being requested, and
a test asserts that sentence is in the prompt.

**The enforcement is structural, not remembered.** `quality_for`, `converted`
and `candidates_for` live in `praxis/agents/reconciliation.py`, which imports no
`LLMProvider` and cannot reach one. `praxis/agents/matcher.py` is the half that
asks a model. Invariant 3 says statistics are deterministic code; putting the
function in a module with no route to a model makes that true by construction
rather than by discipline.

Unit reconciliation sits on the same side of the line. Hours, days and weeks
convert at one **stated convention** — eight hours to a working day, five days
to a week — and the conversion is written into `Outcome.notes` whenever it
fires, so a converted number can be read back to what the document said. Across
families there is no honest rate: `points` are a team's own scale, a `count`
counts different things in two documents, and turning `usd` into `weeks` needs a
labour rate nobody wrote down. Those pairings are **refused**, not converted.

## Rejected

| Option | Why not |
| ------ | ------- |
| Ask the model how well the estimate did | The one field in this record a model would fill in most willingly and least reproducibly. It feeds `BiasDetective` and `ScoringAgent`, both of which are deterministic precisely so the calibration numbers are falsifiable; an opinion in this column would be indistinguishable in the eval table from a number and would make every downstream statistic unreproducible. |
| Keep the banding in `matcher.py` beside the agent | Correct today and one convenient import from being wrong. The rule "this function must not call a model" is exactly the kind that survives the commit stating it and dies three phases later. A module that *cannot* import a provider needs no rule. |
| Store the ratio and let each reader band it | Two readers would band it two ways and the table would stop being comparable across phases. The record is what a person reads next month; a band it does not carry is a band nobody agreed on. |
| Report an infinite ratio when one side is zero | A ratio that does not exist, printed as though it did. `miss` with no ratio says exactly what happened; `inf` invites arithmetic on it. |
| Convert every unit against a common scale | Requires a rate for `points` and `usd` that nobody stated. Inventing one puts a fabricated constant underneath every calibration factor computed from a converted row, where it is invisible. |
| Refuse every conversion, including hours to weeks | Loses real matches to protect against a risk the `active`/`blocked` split already limits. A stated convention written into the row is auditable; a lost pairing is not recoverable. |

## What was known at the time

**The bands were checked against this project's own six hand-scored outcomes,
and they reproduce five.** The sixth is a deliberate disagreement, pinned by a
test so that widening a band to make it agree would fail rather than pass
quietly:

| Outcome | Active est → actual | Ratio | Computed | Hand-scored |
| ------- | ------------------- | ----- | -------- | ----------- |
| `OUT-0001` | 2.5 → 1.1 | 2.27× | `partial` | `partial` |
| `OUT-0002` | 2.0 → 4.2 | 2.10× | `partial` | **`miss`** |
| `OUT-0003` | 4.5 → 5.6 | 1.24× | `close` | `close` |
| `OUT-0004` | 5.5 → 4.2 | 1.31× | `close` | `close` |
| `OUT-0005` | 6.0 → 2.75 | 2.18× | `partial` | `partial` |
| `OUT-0006` | 6.5 → 4.6 | 1.41× | `close` | `close` |

`OUT-0002` was hand-scored `miss` because the estimate's stated **conditions**
also failed, not because of the ratio. That is a judgement this function cannot
make and must not pretend to: it compares two numbers and cannot see whether an
estimate's assumptions held. The disagreement is reported as a finding rather
than tuned away, because tuning the measurement to match the measurer is how a
calibration system stops measuring anything.

The thresholds themselves are a choice, not a derivation. Six points is not
enough to fit them from, and fitting them to six points drawn from one estimator
would be over-fitting with extra steps.

Not known: whether these bands mean anything to a second organisation. They are
reported alongside the raw quantities, so a reader who disagrees can re-band
without re-running anything.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The deterministic half never acquires a model route | `reconciliation_module_llm_imports == 0` | `on_event("a fourth Half B agent is written")` |
| 2 | The bands agree with a human's scoring most of the time | `band_agreement_rate >= 0.8` | `when(stored_outcomes >= 30)` |
| 3 | Converting there and back is exact, so a round trip never drifts | `unit_round_trip_error == 0` | `on_event("a non-time unit becomes convertible")` |
| 4 | Refusing incomparable units costs few real pairings | `incomparable_unit_refusals_share <= 0.05` | `when(stored_outcomes >= 30)` |

## Consequences

**Accepted costs.** The band cannot see context. An estimate that was 2× out
because its scope tripled and one that was 2× out because the estimator is
optimistic get the same `partial`, and only the second is a calibration signal.
`Estimate.conditions` holds the material that would tell them apart and nothing
yet reads it — that is Phase 7's problem and this ADR does not pre-empt it.

The working-day convention is a Western office assumption sitting in a constant.
It is named, tested and written into every row it touches, which is the most
this phase can honestly do about it.

**Reversal cost.** Low for the thresholds — four constants and a table in this
file. High for the *placement*: moving `quality_for` back beside the agent would
be a small diff and would quietly restore the possibility the split exists to
remove, and every outcome the system had ever banded would need re-reading under
the new rule.
