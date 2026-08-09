---
id: ADR-0007
status: accepted
date: 2026-08-09
decision_maker: Sihan Udayaratna
impact: medium
supersedes: null
superseded_by: null
---

# 0007 — Merge commits into `main`, never squash

## Chosen

One branch per phase off `main`, a sub-branch per agent where a phase builds
several, 8–20 conventional commits per phase, merged into `main` with
`--no-ff`. Never `--squash`. `main` is green and releasable after every phase,
and every phase is tagged.

## Rejected

| Option | Why not |
| ------ | ------- |
| Squash merges | Produces a tidy `main` by deleting the thing this project is partly being judged on. It also destroys the commit-level corpus that Phase 12's self-analysis reads, and it is irreversible once the branch is gone. |
| Rebase and fast-forward | Keeps individual commits but flattens the branch structure, so the network graph no longer shows each agent as a separate strand of work. |
| A `develop` branch | Adds a merge step and a second long-lived branch to a single-contributor project. The requirement it usually serves — keep `main` releasable — is already met by requiring CI-green merges. |

## What was known at the time

Single contributor. `gh` is authenticated with `repo` scope. Branch protection
on private repositories requires a paid GitHub plan, so it may not be
available; if it is not, the same rules are enforced locally by the pre-commit
`no-commit-to-branch` hook and the Claude Code `guard_git_workflow` hook, which
blocks both direct commits to `main` and any `--squash`.

Not known: whether branch protection can be configured on this account, and
whether hook-level enforcement alone is convincing to a judge who is looking
for server-side guarantees.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Granular history is legible rather than noise | `commits_per_phase between 8 and 20` | `when(phases_completed >= 4)` |
| 2 | `main` stays releasable at every tag | `main_ci_green_at_every_tag == true` | `on_event("a phase merges with CI red")` |
| 3 | Local hooks are sufficient where server-side protection is unavailable | `direct_commits_to_main == 0` | `on_event("branch protection becomes available")` |
| 4 | Commit subjects parse as Conventional Commits, so Phase 12 can read them | `conventional_commit_ratio >= 0.95` | `on_event("Phase 12 self-analysis runs")` |

## Consequences

**Accepted costs.** `main`'s history is busier than a squashed one, and
bisecting crosses more commits. Neither matters at this size, and both are
outweighed by the history being a deliverable.

**Reversal cost.** Asymmetric, which is why the rule is enforced by a hook
rather than a convention. Choosing to squash later loses information
permanently; choosing to stop squashing later cannot recover what was already
squashed.
