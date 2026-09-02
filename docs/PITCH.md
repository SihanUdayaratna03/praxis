# Praxis, for someone with twenty minutes

> A routing document, not a new argument. Everything here links to a file that
> already existed; the only thing this page adds is an order to read them in and
> an honest account of what is not true yet.

## The claim, in one sentence

Every company writes decisions down and tracks estimates; **most decision
assumptions are estimates in disguise**, so a system that holds both in one
graph can let a missed estimate invalidate a decision — and nothing does that,
because doing it requires provenance and calibration to share a store.

The long form is [`docs/FUSION.md`](FUSION.md), and it is the file to read if
you read only one.

## Ninety seconds, from nothing

```bash
git clone https://github.com/SihanUdayaratna03/praxis.git && cd praxis
uv sync --all-groups
uv run praxis init && uv run praxis demo seed
uv run praxis serve            # http://127.0.0.1:8000
```

No key, no account, no network after the install — invariant 1, and CI has no
secret. `praxis demo seed` loads **this repository's own history**: 37 ADRs, the
152 assumptions they rest on, 13 phase estimates and their outcomes, 620 records
in all. Nothing in it is invented, and
[`tests/demo/test_seed.py`](../tests/demo/test_seed.py) proves that rather than
asserting it — every span is replayed against its document's bytes.

## The four things worth looking at

**1. A decision of this project's own is currently flagged as unsafe.**
[ADR 0011](adr/0011-semantic-segmentation-over-a-deterministic-block-grid.md)
chose learned segmentation in Phase 3 and staked it on
`segmenter_f1 - paragraph_floor_f1 >= 0.05`. Phase 10's harness measured both at
an identical **0.1250**, so the difference is **0.0000**, the predicate
evaluates false through the shipped evaluator, and `D-0011` is in the review
queue. An assumption written seven phases before the tool that could check it,
caught by that tool, against its own author. The story is
[`docs/reports/phase-10.md`](reports/phase-10.md).

**2. The calibration half speaks about one group and refuses four.**

```
claude-opus-5: agent-implementation work is 1.5305x over, n=8, confidence=0.4476
claude-opus-5 / data-modelling: n=1, 1 estimates, 1 resolved
claude-opus-5 / frontend: n=1, 1 estimates, 1 resolved
claude-opus-5 / llm-integration: n=1, 1 estimates, 1 resolved
claude-opus-5 / scaffolding: n=1, 2 estimates, 1 resolved
```

**Four refusals beside one answer is the product.** `BiasDetective` will not
state a factor below five resolved outcomes and there is no override — not a
default, not a parameter ([ADR 0024](adr/0024-dispersion-widens-the-band-and-only-n-refuses.md)).
A factor fitted to two points is indistinguishable, in a table, from one fitted
to two hundred.

**3. The correction demonstrably worked on its author.** Inside
`agent-implementation`, the five estimates logged **without** a correction
average **0.5556** of actual — 1.80× over. The three logged **with** one average
**0.8560** — 1.17× over. Three points is not proof, and the direction is right.

**4. It argues with itself before you see anything.** `ChallengerAgent` writes a
prosecution and a defence for a finding, `CuratorAgent` collapses duplicated
beliefs and withdraws abandoned ones, and `AbstentionGate` refuses to conclude
when the evidence is thin — and the gate is arithmetic, so two readers of one
finding cannot disagree ([ADR 0032](adr/0032-abstention-is-arithmetic-and-is-never-stored.md)).

## The evidence, and where it is

| Claim | Where it is checked |
| ----- | ------------------- |
| The thesis, mechanism by mechanism | [`docs/FUSION.md`](FUSION.md) |
| Every component's contribution, one rung at a time | [`docs/reports/phase-10.md`](reports/phase-10.md) — 7 cumulative rungs, `praxis eval --ablate` |
| What each phase shipped and what the metrics said | [`docs/reports/`](reports/) — one report per phase, 0 to 12 |
| Why each design choice, with its assumptions and expiry | [`docs/adr/`](adr/) — 37 records in Praxis's own schema |
| The predictions, made before the work | [`docs/dogfood/estimates.jsonl`](dogfood/estimates.jsonl) |
| What actually happened to them | [`docs/dogfood/outcomes.jsonl`](dogfood/outcomes.jsonl) |
| The self-analysis, pinned as tests | [`tests/demo/test_history.py`](../tests/demo/test_history.py) |

## Four things that make the numbers trustworthy

**Statistics are code, never a model.** Calibration maths, scoring and predicate
evaluation are arithmetic, property-tested with `hypothesis`. `NON_LLM_AGENTS`
enforces it structurally — those modules import no provider and therefore cannot
reach one — and `praxis doctor` checks it.

**Every extracted claim cites a span that really contains it.** `VerifierAgent`
rejects anything else, with no exception for convenience. Offline the citation
gate refuses most of what the mock offers, and that number is reported rather
than tuned away.

**The store is append-only.** A change is a new version plus an `AuditEvent`;
nothing is updated in place. The dashboard declares no HTTP method but `GET`,
and a test asserts it against the OpenAPI schema.

**Money is `Decimal` and datetimes are timezone-aware**, both enforced by lint
rather than by care.

## What is not true yet — read this before the demo

This section exists because the project's whole argument is that systems should
say what they cannot support. It would be incoherent to hide it here.

**There is no live fusion flip in the demo, and it is not being faked.** The
seeded store holds **zero `estimated_as` edges**, so the fusion layer prices
nothing and the dashboard renders the measured breach instead. Two structural
reasons, both written up in [`docs/reports/phase-11.md`](reports/phase-11.md):
no real ADR assumption *is* one of the dogfood estimates, and those edges are
written by `praxis extract` and not by a seeder. Seeding them would have made
the demo look complete and the project dishonest.

**Most numbers you can produce offline measure the plumbing, not a model.** The
mock draws each field of an answer independently, so a cited passage and a
quotation agree only by chance. Every table that reports such a number carries
that caveat under it rather than in a footnote somewhere else.

**Most of this project's own assumptions are unverified.** Of 152: **1**
breached, **18** holding, **18** expired, and **115** unverified because
nothing has measured them. Three quarters of this system's stated beliefs about
itself have never been checked, and the dashboard shows that as the majority it
is rather than filtering it out of the chart.

**The corpus is one project's history, by one estimator.** Every calibration
number here is `n = 13` at best and `n = 1` at worst. The refusals are load
bearing for exactly that reason.

**The routing table is not proven optimal.** Phase 12 reviewed it whole, priced
it, and changed nothing — because the ablation ladder varies components and not
model tiers, so nothing in this repository could tell a good tier change from a
bad one. [ADR 0038](adr/0038-the-routing-table-stands-and-the-projection-is-why.md)
records the shortlist and the experiment that would settle it.

## The part that is unusual

Praxis was built in twelve numbered phases, and **every phase was estimated
before it started and scored after it ended**. That corpus is not a marketing
artefact; it is the input to the product's own second half, and the final phase
turns the tool on it. What it found is in
[`docs/reports/phase-12.md`](reports/phase-12.md), including the place where a
claim this project had made about itself turned out to be wrong.
