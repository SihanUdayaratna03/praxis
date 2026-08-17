# Handover — start of Phase 4

Read this first, then `CLAUDE.md`. Written at the close of Phase 3 so the next
session can start working instead of re-deriving state.

---

## Where we stopped

Phase 3 is merged, tagged and green. Nothing is in flight.

| | |
| --- | --- |
| `main` | `64888bd`, local and remote identical |
| Tag | `v0.3-phase-3` → `64888bd` (dereferenced through the API, not assumed) |
| CI | 5/5 green on [#9](https://github.com/SihanUdayaratna03/praxis/pull/9) |
| Open PRs | none |
| Remote branches | `main` only |
| Working tree | clean |
| Suite | **1149 passed**, coverage **98.09%** (gate 85%) |
| Schema | version 3 — Phase 3 needed no migration |

```bash
cd "C:\Users\sihan\OneDrive\Desktop\Praxis Agents"
git checkout main && git pull
uv sync --all-groups
uv run praxis doctor            # expect OK, including one offline model call
uv run pytest                   # expect 1149 passed

# the phase 3 pipeline, end to end and offline
uv run praxis init
uv run praxis corpus generate .praxis-tmp/corpus
uv run praxis ingest .praxis-tmp/corpus/documents
uv run praxis store stats       # 12 documents, 112 spans
```

**Shipped in Phase 3:** `SourceAdapter` for markdown, text and JSON; the
deterministic block grid; `SegmenterAgent`; `VerifierAgent`; the
structured-output layer and its repair loop (deferred from Phase 2); the
synthetic corpus generator and its machine-gradeable ground truth; the
end-to-end ingestion pipeline with tracing; `praxis ingest` and `praxis corpus
generate`. 269 new tests. ADRs 0011 and 0012.

**Deliberately not shipped:** a `Finding` for a rejected citation,
cross-document ground-truth edges, streaming, prompt caching — all in
[`BACKLOG.md`](../../BACKLOG.md) with reasons.

The full account is in [`phase-3.md`](phase-3.md). Read its last section before
writing `EST-0005`.

---

## Phase 3's own numbers

| | |
| --- | --- |
| `EST-0004` | 5.5h active, 20h blocked, confidence 0.45, class `agent-implementation` |
| `OUT-0004` | ~4.2h active, ~12.8h blocked, scored **`close`** |
| Miss direction | **over-estimated, ~1.3×** |

Four outcomes now exist in four work classes and they still disagree in
direction — over 2.3×, under 2.1×, under 1.24×, over 1.3×, `n = 1` each.
**Do not correct `EST-0005` for any of them.** `BiasDetective` refuses below
`n = 5` and so should its author.

Two things to carry into the estimate itself:

- **Price components, not phases, and count the components honestly.** The one
  line item that went badly wrong in `EST-0004` was the one described in four
  words. Tests came in at 2,826 against ~2,600 predicted; production came in at
  3,740 against ~1,900, and the whole gap is the corpus generator, priced as a
  single item and delivered as five modules.
- **Commit before a session is likely to end.** Phase 3's active figure is the
  softest number in the corpus because a session limit landed between two
  commits, leaving the first window bounded on one side only. A `wip:` commit
  on a strand branch never reaches `main`'s history and is the difference
  between a bounded window and a guess.

---

## What Phase 4 is

Orchestration, from [`ARCHITECTURE.md`](../../ARCHITECTURE.md) and
[ADR 0004](../adr/0004-custom-async-orchestrator.md): a small async state
machine over a typed message bus, written for this project. No agent framework.

The requirement that drives the design is already written down: **two runs over
one corpus with one seed must produce identical numbers**, or the Phase 10
ablation table means nothing.

That property is currently true of everything Phase 3 built, and each piece has
a test saying so — the segmenter's spans, the corpus's bytes, the answer key,
and the mock's answers are all functions of their inputs. Phase 4 is where it
is easy to lose: concurrency, `asyncio` scheduling order, and anything that
reads a clock or a set iteration order.

Do not design it here. The first action of the phase is the estimate, before
any file is created:

1. `git checkout -b feat/phase-4-<slug>` off `main`.
2. Append `EST-0005` to `docs/dogfood/estimates.jsonl` **before** the work
   starts, with `active_quantity` and `blocked_quantity`.
3. Then work the phase per `CLAUDE.md` § Workflow per phase.

Three things Phase 3 left standing for it:

- **`SegmenterAgent` is the only agent so far, and it is the shape the rest
  follow.** It takes a provider it did not choose, asks
  `praxis.llm.structured.ask_for` for a Pydantic model, degrades rather than
  raising on a bad answer about one document, and lets a failure about the
  *run* propagate. Phase 4's orchestrator should be able to run it without
  knowing any of that.
- **`IngestionPipeline` is a hand-rolled sequence, and Phase 4 is what replaces
  its wiring.** Its stages are the right ones; what it lacks is a run id
  threaded through every write (`Repository.add` already takes `run_id` and the
  pipeline does not pass one), and any notion of concurrency.
- **`praxis/corpus/` is the fixture corpus for every later phase.** Regenerate
  it rather than editing it, and never hand-edit `ground_truth.json` — the
  offsets are written by construction and `verify_corpus` will catch a drift,
  but only if someone runs it.

---

## Things a cold session will otherwise rediscover the hard way

Carried forward, because every one of them cost something.

**From Phase 3:**

- **A test built from two identical-looking literals tests nothing.** The NFC
  test compared two spellings of `café` that were the same string in the source
  file. Build both from `chr(...)` and assert they differ *before* asserting
  normalisation collapses them.
- **`stats.records` omits kinds with no rows.** It is a `GROUP BY`, so
  `records[RecordKind.SPAN]` raises `KeyError` on an empty store. Use `.get(kind, 0)`.
- **A `conftest` fixture inside a `@given` test needs
  `suppress_health_check=[HealthCheck.function_scoped_fixture]`.**
- **Ruff's auto-fix strips an import added before its use lands** — true of the
  `post_edit_verify` hook *and* of a manual `ruff check` between two edits. Add
  the import and its use in the same edit.
- **The structured-output dialect rejects unsupported keywords with a 400**, it
  does not ignore them. `praxis/llm/structured.py` strips them and says them in
  the description instead, which is what the official SDKs do; the Pydantic
  model stays the only validator.
- **Generated prose has to be read by a person.** Nothing automated catches
  "This rests on the index stays under 50 GB".

**From Phase 2:**

- **A single-seed test of a random generator tests one number.**
- **Build test doubles out of the real object where one exists.** The
  segmenter's mechanism tests use a real `LLMProvider` subclass, so they still
  go through routing, the ledger and the trace sink.
- **`assert_never` and mypy's `warn_unreachable` disagree** on an exhaustive
  `if` chain. Put the check in a test.
- **`guard_no_secrets.py` reads a long value after `api_key=` as a credential.**
  Bind it to a local first.
- **`mypy --strict` cannot check a `**kwargs` dictionary through a call.**
- **The trace store and the audit trail are two tables and must stay two.**

**From Phase 1 and 0:**

- **Long text goes to a file.** ~965-byte shell parse limit, and PowerShell 5.1
  mangles embedded quotes passed to native executables. `gh pr create
  --body-file`, `git commit -F`. `.praxis-tmp/` is gitignored.
- **Branch protection does not exist** — `403 Upgrade to GitHub Pro`. `main` is
  protected only by client-side hooks. Branch and open a PR even for a one-line
  docs change.
- **A merge commit message must be a conventional commit.** The `commit-msg`
  hook rejects `merge: ...`; the strand merges use `chore: merge the ... strand`.
- **A check about other people's machines must not be tested only on this one.**
- **`executescript` commits any open transaction before it runs.**
- **An FTS5 table cannot be aliased on the left of `MATCH`.**
- **`strategy.example()` inside a test raises** under `filterwarnings = ["error"]`.
- **Editing a file with a Python script on this machine writes CRLF**, and the
  pre-commit `mixed line ending` hook rewrites it and aborts. `git add` again
  and re-run the commit.
- **Nothing in `praxis/store/` or `praxis/llm/` is expected to need further
  work.** Phase 3 added one read to `store/reports.py` and one generator hint to
  `llm/synthesis.py`, both additive. Fix a bug if a test finds one; do not
  redesign either.

---

## Owner decisions — still standing, do not re-ask

1. **Store location.** Settled in
   [ADR 0010](../adr/0010-store-location-under-a-syncing-filesystem.md).
2. **The repository stays private for now**, revisited at the start of Phase 8.
   Recorded in [`BACKLOG.md`](../../BACKLOG.md) with the trigger and the exact
   commands. Consequence: `main` still has no server-side protection.
3. **No API key exists and none is expected.** `PRAXIS_LLM_PROVIDER=mock` is
   the default, CI has no secret, and `praxis doctor` checks the claim by making
   a call rather than reading a setting.

---

## Phase 4 progress log

Appended as each component lands, so an interrupted session can resume from the
last line rather than from the diff.

**Correction to "What Phase 4 is" above.** That section was written at the close
of Phase 3 and says Phase 4 is orchestration. The owner re-scoped it: Phase 4 is
**Half A core agents** — `DecisionScout`, `DecisionStructurer`,
`AssumptionExtractor`, the pipeline wiring them onto Phase 3's ingestion, and
the first evaluation harness. Orchestration moves later.
[ADR 0004](../adr/0004-custom-async-orchestrator.md) still stands; nothing about
it changed except when it is built.

- `EST-0005` logged on the phase branch before any Phase 4 file existed: **6.0h
  active, 16.0h blocked**, confidence 0.40, class `agent-implementation`. No
  bias correction, for the fifth time — four outcomes, four classes, `n = 1`
  each, still disagreeing in direction. The eval harness is priced as four line
  items rather than one, which is the lesson `OUT-0004` paid for.
- `praxis/prompts/` — the prompt library. Files named `<task>.v<n>.md`, read
  through `importlib.resources`; a version bump is a new file and
  `tests/prompts/test_library.py` pins every digest to enforce it. The
  segmenter's Phase 3 prompt moved in **byte-identical**, so no prompt hash and
  no mock answer changed.
- Migration **004** — `prompt_id` and `prompt_sha` on `llm_trace`, nullable.
  Schema is now at version 4. `LLMRequest.prompt_id` is excluded from
  `canonical()`; the system text it names is already in the hash. ADR 0014.
- `praxis/agents/offering.py` — the numbered span listing every extraction cites
  through, plus `praxis/agents/errors.py`'s refusal vocabulary. ADR 0015. An
  ordinal resolves or is refused; a *wrong* citation still survives and is named
  `IN_ANOTHER_OFFERED_SPAN` separately from `NOWHERE`.
- `DecisionScout` on `feat/phase-4-scout`, merged `--no-ff`. Scan tier,
  ordinals only, no quotation and therefore no mis-attribution possible. No
  floor to degrade to — a blind window is recorded as blind, because "every
  passage holds a decision" would pay the structurer for the whole corpus.
- `DecisionStructurer` on `feat/phase-4-structurer` — **agent committed, tests
  not written yet.** Green under ruff and `mypy --strict`; `tests/agents/
  test_structurer.py` does not exist. Committed mid-component deliberately,
  per `OUT-0004`'s note about a session limit landing between two commits.

### Resume here

Branch `feat/phase-4-structurer` (not merged). Working tree clean, everything
pushed. Next, in order:

1. **`tests/agents/test_structurer.py`**, then merge the strand `--no-ff`.
   Model on `tests/agents/test_scout.py` — the `Answering` provider there is a
   real `LLMProvider` subclass, so routing, the ledger and the trace sink stay
   in the path. Cover: the four `Refusal` members this agent can return
   (`UNOFFERED_SPAN`, `MIS_ATTRIBUTED_QUOTE`, `FABRICATED_QUOTE`,
   `INCOHERENT_RECORD`), `EMPTY_ANSWER` when `found` is false, the
   `justified_by` edge carrying the rationale, and `date_was_stated` in both
   directions.
2. **`feat/phase-4-extractor`** — `AssumptionExtractor` on `REASON`, prompt
   `extract_assumptions.v1.md`. It must write an `Assumption` **and** an
   `Estimate` joined by `estimated_as` whenever a predicate is quantified and
   forward-looking. `work_class` falls back to `unclassified` when the model
   gives none — `WorkClassifier` owns that field and arrives in Half B.
3. **`praxis/agents/extraction.py`** (+ ADR 0013 for the routing table) and
   then `praxis/eval/` as four components — `matching`, `metrics`, `harness`,
   `report` — which `EST-0005` priced as four line items on purpose.

Still to do after that: `praxis extract` / `praxis eval` CLI commands,
`docs/reports/phase-4.md` with the metrics table, `ARCHITECTURE.md` updated
for Half A and schema version 4, the PR, tag `v0.4-phase-4`, and `OUT-0005`.
