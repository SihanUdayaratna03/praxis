# Handover — start of Phase 2

Read this first, then `CLAUDE.md`. Written at the close of Phase 1 so the next
session can start working instead of re-deriving state.

---

## Where we stopped

Phase 1 is merged, tagged and green. Nothing is in flight.

| | |
| --- | --- |
| `main` | `cb0b396`, local and remote identical |
| Tag | `v0.1-phase-1` → `cb0b396` (verified through the API, not assumed) |
| CI | 5/5 green on the PR, green on `main` after the merge |
| Open PRs | none |
| Remote branches | `main` only |
| Working tree | clean |
| Suite | **453 passed**, coverage **98.17%** (gate 85%) |

```bash
cd "C:\Users\sihan\OneDrive\Desktop\Praxis Agents"
git checkout main && git pull
uv sync --all-groups
uv run praxis doctor          # expect OK
uv run pytest                 # expect 453 passed
```

**Shipped in Phase 1:** the nine Pydantic records, typed ids in two schemes, the
SQLite schema with append-only triggers and FTS5, forward-only migrations, the
repository / audit / graph / reports layer, `praxis init`, `praxis store stats`,
the ADR 0010 sync-root check on `doctor`, ADRs 0008 and 0009, and 331 new tests.

**Deliberately not shipped:** any LLM call, any agent, any orchestration.

The full account — what was built, what went wrong, and the estimate versus the
actual — is in [`phase-1.md`](phase-1.md). Read it before writing `EST-0003`;
the last section is addressed to the session that does.

---

## Phase 1's own numbers

| | |
| --- | --- |
| `EST-0002` | 2.0 hours of active engineering, confidence 0.5, class `data-modelling` |
| `OUT-0002` | ~4.2 hours active, 72.7 hours wall clock, scored **`miss`** |
| Miss direction | **under-estimated, ~2.1×** |

The estimate priced the records and the schema, which cost about what it said,
and did not price the four store test modules that make them trustworthy —
roughly 1,700 lines and over half the engineering. **Estimate the tests, not the
feature.** That is the one claim `EST-0003` can be checked against, so put it in
that estimate's conditions where `ScoringAgent` will find it.

Two outcomes now exist and they disagree in direction: `scaffolding`
over-estimated 2.3×, `data-modelling` under-estimated 2.1×, `n = 1` each. Do not
correct `EST-0003` for either. `BiasDetective` refuses below `n = 5` and so
should its author — that refusal is the product's own thesis, and the first
place to break it would be here.

---

## What Phase 2 is

The model access layer from [`ARCHITECTURE.md`](../../ARCHITECTURE.md) §Model
access: `LLMProvider` with `MockProvider` (default, offline), `AnthropicProvider`
(live) and `ReplayProvider` (fixtures), every call recorded to the trace store
with agent name, model, prompt hash, token counts, latency and cost. ADRs
[0005](../adr/0005-offline-first-llm-provider.md) and
[0006](../adr/0006-model-routing-table.md) already decide the shape; Phase 2
implements them.

Do not design it here. The first action of the phase is the estimate, before any
file is created:

1. `git checkout -b feat/phase-2-<slug>` off `main`.
2. Append `EST-0003` to `docs/dogfood/estimates.jsonl` **before** the work
   starts. A phase with no prediction is a hole in the Phase 12 demo.
3. Then work the phase per `CLAUDE.md` § Workflow per phase.

One boundary to settle early rather than discover in Phase 4: the trace store
records what an agent was *told*, and the audit trail records what it *changed*.
They are different things and should not become one table.

---

## Things a cold session will otherwise rediscover the hard way

Carried forward from Phase 1 because every one of them cost something.

- **Long text goes to a file.** This shell has a ~965-byte parse limit and
  PowerShell 5.1 mangles embedded quotes passed to native executables. Use
  `gh pr create --body-file .praxis-tmp/pr-body.md` and
  `git commit -F .praxis-tmp/commit-msg.txt`. `.praxis-tmp/` is gitignored.
- **Branch protection does not exist** — `PUT .../branches/main/protection`
  returns `403 Upgrade to GitHub Pro`. `main` is protected only by the
  pre-commit `no-commit-to-branch` hook and `guard_git_workflow.py`, both
  client-side. Branch and open a PR even for a one-line docs change.
- **The `post_edit_verify` hook runs `ruff --fix`**, which deletes an import
  added in one edit before the edit that uses it lands. Add the import and its
  use in the same edit.
- **A check about other people's machines must not be tested only on this one.**
  Four `test_location.py` cases were green on Windows and red in CI for two days
  because Windows path literals were spelled as `Path`; under a POSIX `Path` a
  backslash is an ordinary character. Use `PureWindowsPath` / `PurePosixPath`.
- **`executescript` commits any open transaction before it runs**, so migration
  transaction control lives *inside* the script. Do not tidy that into a
  `with transaction(...)` block — it silently makes migrations non-atomic.
- **An FTS5 table cannot be aliased on the left of `MATCH`.**
- **`strategy.example()` inside a test raises** under
  `filterwarnings = ["error"]`. Compose the strategy into the `@given`.
- **Editing a file with a Python script on this machine writes CRLF**, and the
  pre-commit `mixed line ending` hook rewrites it and aborts the commit. Just
  `git add` again and re-run the commit; the second one succeeds.
- **Nothing in `praxis/store/` is expected to need further work.** If a later
  test finds a bug, fix it there — but do not redesign it, and do not "restore"
  the nine `<kind>_current` views ADR 0009 deliberately replaced with three.

---

## Owner decisions — still standing, do not re-ask

1. **Store location.** Settled in
   [ADR 0010](../adr/0010-store-location-under-a-syncing-filesystem.md) and
   implemented in Phase 1. Default `data_dir` is the platform data directory,
   `doctor` warns on a sync root, `busy_timeout` is 5s.
2. **The repository stays private for now**, revisited at the start of Phase 8.
   Recorded in [`BACKLOG.md`](../../BACKLOG.md) with the trigger and the exact
   commands. Consequence: `main` still has no server-side protection.

---

## Phase 2 progress log

Appended as each component lands on `feat/phase-2-llm-provider`, so an
interrupted session can resume from the last line rather than from the diff.

- **`EST-0003` logged** — 4.5h active, confidence 0.45, class `llm-integration`,
  no bias correction (n=1 in two disagreeing classes, below `BiasDetective`'s
  threshold) but a scope correction that prices the tests. Next: the request and
  response types in `praxis/llm/types.py`.
- **Request/response types landed** — `praxis/llm/{__init__,types,errors}.py`,
  48 tests. Two API facts checked against the docs rather than recalled and
  cited in the modules: sampling parameters are a 400 on the routed models, so
  the seam has no temperature; and structured output goes through
  `output_config.format`, not forced tool use. Next: the request hash in
  `praxis/llm/hashing.py`.
- **Replay key landed** — `praxis/llm/hashing.py`, 128-bit digest over sorted,
  unescaped canonical JSON, 18 tests including hypothesis properties. Next:
  token and cost accounting in `praxis/llm/accounting.py`.
- **Cache pricing landed** — `CACHE_READ_MULTIPLIER` (0.1×) and
  `CACHE_WRITE_MULTIPLIER` (1.25×) in `praxis/config/models.py`, cited to the
  prompt-caching docs. They are model-independent multiples of the base input
  rate, so they are constants rather than two more `ModelSpec` price columns
  that could drift apart at the next deprecation. Next: token and cost
  accounting in `praxis/llm/accounting.py`.
- **Accounting landed** — `praxis/llm/accounting.py`, 26 tests. `cost_of`
  prices cache reads and writes off the base rate; `CostLedger` is per-run and
  not global, and `check_affordable` refuses *before* the call using a bound
  that prices the full output cap plus a pessimistic 2 chars/token for input.
  `record_free` keeps the token columns populated on a mock run. Next: the
  `LLMProvider` interface in `praxis/llm/provider.py`.
- **Trace row landed** — `praxis/llm/trace.py`, 21 tests. `LLMTrace` is one row
  per *attempt*; `TraceSink` is a `Protocol`, which is what keeps `praxis.llm`
  from importing `praxis.store`, and `MemoryTraceSink` is the offline default.
  The boundary the handover asked for is held: traces and audit events stay two
  tables. Next: the SQLite sink, migration `003_traces.sql` plus
  `praxis/store/traces.py`.
- **Trace table landed** — migration `003_traces.sql` and
  `praxis/store/traces.py`, 29 tests. `llm_trace` has no `node` row and no
  `record_version`; append-only by trigger; `cost_usd` is exact decimal text
  summed in Python, never SQL `SUM` over a cast. The store imports
  `praxis.llm.trace` and not the other way round. `tests/test_cli.py` no longer
  hardcodes schema version 2. Next: the `LLMProvider` base class in
  `praxis/llm/provider.py` — routing, the ceiling check, timing and the trace
  write, with `_invoke` left to each implementation.
- **Provider seam landed** — `praxis/llm/provider.py`, 29 tests against a stub
  rather than an implementation, so the guarantees are proven for the live path
  too. `complete()` routes, checks the ceiling *before* `_invoke`, times, traces
  (failure path included) and raises on a refusal. `bills` is the class-level
  flag that exempts the offline providers from the ceiling. Next: `MockProvider`
  in `praxis/llm/mock.py` — plausible structured output per response shape, not
  a fixed string.
- **Synthesis landed** — `praxis/llm/synthesis.py`, 58 tests. A walk over the
  response schema, seeded from the replay key, filling quotation fields with
  sentences that really occur in the prompt and id fields with ids really
  supplied, so `VerifierAgent` has something to check offline. Nothing in it
  names an agent, which is ADR 0005's first assumption stated as code. A prompt
  carrying no ids gets a well-formed *wrong* span id on purpose — that is what
  a live model does in the same position. Next: `MockProvider` in
  `praxis/llm/mock.py`, which is now the seam plus a call to this.
- **Mock provider landed** — `praxis/llm/mock.py`, 31 tests. Deterministic per
  replay key rather than per call order, free but token-counted, honest about
  latency, and able to produce the two failures worth imitating —
  `malformed_share` and `refusal_share`, both keyed off the request so a
  failing call fails every time. `LLMRequest.source_text` was added and the
  mock quotes *that*: the system prompt is instruction, not evidence, and a
  citation lifted from the brief fails `VerifierAgent` for the wrong reason.
  Next: `ReplayProvider` in `praxis/llm/replay.py` — fixture files keyed by
  replay key, loud `ReplayCacheMissError`, never a fallback to the network.
