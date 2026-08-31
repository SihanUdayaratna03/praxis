---
id: ADR-0033
status: accepted
date: 2026-09-01
decision_maker: Sihan Udayaratna
impact: low
supersedes: null
superseded_by: null
---

# 0033 — Write short, plain comments and docstrings from Phase 10 onward

## Chosen

The style change [ADR 0029](0029-short-commit-messages-from-phase-9.md) made to
commit messages at Phase 9 extends, at Phase 10, to **code comments and
docstrings**. This is a second stage of one decision, not a new one: commits,
comments and docstrings should all read the way a working engineer writes them
day to day.

From Phase 10, a docstring says what the thing does in one or two short lines,
and adds why only when the why is not obvious. A comment explains a choice a
reader would otherwise have to reconstruct, in a sentence. Neither carries an
argument at essay length, and neither carries literary prose.

Reasoning that genuinely needs depth goes in an ADR or in `docs/`, and the code
points at it with a short comment naming the file — the same move ADR 0029 made
for commits.

**Nothing written in Phases 0–9 is rewritten.** Those docstrings and comments
stay exactly as they are, for the same reason ADR 0029 left the earlier commit
bodies alone.

## Rejected

| Option | Why not |
| ------ | ------- |
| Restyle the existing docstrings to match | The same objection ADR 0007 raises against rewriting history, one layer down. The Phase 0–9 code is a dogfood corpus this project will run itself over from Phase 12, and its module docstrings are some of the richest prose in it. Restyling them edits the evidence, and it would be a very large diff that changes no behaviour — the worst kind of diff to have to review. |
| Leave docstrings long and change only commits | This is the status quo after ADR 0029, and it is incoherent. The reason commit bodies were duplicating ADRs is that the depth had nowhere else to go, and the same is true of a module docstring that argues a design at four paragraphs. Fixing one of the two places and not the other leaves the duplication and moves it. |
| Relax the docstring requirement itself | Not the problem. `CLAUDE.md` has asked for a docstring on every public module, class and function since Phase 0, and that rule is a good one — `mypy --strict` and ruff's `D` rules both lean on it. The change is about length and register. Every public thing still gets a docstring. |
| Make it a lint rule with a line cap | A cap counts lines, and the thing worth avoiding is not length but *duplication of an argument recorded elsewhere*. A 6-line docstring listing a function's four failure modes is fine; a 6-line one re-arguing ADR 0016 is not, and no line counter can tell them apart. Judgement, recorded here, with the ADRs as the sink. |

## What was known at the time

Nine phases of code exist. Several module docstrings run past 30 lines —
`praxis/agents/citation.py` numbers and justifies the order of its four checks,
`praxis/llm/synthesis.py` argues three properties before any code, and
`praxis/cli_eval.py` opens with three headed decisions. Every one of those is
well written and several of them are the clearest explanation of their subject
anywhere in the repository.

That is precisely why this is a stage change rather than a correction. The old
prose is not being called a mistake. What is known is that thirty-two ADRs now
exist, `docs/` holds reports and an architecture document, and the places for
depth are no longer scarce — so a docstring absorbing an argument now duplicates
rather than preserves it.

What is **not** known: whether the shorter form makes the code harder to pick up
cold. It might, and the first phase or two are the wrong sample to judge from,
because the reader has the Phase 0–9 prose sitting next to the new code. The
honest test is a phase written entirely in the new register, read months later.

Also not known, and inherited from ADR 0029: whether the ADR corpus grows faster
because of this. That is the intended direction; two style stages now push depth
the same way, so the effect should be larger and easier to see than after Phase
9 alone.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Module docstrings written from Phase 10 stay short | `phase_10_module_docstring_lines <= 8` | `on_event("a phase 10 or later module needs a docstring longer than eight lines")` |
| 2 | Nothing written before Phase 10 is restyled | `restyled_pre_phase_10_docstrings == 0` | `on_event("any restyle of pre-Phase-10 prose is proposed")` |
| 3 | The depth lands in ADRs rather than being lost, at the rate ADR 0029 assumed | `adrs_added_in_phase >= 2` | `on_event("a phase ships with fewer than two ADRs")` |

## Consequences

**Accepted costs.** A reader of Phase 10 code gets less from the file itself and
has to open an ADR more often. That cost is real and it falls hardest on the
changes too small to deserve an ADR — the same gap ADR 0029 accepted for commits,
now present in a second place.

The repository also becomes visibly two-toned: Phase 0–9 files with long
docstrings sitting beside Phase 10 files with short ones. That is deliberate and
it is the same asymmetry ADR 0029 chose. A dated style change that is written
down is a fact about the project; a retroactive one is a fact about nothing.

**Reversal cost.** Nearly none going forward, and none backward. The convention
is a paragraph in `CLAUDE.md` and this file. Reversing it would mean writing
long docstrings again from some later phase, which costs nothing already spent.
