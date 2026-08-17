# Backlog

Work deliberately not done, with the reason. Nothing in this repository says
`TODO: implement later`; it says "see BACKLOG.md" or it does not exist.

Each item records **why not now**, because that is the part that decays. An
entry whose reason has stopped being true should be promoted or deleted.

## Dated decision points

Not deferred work — questions with an owner, a trigger, and a deadline. Unlike
the entries below, these expire: reaching the trigger without a decision is
itself the failure.

### Repository visibility — decide at the start of Phase 8

| | |
| --- | --- |
| **Decision** | Stay private for now. Revisit going public around Phase 8, before competition submission. |
| **Decided by** | Sihan Udayaratna, 2026-08-09 |
| **Trigger** | Phase 8 begins (the fusion layer — the phase that makes the project worth showing) |
| **Latest safe date** | Before submission. Going public *after* submission wastes the benefits entirely. |

**What going public would buy.** Real server-side branch protection, free — it
is currently refused with `403 Upgrade to GitHub Pro`, so `main` is protected
only by client-side hooks. Judges could browse the network graph directly and
see each phase as its own strand, which is a deliberate artifact of ADR 0007.
Unlimited Actions minutes.

**What it costs.** The work becomes visible before submission, including the
incomplete phases and the parts where the reasoning is still wrong.

**Why Phase 8 and not sooner.** Phase 8 is where the product's central claim
first exists in code. Before that the repository shows scaffolding and half a
thesis; after it, the thing being judged is actually there. Waiting also means
the ADR corpus and commit history are substantial enough to be evidence rather
than a promise.

**When the trigger fires, do this:**

```bash
gh repo edit SihanUdayaratna03/praxis --visibility public --accept-visibility-change-consequences
gh api -X PUT repos/SihanUdayaratna03/praxis/branches/main/protection --input <ruleset>
```

The ruleset to apply is in the branch-protection row below. Confirm with the
owner before flipping visibility — it is outward-facing and effectively
irreversible in reputation terms even though the flag itself is not.

## Deferred from Phase 0

| Item | Why not now |
| ---- | ----------- |
| Branch protection on `main` | **Attempted and refused**, not assumed: `PUT /repos/.../branches/main/protection` returns `403 — Upgrade to GitHub Pro or make this repository public to enable this feature`. Blocked on the visibility decision above, so it unblocks at Phase 8. Until then the same rules are enforced client-side by the pre-commit `no-commit-to-branch` hook and `guard_git_workflow.py`. ADR 0007 assumption 3 tracks this. Ruleset to apply verbatim: `required_status_checks.strict = true` over the five CI contexts (`lint + types`, `build distribution`, `tests (ubuntu-latest, py3.12)`, `tests (ubuntu-latest, py3.13)`, `tests (windows-latest, py3.12)`), `allow_force_pushes = false`, `allow_deletions = false`. |
| Move the working copy off the OneDrive-synced path | The owner chose the location. File-sync tools have a poor record around SQLite WAL files, so this becomes a real risk in Phase 1 when the store exists. ADR 0003 assumption 5 is written to fire. The database is gitignored and rebuildable, so the blast radius is a rebuild. |
| Coverage reporting to a service (Codecov or similar) | Needs a token, which contradicts the no-credentials-in-CI stance. Coverage is enforced locally by a `fail_under` gate and uploaded as a CI artifact. Revisit only if a judge asks for a badge. |
| `praxis doctor --json` | No machine consumer yet. Add when the Phase 11 demo script needs to assert on it. |
| Dependency vulnerability scanning | Four runtime dependencies, all first-party-maintained. Add before Phase 12 when the dependency surface is final. |
| A `CONTRIBUTING.md` | Single contributor. `CLAUDE.md` carries the conventions. Write one if the project is opened up. |

## Deferred from Phase 2

| Item | Why not now |
| ---- | ----------- |
| ~~The structured-output layer: Pydantic model → reduced JSON schema, parse, and the repair loop~~ | **Built in Phase 3**, by the first agent that needed one, exactly as this entry said it would be: `praxis/llm/structured.py`, tested against `MockProvider(malformed_share=...)`. Kept here because the reason it waited turned out to be right — the reduction is shaped by what the API actually rejects, and that was read from the docs rather than guessed. |
| Streaming responses | The seam makes one non-streaming call, capped at `MAX_TOKENS_WITHOUT_STREAMING`. Still true after Phase 3: `SegmenterAgent` windows a document into 40 blocks precisely so no single answer approaches the cap. Streaming changes the trace row's shape (one row per attempt becomes one row per attempt plus a stream of deltas). Add it with the first agent that needs an answer over 16k tokens. |
| Prompt caching (`cache_control` on the request) | The pricing multipliers are in `praxis/config/models.py` and `cost_of` already prices cache reads and writes, so turning it on cannot silently make the cost column wrong. Phase 3 produced the first stable prefix — `SegmenterAgent`'s system prompt is identical on every call — but one agent's system prompt is far below the minimum cacheable length, so turning it on would cost a cache write and buy nothing. Revisit when several agents share a long preamble. |

## Deferred from Phase 3

| Item | Why not now |
| ---- | ----------- |
| A `Finding` for a rejected citation | The pipeline reports refused spans in its result and logs one warning each; it does not write a record. `FindingKind` has no member for a fabricated citation, and the SQL `CHECK` constraints mirror that enum, so adding one is a migration — for a record nothing reads until the reporting layer exists. Build it with the first consumer, in Phase 9 or Phase 11. The evidence is already in the right shape: `SpanRejection` carries the defect and the detail a `Finding.prosecution` would quote. |
| Cross-document edges in the corpus ground truth | Item ids are corpus-global and `ExpectedLink` can already name any of them, so nothing structural is missing. The templates only assert edges inside one document because the interesting cross-document case — an ADR's assumption against a status update's estimate — is exactly what `FusionBridge` has to *infer*, and planting it as ground truth before Phase 8 exists would fix the answer before anyone has asked the question. Add it with the Phase 10 harness, once the metric it feeds is written. |
| Streaming or incremental ingestion | Unchanged from Phase 0's entry below, and now measured rather than assumed: the whole 12-document corpus ingests in one call per document. Revisit if a real integration lands. |
| Segmentation quality measurement | ADR 0011 assumption 1 (`segmenter_f1 - paragraph_floor_f1 >= 0.05`) cannot be evaluated without a grader, and the grader is Phase 10. The floor is built and the pipeline already runs on it whenever a model answers badly, so the ablation is a configuration change rather than a rewrite when the harness exists. |

## Deferred by design (revisit with data, not opinion)

| Item | Why not now |
| ---- | ----------- |
| Dynamic model routing by measured input difficulty | Would make cost non-deterministic and break the reproducibility the ablation table depends on (ADR 0004, ADR 0006). Revisit once Phase 10 reports real per-role token counts. |
| Caching identical LLM calls across eval runs | Premature before there is a measurement showing the eval harness is slow. Deterministic replay already covers the correctness case. |
| Incremental / streaming ingestion | Phase 3 handles a fixed local corpus. Streaming matters only if a real integration lands, which is out of scope for the competition. |
| Multi-user or multi-tenant storage | Single-writer is an explicit assumption in ADR 0003. Adding tenancy now would complicate every query for a requirement nobody has. |
| Anything beyond markdown, txt and json as a source | Email, chat exports, transcripts and issue trackers are named in the architecture as later adapters. The `SourceAdapter` protocol exists and has three implementations, so a fourth is additive — and ADR 0011 assumption 2 is written to fire if a new source kind turns out to need its own block rules. |

## Known risks being carried

| Risk | Current stance |
| ---- | -------------- |
| The gap between mock-realistic and real model behaviour is unmeasured | ADR 0005 assumption 3. The first live run is set up to be a measurement rather than a surprise. |
| `DecisionScout` on the cheapest tier may not have adequate recall | ADR 0006 assumption 2. The fix is a one-line re-tier; the point is to find out from the eval harness rather than guess now. |
| Hand-written predicates may not parse once the Phase 5 DSL exists | ADR 0001 assumption 1. A small fixed set of files to migrate, and a finding the system is designed to surface. |
