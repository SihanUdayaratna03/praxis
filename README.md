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
> *"1.8x under-estimation, n=14, CI [1.4, 2.3], confidence 0.79."*
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

Pre-alpha, built in numbered phases. See [`docs/reports/`](docs/reports/) for
what each phase delivered and what the metrics said.

| Phase | Scope | State |
| ----- | ----- | ----- |
| 0 | Foundation: toolchain, CI, config, logging, hooks, ADR practice | ✅ |
| 1 | Data model + SQLite graph store | ⬜ |
| 2 | LLM provider abstraction (mock / anthropic / replay) | ⬜ |
| 3 | Ingestion, segmentation, `VerifierAgent`, synthetic corpus | ⬜ |
| 4 | Half A core: scout, structurer, assumption extractor | ⬜ |
| 5 | Predicate DSL + `AssumptionMonitor` | ⬜ |
| 6 | Half B core: estimate extractor, work classifier, outcome matcher | ⬜ |
| 7 | Calibration math: bias, intervals, scoring | ⬜ |
| 8 | **The fusion layer** | ⬜ |
| 9 | Adversarial + governance: challenger, curator, abstention | ⬜ |
| 10 | Eval harness + ablation table | ⬜ |
| 11 | Dashboard + demo | ⬜ |
| 12 | Polish + Praxis analysing its own history | ⬜ |

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

## Quickstart

```bash
uv sync --all-groups          # installs Python 3.12 + all dependencies
cp .env.example .env          # optional; defaults already work offline
uv run praxis doctor          # verify the install
uv run praxis version
uv run praxis config show
```

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
  config/        settings loader and the single source of truth for model IDs
  obs/           structured logging and, later, the trace store
tests/           pytest + hypothesis
docs/adr/        architecture decision records, written in Praxis's own schema
docs/reports/    one report per phase: what shipped, what the metrics said
docs/dogfood/    Praxis's predictions about its own construction
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

By the final phase, Praxis analyses the record of its own construction and
reports where its author was wrong.

## License

MIT — see [LICENSE](LICENSE).
