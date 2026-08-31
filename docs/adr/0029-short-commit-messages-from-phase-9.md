---
id: ADR-0029
status: accepted
date: 2026-08-31
decision_maker: Sihan Udayaratna
impact: low
supersedes: null
superseded_by: null
---

# 0029 — Write short, plain commit messages from Phase 9 onward

## Chosen

From Phase 9, a commit message is a single line — or at most two or three short
sentences — describing what changed and why in ordinary language; reasoning that
needs depth goes in an ADR, and the commit points at it rather than repeating it.

Phases 0–8 are **not** rewritten. Their commit bodies stay exactly as they are.

## Rejected

| Option | Why not |
| ------ | ------- |
| Keep the long-form bodies of Phases 0–8 | They were duplicating ADRs. Nearly every commit body long enough to be worth reading restated an argument that was already recorded, better and in a form built for it, in `docs/adr/`. Two copies of an argument is one copy that can go stale, and the copy inside a commit is the one that can never be revised. |
| Rewrite the existing history to match | Rewriting `main` is exactly what ADR 0007 exists to forbid, and the history through Phase 8 is a dogfood corpus this project is going to run itself over. Restyling it would edit the evidence. A style change that starts on a date and is written down is a fact about the project; a retroactive one is a fact about nothing. |
| Say nothing and just start writing shorter | This is the change that would have been invisible. A reader hitting Phase 9 after Phase 8 sees the bodies collapse and has no way to tell a deliberate convention change from an author who stopped caring. That reading is available precisely because the earlier bodies were so long. |
| Drop commit bodies entirely | A subject line under 72 characters cannot always carry the *why*, and Conventional Commits' body is the right place for the one or two sentences that can. The change is about length and register, not about removing the field. |

## What was known at the time

Eight phases of commits exist, and their bodies run to several paragraphs each.
`CLAUDE.md` has said "bodies explain why, not what" since Phase 0, and that
instruction was followed to the point of essay length.

Twenty-nine ADRs also exist. In the overwhelming majority of cases, a commit body
long enough to be worth reading was arguing something an ADR in the same commit
or an earlier one already argued — ADR 0026 and the `FusionBridge` commit, ADR
0028 and the `fuse_store` commit, ADR 0025 and the calibrator's re-routing. The
duplication is visible by reading the two side by side.

What was **not** known: whether the shorter form loses anything a reader of this
repository actually wants. It might. The mitigation is that nothing is being
deleted — the depth moves to the ADRs, which is where a reader looking for
depth already goes first because `CLAUDE.md`'s opening line points them there.

Also not known: whether the ADR corpus grows faster as a result. If depth has to
land somewhere, and commits stop absorbing it, ADRs are the sink. That is the
intended direction and is worth watching rather than assuming benign.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Commit bodies from Phase 9 stay short — a subject plus at most a few short sentences | `phase_9_commit_body_lines <= 4` | `on_event("a phase 9 or later commit needs a body longer than four lines")` |
| 2 | The depth lands in ADRs rather than being lost, so the ADR count keeps growing per phase | `adrs_added_in_phase >= 2` | `on_event("a phase ships with fewer than two ADRs")` |
| 3 | No commit before Phase 9 is rewritten, ever | `rewritten_pre_phase_9_commits == 0` | `on_event("any history rewrite of a pre-Phase-9 commit is proposed")` |

## Consequences

**Accepted costs.** A reader who wants to know why a specific change was made
now has one more hop: read the subject, then open the ADR it names. That is a
real cost for the minority of changes too small to deserve an ADR and too subtle
to explain in a sentence, and those will occasionally be under-explained. The
`commit-msg` hook is unchanged and still enforces Conventional Commits, so the
subject line carries the same discipline it always did.

**Reversal cost.** Nearly none going forward — the convention is a paragraph in
`CLAUDE.md` and this file. Reversal is not retroactive in either direction:
Phases 0–8 keep their long bodies whatever happens, and Phase 9 onward keeps its
short ones. That asymmetry is the point, and it is why this is written down
rather than silently adopted.
