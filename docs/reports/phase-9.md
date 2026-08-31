# Phase 9 — adversarial and governance

> The first phase that adds no extraction surface. Everything through Phase 8
> produces records; this decides what the records are worth, and it is the only
> layer in the system whose headline output is a count of things it refused to
> say.

## What shipped

| | |
| --- | --- |
| Branch | `feat/phase-9-adversarial-governance`, cut from `main` at `a79dc0e` |
| Sub-branches | 3, one per agent, merged `--no-ff` |
| Commits | 13, one logical change each |
| Suite | **2948 passed**, coverage **98.97%** (gate 85%) |
| New modules | 6, every one at **100%** |
| ADRs | 0029, 0030, 0031, 0032 |
| Schema | version 4 — **no migration**, for the fourth phase running |

`praxis/agents/challenger.py` (`ChallengerAgent`), `curator.py`
(`CuratorAgent`), `abstention.py` (`AbstentionGate`), `governance.py`
(`govern_store`), `praxis/cli_govern.py` (`praxis govern`),
`praxis/eval/governance.py`, and `praxis/prompts/texts/challenge_finding.v1.md`.

## The metrics table

Against the 16-document synthetic corpus, offline:

| | |
| --- | --- |
| Findings standing | 0 |
| Challenged / decided | 0 / 0 |
| **Concede rate** | **no measurement** — nothing was decided |
| Assumptions curated | 0 |
| **Retirement rate** | **0** (0 retired of 0) |
| **Abstention rate** | **0** (0 withheld, 0 concluded) |
| Merges proposed | 0 of **4** the corpus labels — recall **0.0000** |
| Verdicts carry their challenge | yes |
| Nothing retired was depended on or deleted | yes |
| The gate agrees with its own rules | yes |

**Every one of those zeros is correct, and the reason is not this phase's.** The
offline pipeline was run end to end *before* `EST-0010` was written, not after
it failed: 16 documents, 145 spans, and then **1 decision, 0 assumptions, 0
findings**, because ADR 0016's citation gate refuses 38 of 40 claims against a
mock that quotes text it was never shown. A governance layer over a store with
no findings and no assumptions has nothing to govern, and reports so.

The three booleans are what carry a claim here, exactly as in Phases 7 and 8.
They hold, and each is tested in **both** directions — including the two that
only a monkeypatched gate can falsify, because a consistency claim that can only
be true is not a claim.

## The concede rate, and the bound it was measured against

The product owner set the terms in advance: **below 5% or above 60% is a broken
agent, not a passing test, and the corpus is not to be tuned until the number
moves.** So it was measured rather than avoided.

Against 40 synthetic findings with the mock:

| | |
| --- | --- |
| Findings put to the challenger | 40 |
| Answered about | **6** |
| Decided | 5 |
| Conceded | 1 |
| **Concede rate** | **0.2000** |

**0.2000 is inside the band, and it is not evidence about the challenger.** Two
facts about `MockProvider` produce it and neither is about reasoning:
`praxis/llm/synthesis.py` answers a bare boolean true seven times in ten
(`_TRUE_BIAS`), and it synthesises about two array entries per call whatever the
batch holds — which is why **34 of 40 findings went unargued**.

That second number is the more useful finding and it is reported rather than
smoothed: offline, the challenger argues a *minority* of what it is given. The
agent counts those as `unjudged` rather than as concessions nobody made, which
is the refusal working. `BACKLOG.md` records why the mock is **not** being
repaired to fix it: making it answer about every ordinal would make it a better
challenger than it is a model, and every other agent's offline numbers would
then be measuring a mock special-cased for one of them.

**No response schema was reshaped to move the number.** `Judgement.upheld`
mirrors `Verdict`'s own polarity, because the field should follow the record it
is written into. Had it been spelled `overturns` instead, the mock's bias would
have produced roughly 0.7 and blown the upper bound — and the temptation to
choose the spelling by its effect on the metric is exactly what ADR 0030's
assumptions 1 and 2 exist to move to a real run.

## The model-call decision, per agent

| Agent | Call? | Why |
| --- | --- | --- |
| `ChallengerAgent` | **Yes, `reason`** | Its input is a `prosecution` in prose that nobody reduced to a record, and no arithmetic decides whether a case is sound |
| `CuratorAgent` | No | A `CONTRADICTS` edge Phase 5 already paid a `reason` call for, two parsed predicates, two timestamps, an audit trail |
| `AbstentionGate` | No | A confidence, a span count, two enums and a boolean |

**The streak broke exactly once, exactly where it was predicted to.** Six
consecutive components moved to the arithmetic side across Phases 7 and 8, and
ADR 0027 — the last of them — wrote down where the run would stop:

> That also predicts where it stops: `ChallengerAgent` and `ReviewTriageAgent`,
> whose input is prose nobody has reduced to a record, should keep their routes.

`EST-0010` priced the phase on that prediction holding. It held, and the other
two components went the other way, taking the run to nine on either side of the
break. **The arithmetic/judgement boundary has now moved a component in six
consecutive phases, and this is the second in which the estimate called the
direction in advance** — and the first in which it called a component moving
*back*.

## Three axes on which this phase cost no migration

Checked against the records before pricing, not assumed:

1. **The challenge needed no storage.** `Finding` has carried `prosecution`,
   `challenge` and `verdict` since Phase 1, with a validator binding the last
   two together, and `Verdict`'s own docstring reads *"What survived
   `ChallengerAgent`"*. Phase 1 built the record for this agent.
2. **Curation needed no new mechanism.** Merging is `LinkType.SUPERSEDES`,
   defined in Phase 1 as "a newer record replaces an older one of the same
   kind"; retiring is `Repository.retract`, whose own docstring already states
   the property the scope asked for — *"Not a delete. The withdrawn versions
   stay readable, so a retraction is a statement about a record rather than the
   disappearance of one."*
3. **Abstention is never stored.** A disposition is recomputed each time, the
   same argument `praxis.agents.calibration` makes about a calibration factor. A
   `needs_human` column and a new `FindingKind` member would both have been
   migrations, and both would have been storing a derived value that goes stale
   the moment a challenge lands.

## What "never fires" means, precisely

An assumption is **idle** when its status is still `UNVERIFIED`, its audit trail
carries **no event written by `AssumptionMonitor`**, and its **first** version
was written at least `IDLE_DAYS` (60) ago. `Repository.audit_for` is the whole
query. No parallel bookkeeping, no counter, no second table.

**The clock is read off the trail rather than the record**, and that was found
by a test rather than designed in: `Repository.revise` overwrites `created_at`,
so measuring off the current version would let any unrelated write reset the
window — a formalizer rewriting a predicate would make an assumption nothing had
ever settled look brand new.

**The known limitation is stated rather than hidden.** `praxis.monitor.run`
writes only on a change, so a monitoring pass that decided nothing leaves no
audit row, and this cannot tell "monitored repeatedly and never settleable" from
"never monitored at all". `UNVERIFIED` means *nothing has ever settled it* in
both cases, and an assumption nothing can settle is dead weight whichever way it
got there. ADR 0031 assumption 3 is written to fire if that stops being true.

## The refusal that matters most

**An idle assumption a live `Decision` assumes is not retired.** It is an
unverifiable belief underneath a live decision, which is a finding for a person
rather than dead weight for a curator — retiring it would quietly remove the
very thing that makes the decision worth re-reading. `praxis govern` gives it
its own section, apart from the housekeeping refusals, because grouping it with
"written too recently" would bury the only line here worth interrupting somebody
about.

## The corpus was checked before it was extended

The discipline Phases 5 and 6 both applied, and it found a real gap and a real
non-gap.

**The gap:** the corpus states no findings and no challenges, and offline
produces none. **Deliberately not filled.** A finding is *produced*, not stated;
planting one as ground truth would grade the harness against itself.

**The non-gap:** the corpus already holds 4 revision notes, each saying in words
that an earlier assumption is no longer true. That is a stated `supersedes`
relation, and it is now planted beside the `contradicts` already there — one
edge type, no more. The distinction against `BACKLOG.md`'s refusal to plant
cross-document `estimated_as` edges is the one that matters: **the source states
this relation, so labelling it is reading the document rather than fixing the
answer before anyone asked the question.**

It buys the one comparison against truth in this quarter of the table
(`merge_recall`, 0 of 4 offline for the reason above) and it will grade a real
run.

## Three defects found by running the CLI rather than reading it

Phase 8 found two this way; the practice keeps paying.

1. **The empty store said "nothing changed".** Which tells a person their
   governance pass is settled when it has never had anything to settle. It now
   distinguishes three states: nothing to govern, nothing changed, and every
   finding already decided.
2. **`--dry-run` asserted a retraction that had not happened.** It was feeding
   the gate the retirements it had only *proposed*, so it printed *"A-0003 has
   been retracted"* about a record still standing. A dry run may say what would
   happen; it may not assert something untrue about the store as it is.
3. **The whole argument section vanished on a dry run**, leaving a reader to
   infer from a missing heading that no verdict had been reached.

## Deviations, stated rather than buried

- **`praxis/agents/curator.py` is 581 lines**, against the style guide's "under
  ~400". The largest file in the repository before this was 540
  (`agents/extractor.py`). Not split: the merge rules and the retirement rules
  share one refusal vocabulary, and separating them would put `Refusal` in a
  third module that both import to say the same things.
- **`ReviewTriageAgent` is not in this phase.** Named in `ARCHITECTURE.md`'s
  fusion box and deferred again, with the reason *changed* rather than repeated
  — Phase 9 built the gate that decides what may enter a queue, and writing the
  ranking in the same phase would design a consumer against a producer written
  the same week. Moved to Phase 11 in `BACKLOG.md`.

## Commit messages changed, and it is a decision

From this phase, a commit message is a line or two of plain language pointing at
an ADR, not an essay. Phases 0–8 keep their long bodies and are **never**
rewritten — rewriting them would edit the dogfood corpus this project is going
to run itself over. Recorded as
[ADR 0029](../adr/0029-short-commit-messages-from-phase-9.md), because a style
change that starts on a date and is written down is a fact about the project and
a silent one is a fact about nothing.

## The estimate

`EST-0010`: **3.6h active**, corrected from a raw 6.5h by the live factor. The
second bias correction this project has applied, and the factor was recomputed
in-session rather than copied — the brief quoted `1.7999x over, n=5,
confidence 0.3875`, which was the state *before* `OUT-0009` closed. The live
figure at `n = 6` is **factor 0.5527, 1.8093x over, band 1.39x to 2.35x,
confidence 0.4329**.

**Confidence did not move, and that is the point.** `OUT-0009` was the first
exact match in this project's history and the first correction that worked, and
it is n = 1. Raising confidence on one result would be `EST-0008`'s mistake with
the sign flipped. The whole content of the estimate stayed in the quantity.

**The classification was decided explicitly**, because the phase mixes one
LLM-reasoning component with two deterministic ones. It is `agent-implementation`:
the class is defined by the shape of the work, not by how many components call a
model, and the fitted population already spans the full range internally — Phase
7 called no model and produced the worst ratio in it (2.62×), Phase 5 called five
and produced 1.41×. Splitting the class on model-callingness would drop every
sub-class below `n = 5` and silence `BiasDetective`, which is ADR 0024's refused
override arriving through a side door. Refused in writing for the second phase
running.

See `OUT-0010` for what it actually cost.
