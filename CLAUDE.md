# CLAUDE.md

Conventions and invariants for this repository. Read
[`ARCHITECTURE.md`](ARCHITECTURE.md) for what is being built and
[`docs/adr/`](docs/adr/) for why.

## What this project is

Praxis captures decisions with the assumptions they rest on, compiles those
assumptions into predicates that expire, logs every estimate as a prediction,
learns each estimator's bias per class of work, and **fuses the two**: most
decision assumptions are estimates in disguise, so calibration data can
invalidate a decision, and a missed estimate can be traced forward to every
decision that leaned on it.

When a design choice is unclear, pick the one that serves that thesis.

## Commands

```bash
uv sync --all-groups            # install (fetches Python 3.12 itself)
uv run pytest                   # tests
uv run pytest --cov             # tests with coverage (gate: 85%)
uv run ruff check . && uv run ruff format --check .
uv run mypy                     # strict, over praxis/ only
uv run pre-commit run --all-files
uv run praxis doctor            # verify the install needs no credentials
```

The `suite-runner` subagent runs all of these and reports only failures. Use it
before opening a PR rather than pasting a passing test run into the context.

## Invariants — do not break these

1. **No credentials, ever, to run anything.** `PRAXIS_LLM_PROVIDER=mock` is the
   default and CI has no secret. If a test, the eval harness, the CLI or the
   dashboard needs a key, that is the bug.
2. **Model identifiers live only in `praxis/config/models.py`.** Nowhere else.
   Agents ask for a `ModelRole`.
3. **Statistics are deterministic code, never a model.** Calibration maths,
   scoring and predicate evaluation are arithmetic, property-tested with
   `hypothesis`. `NON_LLM_AGENTS` enforces it; `praxis doctor` checks it.
4. **Money is `Decimal`.** Never `float`. It is summed across thousands of
   calls and read off a table by a judge.
5. **Datetimes are timezone-aware.** Ruff's `DTZ` rules enforce it. An
   assumption's expiry is a comparison against wall-clock time.
6. **Every extracted claim carries a `span_id` that really contains it.**
   `VerifierAgent` rejects anything else. No exceptions for convenience.
7. **The store is append-only.** A change is a new version plus an
   `AuditEvent`. Never update in place.
8. **`main` only moves through a reviewed, CI-green merge commit.** Never
   squash — see [ADR 0007](docs/adr/0007-merge-commits-never-squash.md).
9. **No `TODO: implement later`.** Out-of-scope work goes in
   [`BACKLOG.md`](BACKLOG.md) with a reason.
10. **Never invent an API detail.** Look it up or read the source, and cite the
    URL in a comment. The `api-researcher` subagent exists for this.

## Style

- Type hints everywhere; `mypy --strict` passes on `praxis/`.
- Functions under ~50 lines, files under ~400.
- Structured logging only. `print` is banned by ruff's `T20`; the CLI uses
  `rich`, and hooks are exempt because stdout is their protocol.
- Comments explain **why**. The code already says what.
- Absolute imports only (`ban-relative-imports = "all"`).
- Docstrings on every public module, class and function, Google convention.

## Commits

Conventional Commits, enforced by a `commit-msg` hook. 8–20 commits per phase,
one logical change each. **Bodies explain why, not what** — the diff says what.
Subject under 72 characters, lower case, no trailing period.

Types: `feat` `fix` `docs` `test` `refactor` `perf` `build` `ci` `chore`
`style` `revert`.

## Workflow per phase

1. `git checkout -b feat/phase-N-<slug>` off `main`.
2. Sub-branch per agent where a phase builds several; merge back with
   `--no-ff` so the network graph shows each as its own strand.
3. 8–20 commits, all green.
4. Push, `gh pr create` with what changed and why, how it was tested, the
   metrics delta, and the ADRs recorded.
5. `gh pr checks --watch`, then `gh pr merge --merge --delete-branch`.
6. Tag `v0.N-phase-N`, push tags, **verify against the remote** rather than
   assume.
7. Write `docs/reports/phase-N.md`.
8. Close the dogfood estimate with an actual outcome.
9. Summarise in Sinhala, then stop.

## Hooks

`.claude/settings.json` wires three, and they are enforcement rather than
advice:

| Hook | Fires on | Blocks |
| ---- | -------- | ------ |
| `guard_no_secrets.py` | Write, Edit | Credential-shaped content, real `.env` files |
| `guard_git_workflow.py` | Bash | Commits on `main`, any `--squash`, bare force-push |
| `post_edit_verify.py` | Write, Edit | Ruff or mypy failures on the edited file |

The same rules run in pre-commit, so they hold for commits made by hand too.

If a hook blocks you, it is usually right. Fix the cause.

## Dogfooding

Praxis runs on its own history, so the history has to be a valid corpus:

- Every architectural decision is an ADR in Praxis's own schema, with
  predicates and expiry conditions.
- Every phase estimate is logged in `docs/dogfood/estimates.jsonl` **before**
  the work starts.
- The real duration is appended to `docs/dogfood/outcomes.jsonl` when the phase
  closes.

By Phase 12 the demo is Praxis reporting where its author was wrong. Do not
skip the estimate — a phase with no prediction is a hole in that demo.

## Language

Code, comments, commits, branches, file names, PR bodies and documentation:
**English**. Progress explanations to the product owner: **Sinhala**.
