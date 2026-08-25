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

**From Phase 4:**

- **Rich wraps a `console.print` at the terminal width, and CI's is narrower
  than yours.** An error message carrying a path folded `praxis corpus
  generate` in half on CI's 80 columns and passed locally. Put the remedy on
  its own short line, and assert on that rather than on a fragment. A table
  printed for a person to copy needs `soft_wrap=True, markup=False`.
- **Latent, not fixed:** `test_corpus_generate_reports_a_bad_request_as_a_sentence`
  fails at `COLUMNS=60` for the same reason. CI runs at 80 and it has always
  passed there; it was left alone because Phase 3's output is not Phase 4's to
  change. Worth a line when something else touches that command.

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
- `DecisionStructurer` on `feat/phase-4-structurer`, merged `--no-ff`. Extract
  tier, one call per candidate. The rationale is on the `justified_by` edge and
  not on the record, a decision with no alternatives is recorded as one rather
  than invented into two, and a missing date falls back to `ingested_at` with
  `date_was_stated` carrying the difference. 51 tests, `praxis/agents/
  structurer.py` at 100%. `Answering` and `Refusing` moved into
  `tests/agents/conftest.py` so both agents' tests share one copy.
- **A moved document is not a fabricating model.** `verify_claim` was asked
  first, so a span that no longer resolved came back `ABSENT` and was reported
  as `FABRICATED_QUOTE`. `SPAN_DOES_NOT_RESOLVE` existed and nothing returned
  it. Span resolution is now asked about before the quotation is judged.
- **Offline, almost every extraction is refused, and that is the mock working.**
  `praxis.llm.synthesis` draws the cited ordinal from the bracketed labels and
  the quotation from the passages *independently*, so the two agree only by
  chance. The eval harness will measure the plumbing rather than the model, and
  `docs/reports/phase-4.md` has to say so beside the table.

- `praxis/agents/citation.py` — the one gate every extracted claim's citation
  passes through, lifted out of the structurer before the extractor could grow
  a second copy. The order of its checks is the substance: an unoffered ordinal
  beats everything downstream, and a span that will not resolve beats a bad
  quotation.
- `praxis/agents/offering.py` gained `spread(spans, index, behind=, ahead=)`,
  and `around` now delegates to it, so one place knows how a window is cut.
- `AssumptionExtractor` on `feat/phase-4-extractor`, merged `--no-ff`. Reason
  tier, one call per decision, writing an `Assumption`, its `justified_by` edge,
  the decision's `assumes` edge, and — when the claim is quantified and
  forward-looking — an `Estimate` with **its own citation** plus the
  `estimated_as` edge. 56 tests, `extractor.py` at 100%.
- **ADR 0016**: the extractor writes the first `estimated_as` edge, which
  `ARCHITECTURE.md` assigns to `FusionBridge`. The corpus labels those edges by
  construction and nothing produced one to grade against; the cheap half of the
  recognition is free in a call already holding the assumption and its quantity,
  and the cross-document half is still `FusionBridge`'s.
- **The extractor's window is 3 behind and 8 ahead**, which is 12 passages —
  exactly ADR 0015's `spans_per_offering <= 12`. Assumptions are written *after*
  a decision in both corpus shapes, so reaching equally would spend half the
  listing on the title block and still stop short of the effort section.
  Widening either part breaches a recorded assumption rather than editing a
  constant.

- `praxis/agents/extraction.py` and `praxis/agents/results.py` on
  `feat/phase-4-pipeline`, merged `--no-ff`. `ExtractionPipeline` runs scout →
  structurer → extractor over a store and writes in dependency order. 23 tests
  reading every assertion back out of SQLite; both modules at 100%. **No ADR
  0013** — the routing table it was reserved for is unchanged from ADR 0006 and
  the pipeline decided nothing that file did not already say. The number stays
  free rather than being spent on a restatement.
- Three things the pipeline had to settle, each in its docstring: ids come from
  a counter seeded off the store (`next_id` reads the highest ordinal, and one
  extractor call builds three assumptions before any is written); a document
  that already holds decisions is recognised rather than redone (sequential ids
  would fork it, content-addressed `Link` ids would collide); and the extractor
  runs once per *verified* decision, so a refused one is never paid for twice.
- `run_id` is threaded through every write, which is the gap Phase 3's handover
  flagged on `Repository.add`. Nothing generates one yet — the caller passes it
  or it stays null, and ADR 0004's orchestrator is what will mint them.

- `praxis/eval/` landed as the four priced components — `matching`, `metrics`,
  `harness`, `report` — with `tests/eval/test_matching.py` and
  `test_metrics.py` covering the deterministic half. 50 tests.

- `tests/eval/test_harness.py` — the corpus really generated, ingested,
  extracted and graded. Two runs render identical JSON; a record the run never
  reported is still graded, because grading reads SQLite. 15 tests.

- `tests/eval/test_report.py` — the renderer held to the numbers it was given:
  table and JSON never disagree about a rate, and one result rendered behind
  two different pairings is byte-identical. 26 tests. `praxis/eval/` is done.

- `praxis extract` and `praxis eval` in `praxis/cli_eval.py`, registered from
  `cli.py`. `extract` exits zero although the gate refused, because offline
  that is almost every claim; `eval` grades into a scratch store rather than
  the owner's, and prints to stdout exactly what `--markdown` writes.
- **A provenance line has to be ASCII.** Redirecting `praxis eval` into a file
  on Windows gets the console codepage, and the middot separator arrived as a
  replacement byte in the one line naming the provider, seed and prompt
  versions.

- `docs/reports/phase-4.md`, `ARCHITECTURE.md` (Half A, evaluation, schema
  version 4, orchestration moved to Phase 5+) and the README quickstart, which
  now runs the whole pipeline end to end with no credentials.
- **One commit was rewritten.** `c94a2f7` carried a copy of the previous
  commit's subject while its diff was the whole of `praxis/eval/`. Reworded on
  the unmerged branch and pushed with `--force-with-lease`, because this
  history is meant to be a valid corpus and a subject that contradicts its own
  diff is a bad row in it.

### Resume here

Phase 4 is complete: merged, tagged `v0.4-phase-4`, `OUT-0005` closed. Phase 5
starts by reading `docs/reports/phase-4.md`'s last section, then logging
`EST-0006` on a fresh branch **before** any Phase 5 file exists.

---

## Phase 5 progress log

Appended as each component lands, so an interrupted session can resume from the
last line rather than from the diff.

**Phase 5 is Assumption Formalization and Monitoring**, and it closes Half A.
Phase 4 extracted assumptions as natural language tied to a `Span`; this phase
makes them checkable. `AssumptionFormalizer`, `AssumptionMonitor`,
`ContradictionDetector` and `ArchaeologistAgent`, plus the predicate DSL that
[`BACKLOG.md`](../../BACKLOG.md) and ADR 0001 assumption 1 have both been
waiting for.

- `EST-0006` logged on the phase branch before any Phase 5 file existed: **6.5h
  active, 24.0h blocked**, confidence 0.40, class `agent-implementation`. The
  ratios were **recomputed from the log rather than recalled**, and they do not
  say what the previous four calibration notes said: five outcomes across
  **four** classes, not five, with `agent-implementation` at `n = 2` and both
  points over-estimates. No correction applied for the sixth time -- and the
  note refuses the disguised version too, since rescaling by the measured
  throughput of the same work class is a bias correction wearing a hat.
- `praxis/predicates/` — the DSL, on `feat/phase-5-predicates`. Lexer, parser
  and renderer first, at 100% coverage. **A truth value is three-valued**:
  `TRUE`, `FALSE`, `UNKNOWN(reason)`. An assumption mentioning a quantity
  nobody measured has not been violated, it has not been *checked*, and a
  two-valued evaluator has to call that false -- which is what raises a breach.
  The whole distinction this phase is graded on lives in that gap, so it is in
  the type rather than in a convention.
- The grammar is shaped by the predicates already in this repository, not by
  taste: digit separators because two ADRs write `1_000_000`, division because
  ADR 0001's own assumption is a ratio, and `between a and b` because ADR 0007
  writes commits-per-phase that way. It desugars rather than surviving as a
  node the interval arithmetic would have to learn twice.
- **A term at the top is refused.** `index_size_gb` is a quantity, not a claim,
  and accepting it would let a predicate truncated by a bad model answer parse
  as though it said something.
- **The one ADR predicate that does not parse is left not parsing.** ADR 0015
  assumption 3 says a ratio should be `outside [0.5, 2.0]`, which is English.
  ADR 0001 assumption 1 exists to surface exactly that; widening the grammar
  for one instance would be answering the question by editing it.
- `praxis/predicates/evaluator.py` and `world.py`. **Kleene, not Python**:
  `false and unknown` is `false`, because no measurement could rescue it. The
  arithmetic context is *owned* rather than inherited -- `decimal`'s context is
  per-thread and anyone can change it, and the test pins a threshold sitting
  between two-thirds at three digits and at twenty-eight.
- **The monotonicity property is the one the monitor rests on**, and it is
  generated rather than sampled: adding facts may decide an undecided predicate
  and may never change a decided one. If that breaks, supplying a fact can
  raise a breach against a decision that was holding.
- A `WorldState` is a value, not a service. Facts, events and a clock, answered
  from what it was built with. Events match after case-and-whitespace
  normalisation and nothing looser -- anything looser is a judgement, and it is
  made in `AssumptionMonitor` on a path that can only *age* an assumption.
- `praxis/predicates/expiry.py` and `intervals.py`. Three expiry forms and no
  more -- an observation, not a design: every condition in `docs/adr/` and
  `corpus/topics.py` is a `when`, an `after` or an `on_event`. Firing is
  three-valued too, so an undecided condition expires nothing.
- **The interval arithmetic settles contradictions a model has no privileged
  access to.** `index_size_gb <= 50` against `> 50` permits disjoint sets of
  numbers; that costs nothing, is reproducible, and leaves the `reason` tier
  for pairs that are genuinely a judgement.
- `constraints_of` returns **nothing at all** for a disjunction rather than
  reading the readable half. A weakened claim is what produces a *false*
  contradiction, and that is the expensive direction to be wrong in.
- `praxis/predicates/` is done: **167 tests, 99% coverage**, no model anywhere
  in it. The whole package is the deterministic half invariant 3 names.
- `AssumptionFormalizer` on `feat/phase-5-formalizer`, 100% covered. It stores
  the **rendered** predicate rather than the model's spelling, so `x<=50` and
  `x <= 50` become one string and land in one blocking bucket later. One retry,
  shown the parse error; two would be a repair loop, which
  `praxis.llm.structured` already owns for the *schema*.
- **Nothing is dropped.** An assumption that will not compile is written with
  its confidence capped at 0.3 and the audit reason saying why. Safe because
  the monitor cannot breach on a predicate that does not parse, and better than
  silence because a person can fix what they can see.
- `is_checkable` is a **parse, not a stored flag** -- a flag can disagree with
  the text beside it. That is also the re-runnability: an assumption that
  already parses is skipped with no model call.
- **The offline provider now answers a `predicate` field from the passage.**
  `praxis/llm/synthesis.py` gained `predicates_in` and `expiries_in`, matched on
  shape because `praxis.llm` may not import `praxis.predicates`. Exactly the
  `ordinals_in` argument from Phase 4: without it the branch that *stores* a
  formalized predicate never runs offline and only the refusal path is covered.
  The numbers are still about the plumbing, and the report has to say so.
- `tests/test_boundaries.py` gained a row: `praxis/predicates/` never imports
  `praxis.llm`. The package is not an agent, so `NON_LLM_AGENTS` cannot describe
  it, and one convenient import is how invariant 3 would actually be lost here.
- `praxis/monitor/` on `feat/phase-5-monitor`, at **99%**. **No model output can
  breach an assumption**, and that is structural rather than a convention: the
  model is asked one question -- does a recorded observation report the event an
  `on_event(...)` names -- and its answer can only add an observation, which can
  only fire an expiry, which can only produce `EXPIRED`. A wrong answer ages an
  assumption early; it cannot accuse a decision of resting on something false.
- The four statuses fall out of three-valued evaluation and nothing else: false
  is a breach, true is holding, unknown-with-a-fired-expiry is expired, and
  unknown-without-one is **unchanged and reported rather than written**.
- That last row is the whole of re-runnability. Evaluation is free, so the
  monitor always evaluates and **writes only when the verdict changes** — the
  store already holds the last one, so this needs no column and no marker. The
  test asserts it against version counts and audit rows, not against a flag.
- An `AssumptionBreach` is a `Finding` with the kind Phase 1 already defined.
  **One per breached assumption**, never one per decision resting on it: the
  decisions are reverse-reachable over `assumes`, and N findings for one fact
  would make a count of breaches a count of citations. Severity is arithmetic.
- `AssumptionMonitor` takes an **optional** provider. Without one every
  predicate is still evaluated and every breach still raised.
- **The thesis runs in eight lines**: an outcome that missed its estimate
  reaches back through `estimated_as` and breaches the assumption a decision
  rests on. Tested against a hand-built store — it binds nothing in a corpus run
  until Phase 6 writes an `Outcome`, and the report says so beside that zero.
