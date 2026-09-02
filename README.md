# Praxis

**Organizational memory that argues with itself.**

Every company already writes decisions down and already tracks estimates. Both
end up as dead text: a decision doc nobody revisits, a ticket nobody scores.
Praxis makes them living objects that invalidate each other.

---

## The thesis

Praxis has two halves and one idea that only exists because both halves are in
the same system.

### Half A — Decision provenance

A decision is captured with what was chosen, what was rejected, who decided,
what was known at the time, and — the part that matters — **what assumptions it
rests on**. Each assumption is compiled into a machine-checkable predicate with
an expiry condition:

```
fuel_price_lkr < 350
team_size >= 4
vendor_sla_pct >= 99.5
migration_weeks <= 6
```

When a predicate stops holding, every decision resting on it is flagged for
review. Decision documents do not rot silently; they raise their hand.

### Half B — Estimation calibration

Every quantified forward-looking claim anyone makes is logged as a prediction
with units, owner and stated confidence. When reality arrives, outcomes are
matched back. Praxis learns directional bias per person **per class of work** —
because the same engineer who is accurate on UI work can be 2x optimistic on
data migrations, and an org-wide average hides exactly that.

### The fusion — why these are one product

Most decision assumptions are estimates in disguise.

> `AssumptionMonitor` → `CalibrationEngine`:
> *"D-0042 assumed the migration takes 6 weeks. What is this team's calibration
> factor for `data-migration` work?"*
>
> `CalibrationEngine` → :
> *"1.8x under-estimation, n=14, band [1.4, 2.3], confidence 0.59."*
>
> Praxis emits:
> *"D-0042 is probably built on a 40% under-estimate. Re-examine it."*

And in reverse: when an estimate misses badly, `CollateralAgent` walks the
provenance graph to find every decision that was justified by that estimate and
surfaces them as collateral damage.

No competing product does this, because doing it requires provenance and
calibration to share one graph.

---

## Status

Built in numbered phases, each one merged green with a report. See
[`docs/reports/`](docs/reports/) for what each delivered and what the metrics
said.

| Phase | Scope | State |
| ----- | ----- | ----- |
| 0 | Foundation: toolchain, CI, config, logging, hooks, ADR practice | ✅ |
| 1 | Data model + SQLite graph store | ✅ |
| 2 | LLM provider abstraction (mock / anthropic / replay) | ✅ |
| 3 | Ingestion, segmentation, `VerifierAgent`, synthetic corpus | ✅ |
| 4 | Half A core: scout, structurer, assumption extractor | ✅ |
| 5 | Predicate DSL + `AssumptionMonitor` | ✅ |
| 6 | Half B core: estimate extractor, work classifier, outcome matcher | ✅ |
| 7 | Calibration math: bias, intervals, scoring | ✅ |
| 8 | **The fusion layer** | ✅ |
| 9 | Adversarial + governance: challenger, curator, abstention | ✅ |
| 10 | Eval harness + ablation table | ✅ |
| 11 | Dashboard + demo | ✅ |
| 12 | Polish + Praxis analysing its own history | ✅ |

---

## Runs with no API key, by design

Praxis reaches models only through an `LLMProvider` interface with three
implementations:

| Provider | Use |
| -------- | --- |
| `mock` *(default)* | Deterministic, offline, zero credentials. Drives the full pipeline, test suite, eval harness, CLI and dashboard. |
| `anthropic` | Live models. |
| `replay` | Replays recorded responses from fixture files. |

Switching is one environment variable:

```bash
PRAXIS_LLM_PROVIDER=mock      # or: anthropic | replay
```

This is not a workaround for a missing key. Agent systems are only testable if
the model layer is swappable and reproducible: deterministic replay is what lets
CI assert on agent behaviour, what makes evaluation results comparable between
commits, and what makes a bug reproducible instead of anecdotal. The offline
path is the primary path, and it is the one CI exercises on every push.

---

## Quickstart — four commands to a real finding

No API key, no account, no network after the install. Every command below was
run against a fresh clone of this repository before it was written down.

```bash
git clone https://github.com/SihanUdayaratna03/praxis.git && cd praxis
uv sync --all-groups          # fetches Python 3.12 and every dependency
uv run praxis init            # create the store
uv run praxis demo seed       # load Praxis's own history into it
```

`praxis demo seed` reads this repository's own `docs/adr/` and
`docs/dogfood/` — 36 architecture decisions, the 149 assumptions they rest on,
and 13 phase estimates with their outcomes — then runs the monitor over them.
It writes 608 records and prints this:

```
149 predicate(s) evaluated against docs/dogfood/facts.json — 1 breached
  The predicate `segmenter_f1 - paragraph_floor_f1 >= 0.05` evaluated false
  against the facts this run was given, so the assumption "Block grouping
  extracts better than the deterministic paragraph floor" no longer holds.
  Resting on it: D-0011.
```

**That is a real finding about this repository, not a fixture.** ADR 0011 chose
learned segmentation over a paragraph grid in Phase 3 and staked the choice on a
number nobody could measure yet. Phase 10's eval harness measured it. The two
approaches scored an identical F1 of 0.1250, the difference is 0.0000 against a
required 0.05, and the decision that rested on it is now flagged for review by
the system it is part of. The whole story is in
[`docs/reports/phase-10.md`](docs/reports/phase-10.md).

Then look at it:

```bash
uv run praxis serve           # http://127.0.0.1:8000
```

Eight views over the same store: the decisions and what each one assumes, the
assumption health breakdown, the calibration lens, the review queue and the
append-only audit trail. Read-only — the API declares no HTTP method but `GET`,
and a test asserts it.

```bash
uv run praxis calibrate       # what this project's estimates say about its author
uv run praxis doctor          # verify the install needs no credentials
uv run praxis config          # resolved settings and the model routing table
```

`praxis calibrate` is where the second half speaks. It measures one group and
refuses four, by name and with a reason:

```
claude-opus-5: agent-implementation work is 1.5305x over, n=8, confidence=0.4476
claude-opus-5 / data-modelling: n=1, 1 estimates, 1 resolved
claude-opus-5 / frontend: n=1, 1 estimates, 1 resolved
claude-opus-5 / llm-integration: n=1, 1 estimates, 1 resolved
claude-opus-5 / scaffolding: n=1, 2 estimates, 1 resolved
```

**Four refusals beside one answer is the product working, not failing.**
`BiasDetective` will not state a factor below five resolved outcomes and there
is no override — see
[ADR 0024](docs/adr/0024-dispersion-widens-the-band-and-only-n-refuses.md).

Structured logs go to stderr, so `2>/dev/null` gives you the tables alone.
A store already holding decisions is refused rather than seeded twice: the store
is append-only, so point `PRAXIS_DATA_DIR` at an empty directory to start over.

### The extraction pipeline, on a synthetic corpus

The demo above seeds from committed history. To watch the agents actually read
documents, generate the graded corpus instead:

```bash
export PRAXIS_DATA_DIR=.praxis-corpus                # a second store, not the demo's
uv run praxis init                                   # create it
uv run praxis corpus generate .praxis-tmp/corpus     # 82 documents + answer key
uv run praxis ingest .praxis-tmp/corpus/documents    # documents -> verified spans
uv run praxis extract                                # spans -> decisions, assumptions, estimates
uv run praxis store stats                            # what the store holds

uv run praxis eval .praxis-tmp/corpus                # grade it against the key
uv run praxis eval .praxis-tmp/corpus --ablate       # and rung by rung
```

The separate `PRAXIS_DATA_DIR` matters: the store is append-only, so seeding the
demo and ingesting a corpus into one store leaves you reading both at once.

`praxis eval` prints precision, recall and field accuracy per record kind, how
often a citation survived the gate, the recall over the `estimated_as` edges the
corpus labels — the fusion relationship the product exists to find — and cost
per document per agent. `--ablate` runs the same grading over seven cumulative
rungs, each in a store of its own, so every component's contribution is a row
rather than an assertion. See
[`docs/reports/phase-10.md`](docs/reports/phase-10.md) for the table it produced.

Offline those numbers measure the plumbing rather than a model, and the report
says so beneath itself rather than only here.

Run the checks exactly as CI does:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

---

## Repository layout

```
praxis/          library and CLI (the only place typed business logic lives)
  agents/        every agent, and the arithmetic ones that must never call a model
  config/        settings loader and the single source of truth for model IDs
  corpus/        the synthetic corpus generator and its machine-gradeable key
  demo/          seeding a store from this repository's own committed history
  domain/        the records, typed ids, spans and edge types
  eval/          the harness, the metrics and the ablation ladder
  ingest/        adapters, segmentation and the citation verifier
  llm/           the provider interface: mock, anthropic, replay, and the traces
  monitor/       evaluating assumptions against facts, and what a breach is
  obs/           structured logging
  predicates/    the small total predicate language: lexer, parser, evaluator
  prompts/       prompts as versioned stored artefacts
  store/         SQLite schema, migrations, repository, append-only audit trail
  web/           the read-only FastAPI layer and the dashboard it serves
tests/           pytest + hypothesis
docs/adr/        architecture decision records, written in Praxis's own schema
docs/reports/    one report per phase: what shipped, what the metrics said
docs/dogfood/    Praxis's predictions about its own construction, and the facts
docs/FUSION.md   the claim the whole product exists to make, in detail
.claude/         Claude Code hooks and conventions used to build this repo
```

---

## Built with Claude Code, deliberately

This repository is also an argument about how to build with an agentic coding
tool, so the mechanics are committed rather than described:

- [`CLAUDE.md`](CLAUDE.md) holds the conventions and the invariants that must
  never break — it is loaded into every session.
- [`.claude/hooks/`](.claude/hooks/) enforces the rules that actually matter
  deterministically instead of trusting prose: secrets can't be written, `main`
  can't be committed to directly, and `ruff`/`mypy` run after every edit.
- Model routing (cheap model for high-recall scanning, strong model for
  reasoning) is a recorded decision, not an accident — see
  [`docs/adr/0006-model-routing-table.md`](docs/adr/0006-model-routing-table.md).

## Dogfooding

Praxis is run against its own development history. Every architectural decision
is an ADR written in Praxis's own schema, complete with assumptions and expiry
conditions. Every estimate of how long a phase would take is logged as a
prediction in [`docs/dogfood/estimates.jsonl`](docs/dogfood/estimates.jsonl),
and the real duration is fed back as an outcome when the phase closes.

In the final phase Praxis analysed the record of its own construction and
reported where its author was wrong. The write-up is
[`docs/reports/phase-12.md`](docs/reports/phase-12.md); the short version is
that one architectural decision is standing on a measurement that contradicts
it, that this author over-estimates `agent-implementation` work by 1.53× at
n = 8, and that the correction — once he started applying it — moved him from
1.80× over to 1.17× over.

## License

MIT — see [LICENSE](LICENSE).
