# Handover — start of Phase 3

Read this first, then `CLAUDE.md`. Written at the close of Phase 2 so the next
session can start working instead of re-deriving state.

---

## Where we stopped

Phase 2 is merged, tagged and green. Nothing is in flight.

| | |
| --- | --- |
| `main` | `7cdddec`, local and remote identical |
| Tag | `v0.2-phase-2` → `7cdddec` (dereferenced through the API, not assumed) |
| CI | 5/5 green on the PR |
| Open PRs | none |
| Remote branches | `main` only |
| Working tree | clean |
| Suite | **880 passed**, coverage **98.16%** (gate 85%) |

```bash
cd "C:\Users\sihan\OneDrive\Desktop\Praxis Agents"
git checkout main && git pull
uv sync --all-groups
uv run praxis doctor          # expect OK, including one offline model call
uv run pytest                 # expect 880 passed
```

**Shipped in Phase 2:** the `LLMProvider` seam, `MockProvider` built on a
schema-driven synthesiser, `ReplayProvider`, `AnthropicProvider` and its
recording subclass, the replay key, token and cost accounting with a ceiling
checked before the call, the `llm_trace` table (schema version 3), the provider
factory, the boundary test, and `doctor` proving the offline claim by making a
call. 427 new tests.

**Deliberately not shipped:** the structured-output layer and its repair loop,
streaming, and prompt caching — all three in [`BACKLOG.md`](../../BACKLOG.md)
with reasons. No agent, no prompt, no orchestration.

The full account is in [`phase-2.md`](phase-2.md). Read its last section before
writing `EST-0004`.

---

## Phase 2's own numbers

| | |
| --- | --- |
| `EST-0003` | 4.5 hours active, confidence 0.45, class `llm-integration` |
| `OUT-0003` | ~5.6 hours active, ~30 hours wall clock, scored **`close`** |
| Miss direction | **under-estimated, ~1.24×** |

The scope correction OUT-0002 asked for worked: the estimate priced ~2,200
production and ~3,000 test lines as line items, and the actual is 2,246 and
2,566. The test figure is short by about the one component that was deferred.

Three outcomes now exist in three work classes and they disagree in direction —
over 2.3×, under 2.1×, under 1.24×, `n = 1` each. **Do not correct `EST-0004`
for any of them.** `BiasDetective` refuses below `n = 5` and so should its
author; that refusal is the product's own thesis, and the first place to break
it would be here.

---

## What Phase 3 is

Ingestion, from [`ARCHITECTURE.md`](../../ARCHITECTURE.md): `SourceAdapter` →
`SegmenterAgent`, turning markdown, text and JSON into `Document` and `Span`
records with stable ids and exact source offsets. This is the first phase with
an agent in it, so it is also where the model layer stops being theoretical.

Do not design it here. The first action of the phase is the estimate, before
any file is created:

1. `git checkout -b feat/phase-3-<slug>` off `main`.
2. Append `EST-0004` to `docs/dogfood/estimates.jsonl` **before** the work
   starts. A phase with no prediction is a hole in the Phase 12 demo.
3. Then work the phase per `CLAUDE.md` § Workflow per phase.

Two things Phase 2 left standing for it:

- **The structured-output layer belongs to the first agent that needs it.**
  `MalformedOutputError` is in the vocabulary with nothing raising it, and
  `MockProvider(malformed_share=...)` exists to give a repair loop something to
  repair. Build it when there is a real failure distribution to build against.
- **Every agent asks `provider_for()` for a provider and `praxis.config.models`
  for a role.** An agent that names a provider or a model breaks ADR 0005's
  first assumption and invariant 2 respectively, and `praxis doctor` plus
  `tests/test_boundaries.py` are what notice.

---

## Things a cold session will otherwise rediscover the hard way

Carried forward, because every one of them cost something.

**From Phase 2:**

- **A single-seed test of a random generator tests one number.** The
  synthesiser rounded values back outside their own exclusive bounds, and only
  a thirty-seed loop caught it.
- **Build test doubles out of the real object where one exists.** The live
  provider's tests construct real `anthropic.types.Message` values; that is how
  the SDK reporting cache token counts as `None` rather than zero was found
  before a live run could find it. A stub agrees with whatever you assumed.
- **`assert_never` and mypy's `warn_unreachable` disagree** on an exhaustive
  `if` chain — the exhaustiveness proof makes the `assert_never` unreachable
  code. Put the check in a test instead of silencing a gate.
- **`guard_no_secrets.py` reads a long value after `api_key=` as a credential**
  and is right to. Bind it to a local first.
- **`mypy --strict` cannot check a `**kwargs` dictionary through a call.** Pass
  the arguments explicitly.
- **The trace store and the audit trail are two tables and must stay two.** One
  records what an agent was *told*, the other what it *changed*.

**From Phase 1 and 0:**

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
  Use `PureWindowsPath` / `PurePosixPath` for path literals in tests.
- **`executescript` commits any open transaction before it runs**, so migration
  transaction control lives *inside* the script.
- **An FTS5 table cannot be aliased on the left of `MATCH`.**
- **`strategy.example()` inside a test raises** under
  `filterwarnings = ["error"]`. Compose the strategy into the `@given`.
- **Editing a file with a Python script on this machine writes CRLF**, and the
  pre-commit `mixed line ending` hook rewrites it and aborts the commit. Just
  `git add` again and re-run the commit; the second one succeeds.
- **Nothing in `praxis/store/` or `praxis/llm/` is expected to need further
  work.** If a later test finds a bug, fix it there — but do not redesign
  either.

---

## Owner decisions — still standing, do not re-ask

1. **Store location.** Settled in
   [ADR 0010](../adr/0010-store-location-under-a-syncing-filesystem.md).
2. **The repository stays private for now**, revisited at the start of Phase 8.
   Recorded in [`BACKLOG.md`](../../BACKLOG.md) with the trigger and the exact
   commands. Consequence: `main` still has no server-side protection.
3. **No API key exists and none is expected.** `PRAXIS_LLM_PROVIDER=mock` is
   the default, CI has no secret, and Phase 2 made that checkable rather than
   asserted. Do not write anything that needs a credential to run.

---

## Phase 3 progress log

Appended as each component lands, so an interrupted session can resume from the
last line rather than from the diff.

- **`EST-0004` logged** on `feat/phase-3-ingestion` before any `praxis/ingest`
  file existed: **5.5h active, 20h blocked**, confidence 0.45, class
  `agent-implementation`. No bias correction, for the fourth time and the same
  reason — three work classes, `n = 1` each, directions disagreeing, and
  `BiasDetective` refuses below `n = 5`. First estimate to carry
  `active_quantity` and `blocked_quantity` as separate fields, matching the
  `Estimate` record's own shape. **Next:** `SourceAdapter` on
  `feat/phase-3-adapter`.
- **Adapter and block grid landed** on `feat/phase-3-adapter`:
  `praxis/ingest/{errors,adapters,blocks}.py`. Normalisation is exactly three
  transformations (BOM, line endings, NFC) and the tests pin what it must *not*
  do as hard as what it does. JSON is re-rendered one `path: value` block per
  leaf so its prose is quotable. The block grid is property-tested to tile a
  document exactly — every non-whitespace character in exactly one block — and
  is the segmenter's degradation floor. **Next:** ADR 0011, then the
  structured-output layer and `SegmenterAgent` on `feat/phase-3-segmenter`.
- **ADR 0011, the structured-output layer and `SegmenterAgent` landed** on
  `feat/phase-3-segmenter`. The layer deferred from Phase 2 is
  `praxis/llm/structured.py`: reduce, describe the stripped bound, close every
  object, validate locally — the same four steps the official SDKs document,
  cited. The agent answers in block numbers only. `synthesis.py` gained
  `ordinals_in` so the mock answers with labels it was actually shown, without
  which the code that honours a good grouping would never run offline.
  **Next:** `VerifierAgent` on `feat/phase-3-verifier`.
- **`VerifierAgent` landed** on `feat/phase-3-verifier`: span resolution and
  the invariant-6 claim gate, both deterministic. Documents are *resolved*
  through a `DocumentSource`, so a span citing a document nothing ingested is
  refused as `UNKNOWN_DOCUMENT` (new member of `SpanDefect`). Whitespace is the
  only latitude; case and punctuation are not normalised. `test_boundaries.py`
  now maps each `NON_LLM_AGENTS` name onto its module and asserts the module
  cannot import `praxis.llm`. **Next:** ADR 0012 and the corpus generator on
  `feat/phase-3-corpus`.
- **ADR 0012 and the ground-truth format landed** on `feat/phase-3-corpus`:
  `praxis/corpus/{groundtruth,topics}.py`. Offsets are into the *normalised
  content*, not the file on disk, so the answer key and a `Span` share one
  coordinate system and `content_sha256` is exactly `Document.content_hash`.
  `verify_corpus` re-reads every offset and returns all problems rather than
  the first. The JSON rendering helpers in `adapters.py` are public so the
  generator can place an offset without searching for it. **Next:** the
  generator itself, then the end-to-end pipeline.
- **The corpus generator landed** on `feat/phase-3-corpus`:
  `praxis/corpus/{drafting,templates,generator}.py`. 12 documents across four
  shapes (ADR, meeting notes, status update, JSON export) and eight topics,
  with 30 real items and 12 distractors. `generate_corpus` verifies what it
  wrote and refuses to return a corpus with a problem in it. Every ADR carries
  an effort assumption with an `estimated_as` edge, so the fusion relationship
  is labelled in the corpus before the agent that finds it exists. Tests
  ingest the generated documents and build a real `Span` over every
  ground-truth range. **Next:** the end-to-end pipeline on
  `feat/phase-3-pipeline`.
- **The pipeline and the CLI landed** on `feat/phase-3-pipeline`:
  `praxis/ingest/pipeline.py`, `praxis ingest`, `praxis corpus generate`, and a
  re-ingestion lookup by content hash added to `praxis/store/reports.py`. Suite
  is **1149 passed, 98.09% coverage**. `ARCHITECTURE.md` and `BACKLOG.md`
  updated. **Next:** the phase report, `OUT-0004`, and the PR.
