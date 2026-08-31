---
id: ADR-0032
status: accepted
date: 2026-08-31
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0032 — Decide abstention by arithmetic, and never store the result

## Chosen

`AbstentionGate` routes a finding to `EMIT`, `NEEDS_HUMAN` or `DROPPED` from
four checks over fields the record already carries. It makes **no model call**
and joins `NON_LLM_AGENTS`. The disposition is **recomputed on every read and
never written to the store**.

## Rejected

| Option | Why not |
| ------ | ------- |
| Store the disposition on the `Finding` | A migration, and a wrong one. A disposition is derived from a confidence, a verdict, a citation count and a retraction flag, every one of which can move after it is computed — a challenge lands, a subject is withdrawn, a confidence is revised. `praxis.agents.calibration` makes exactly this argument about a calibration factor and the reasoning transfers unchanged: a stale refusal is worse than no refusal, because it reads as a decision somebody made. |
| Add a `NEEDS_HUMAN` member to `FindingKind` | A finding's *kind* is what it alleges. Where it should go is not what it alleges, and conflating the two would mean a stale decision that fails the citation rule stops being a stale decision. It is also a migration, since the SQL `CHECK` constraints mirror the enum. |
| Give the gate a model, as ADR 0006 originally routed it | Its whole input is a confidence, a count, two enums and a boolean. There is no prose in the decision anywhere. Invariant 3 forbids exactly this, and a model asked whether 0.4 is below 0.5 will occasionally say no. |
| Fold it into `ChallengerAgent`, since both gate the same findings | They answer different questions and fail differently. The challenger asks whether the *argument* holds; this asks whether the *evidence* does. A finding can survive a rigorous challenge on a confidence of 0.3, and a merged component would have to be right about both at once with one number. |
| Short-circuit on the first failed rule | Cheaper by nothing measurable, and it costs a round trip per defect: a person told a finding is uncited fixes the citation and is sent straight back because it was also about a withdrawn record. All four rules run and all failures are reported. |
| Abstain on every finding with no evidence spans | Would abstain on every fusion finding the product exists to produce. `Finding.evidence_span_ids` is documented since Phase 1 as legitimately empty for a computed finding — a calibration factor is not something a document says. The rule is per `FindingKind`, and `QUOTING_KINDS` names the two that owe a citation. |

## What was known at the time

`ARCHITECTURE.md` has said since Phase 0 that no high-severity finding reaches a
human without surviving `ChallengerAgent`. Phase 9 built the challenger, which
made "and then what" a real question for the first time.

Three producers write findings — `praxis.monitor.breach`,
`praxis.agents.calibration` and `praxis.agents.fusion_pass` — and all three
write at version 1 with `Verdict.UNDECIDED`. Two of the five `FindingKind`
members quote a document; three are computed, and `Finding.evidence_span_ids`
already documented the distinction.

`Repository.get` returns a retracted record rather than `None`, which Phase 8
found the hard way and recorded in the handover. That is why a withdrawn subject
is a check rather than an absence.

What was **not** known: what fraction of real findings this refuses.
`DEFAULT_CONFIDENCE_FLOOR = 0.5` is a judgement, and against the offline corpus
there are no findings to route at all, so the rate is unmeasured rather than
measured-and-low. Assumption 4 is written to fire on the first real number.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Nothing about abstention is ever persisted, so a disposition cannot go stale | `stored_dispositions == 0` | `on_event("a disposition is written to the store")` |
| 2 | The gate stays deterministic and store-free, so two readers of one finding agree | `gate_model_calls == 0` | `on_event("AbstentionGate acquires a provider")` |
| 3 | The three computed finding kinds keep being legitimately uncited, so the per-kind rule is right | `uncited_computed_findings >= 1` | `on_event("every computed finding in a real store cites a span")` |
| 4 | Abstention stays a minority of emittable findings — a gate, not a wall | `abstention_rate <= 0.5` | `on_event("a real run abstains on more than half of what it could emit")` |

Assumption 4 is the one that would most change the design if it fired. A gate
refusing most of what it sees is not a gate, and the honest response would be to
find which rule is doing it rather than to lower the floor.

## Consequences

**Accepted costs.** Every reader of a finding pays the routing arithmetic again,
and a caller wanting a disposition has to hold the finding plus the knowledge of
whether its subject was retracted. `route_all` exists so a caller with a store
resolves the withdrawn set once, but the coupling is real: the gate cannot
answer from a finding alone.

The abstention rate has no offline number, because the corpus produces no
findings against the mock (ADR 0016's citation gate). That is reported as an
absent measurement rather than as a low rate, and `GateResult.considered` is
printed beside the rate so the two cannot be confused.

A `NEEDS_HUMAN` disposition is a feature and is reported plainly. It is not
driven toward zero, and no rule is loosened to move the number — the stance this
project has taken on every refusal since `BiasDetective` declined to speak below
`n = 5`.

**Reversal cost.** Storing a disposition later would be a migration plus a
staleness problem to solve, and the staleness problem is the expensive half —
every write that touches a finding, a challenge or a subject's retraction would
have to invalidate it. Making the gate a model call is one line in the routing
table plus a prompt, and would cost the property that two readers of one finding
always agree.
