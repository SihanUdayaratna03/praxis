# Backlog

Work deliberately not done, with the reason. Nothing in this repository says
`TODO: implement later`; it says "see BACKLOG.md" or it does not exist.

Each item records **why not now**, because that is the part that decays. An
entry whose reason has stopped being true should be promoted or deleted.

## Deferred from Phase 0

| Item | Why not now |
| ---- | ----------- |
| Branch protection on `main` | **Attempted and refused**, not assumed: `PUT /repos/.../branches/main/protection` returns `403 — Upgrade to GitHub Pro or make this repository public to enable this feature`. The same rules are enforced by the pre-commit `no-commit-to-branch` hook and the `guard_git_workflow` Claude Code hook. The exact API call is recorded in `docs/reports/phase-0.md`; re-run it the moment the repo goes public or the plan changes. ADR 0007 assumption 3 tracks this. |
| Move the working copy off the OneDrive-synced path | The owner chose the location. File-sync tools have a poor record around SQLite WAL files, so this becomes a real risk in Phase 1 when the store exists. ADR 0003 assumption 5 is written to fire. The database is gitignored and rebuildable, so the blast radius is a rebuild. |
| Coverage reporting to a service (Codecov or similar) | Needs a token, which contradicts the no-credentials-in-CI stance. Coverage is enforced locally by a `fail_under` gate and uploaded as a CI artifact. Revisit only if a judge asks for a badge. |
| `praxis doctor --json` | No machine consumer yet. Add when the Phase 11 demo script needs to assert on it. |
| Dependency vulnerability scanning | Four runtime dependencies, all first-party-maintained. Add before Phase 12 when the dependency surface is final. |
| A `CONTRIBUTING.md` | Single contributor. `CLAUDE.md` carries the conventions. Write one if the project is opened up. |

## Deferred by design (revisit with data, not opinion)

| Item | Why not now |
| ---- | ----------- |
| Dynamic model routing by measured input difficulty | Would make cost non-deterministic and break the reproducibility the ablation table depends on (ADR 0004, ADR 0006). Revisit once Phase 10 reports real per-role token counts. |
| Caching identical LLM calls across eval runs | Premature before there is a measurement showing the eval harness is slow. Deterministic replay already covers the correctness case. |
| Incremental / streaming ingestion | Phase 3 handles a fixed local corpus. Streaming matters only if a real integration lands, which is out of scope for the competition. |
| Multi-user or multi-tenant storage | Single-writer is an explicit assumption in ADR 0003. Adding tenancy now would complicate every query for a requirement nobody has. |
| Anything beyond markdown, txt and json as a source | Email, chat exports, transcripts and issue trackers are named in the architecture as later adapters. The `SourceAdapter` interface exists so they are additive. |

## Known risks being carried

| Risk | Current stance |
| ---- | -------------- |
| The gap between mock-realistic and real model behaviour is unmeasured | ADR 0005 assumption 3. The first live run is set up to be a measurement rather than a surprise. |
| `DecisionScout` on the cheapest tier may not have adequate recall | ADR 0006 assumption 2. The fix is a one-line re-tier; the point is to find out from the eval harness rather than guess now. |
| Hand-written predicates may not parse once the Phase 5 DSL exists | ADR 0001 assumption 1. A small fixed set of files to migrate, and a finding the system is designed to surface. |
