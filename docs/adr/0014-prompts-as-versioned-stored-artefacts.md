---
id: ADR-0014
status: accepted
date: 2026-08-18
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0014 — Prompts are versioned files, and every trace names the one it read

## Chosen

A prompt is a file in `praxis/prompts/texts/`, named `<task>.v<n>.md`. The
filename carries the metadata: `name` is the `LLMRequest.task` it serves and
`<n>` is its version. `praxis.prompts.library` reads them through
`importlib.resources`, the same mechanism `praxis.store.migrations` already
uses, and hands an agent a `Prompt` carrying the text, its id (`group_blocks@v1`)
and the SHA-256 of its bytes.

**A version bump is a new file. An existing prompt file is never edited.**

Every request carries `prompt_id` and every trace row records `prompt_id` and
`prompt_sha`, so a number in a metrics table can be traced back to the exact
words that produced it.

Interpolation is `string.Template` (`$max_blocks`), not `str.format`.

## Rejected

| Option | Why not |
| ------ | ------- |
| Module-level string constants, as `SegmenterAgent` shipped in Phase 3 | Fine for one agent and wrong for six. A prompt changed in a commit is invisible in the metrics table it moved, and "recall 0.72" is not a fact unless the question that produced it can still be read. The constant also puts the largest untyped input in the system inside the file least likely to be reviewed for prose. |
| A prompt file with a YAML front-matter header carrying name and version | A header can disagree with the file it sits in; a filename cannot. The same reasoning migrations already use, and one less parser. |
| One file per prompt, edited in place, with git as the version history | Git knows which bytes were in the tree at a commit. It does not know which bytes a *trace row* read, and the trace row is what a report cites. Recovering that would mean joining a run's timestamp against the file's history, which is archaeology, not provenance. |
| A `prompt_version` integer in `praxis/config/settings.py` | Makes the version a property of the run rather than of the prompt, so two agents can never be on different versions — which is exactly what an A/B of one prompt needs. |
| `str.format` interpolation | Prompts carry JSON examples and predicate syntax, both full of braces. A prompt language that requires escaping braces is a prompt language that will eventually be escaped wrongly, and the failure is a 400 or a silently different question. |
| Put `prompt_id` in `LLMRequest.metadata` | `metadata` is excluded from `canonical()` and therefore never reaches the trace table. The column had to be real, which is why migration 004 exists. |

## What was known at the time

The trace store already records `request_json`, which contains the full system
prompt text — so in a narrow sense the prompt *was* already recoverable. Two
things it could not do: group rows by prompt version without parsing a JSON blob
per row, and distinguish two versions whose text differs by a character from two
runs of the same version. A column and a digest cost one migration and answer
both as a query.

Not known: whether prompt versions will ever be run concurrently for comparison.
The design permits it — `load(name, version=...)` exists and nothing forces one
version per process — but nothing does it yet, and the A/B harness is Phase 10's
business.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | A shipped prompt is never edited in place, because the digest table fails the suite when one is | `edited_prompts_reaching_main == 0` | `when(prompt_versions_shipped >= 12)` |
| 2 | Prompts stay small enough that a file each is legible, rather than needing composition or partials | `longest_prompt_lines <= 120` | `on_event("a prompt needs a shared preamble")` |
| 3 | Filename-as-metadata survives the number of prompts this project ends with | `prompts_shipped <= 40` | `when(phases_completed >= 10)` |
| 4 | Reading a prompt per call costs nothing measurable, because the directory walk is cached | `prompt_load_share_of_run_latency <= 0.01` | `when(corpus_documents > 200)` |

## Consequences

**Accepted costs.** Prompt text is no longer greppable from the agent that uses
it — a reader has to follow `load(TASK)` to a file. That is the same indirection
`praxis/config/models.py` imposes on model ids and it is accepted for the same
reason: the thing that must not drift is worth one hop.

The digest table in `tests/prompts/test_library.py` is a test that fails on a
legitimate-looking change, which is a category of test people learn to update
without reading. Its failure message says what to do instead, and the one
legitimate edit — a typo caught before the prompt has ever run — is cheap to
make deliberately.

Enforcement of "never edited in place" is a test rather than a runtime check,
and that is not a compromise: nothing in a running process can tell an edited
file from one that was always that way. The property is about the repository's
history, so the repository's test suite is where it belongs.

**Reversal cost.** Low. The library is one module and four files; inlining the
text back into agents would be mechanical. The migration is additive, so a
rollback leaves two unused columns rather than an unreadable store.
