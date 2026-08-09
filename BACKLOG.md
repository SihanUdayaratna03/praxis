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
