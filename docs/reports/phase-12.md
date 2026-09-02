# Phase 12 — polish, and Praxis on its own history

> The last phase, and the only one that adds no component to the pipeline. Its
> subject is the pipeline's own output: a README a stranger can follow, a
> profile instead of a guess, a routing table read whole for the first time,
> and the demonstration the whole project was built toward — Praxis reporting
> where its author was wrong.

## What shipped

| | |
| --- | --- |
| Branch | `feat/phase-12-polish`, cut from `main` at `3048529` |
| ADRs | 0038 |
| Schema | version 4 — **no migration**, for the seventh phase running |
| New commands | none. No new agents, as `EST-0013` said |
| ADR predicates | 151 of 152 parse, **0.9934** |
| Suite | **3431 passed**, coverage **98.57%** (gate 85%) |
| Demo dataset | **621 records** from 37 ADRs and `docs/dogfood/` |

---

# The closing demonstration

Everything below came out of the shipped commands run against this
repository's own committed history. No number here was assembled by hand, and
[`tests/demo/test_history.py`](../../tests/demo/test_history.py) asserts the
ones this report quotes so that prose and store cannot drift apart.

## 1. A decision of this project's own is standing on a false premise

`praxis demo seed` evaluates 152 predicates and breaches exactly one:

```
152 predicate(s) evaluated against docs/dogfood/facts.json — 1 breached
  The predicate `segmenter_f1 - paragraph_floor_f1 >= 0.05` evaluated false
  against the facts this run was given, so the assumption "Block grouping
  extracts better than the deterministic paragraph floor" no longer holds.
  Resting on it: D-0011.
```

[ADR 0011](../adr/0011-semantic-segmentation-over-a-deterministic-block-grid.md)
was written in **Phase 3**. It chose semantic segmentation over a deterministic
paragraph grid and staked that choice on a number nobody could measure at the
time, with an expiry of `when(phases_completed >= 10)`. Phase 10 built the
harness, the expiry fired, and both approaches scored an identical F1 of
**0.1250** — a difference of **0.0000** against a required **0.05**.

The value of this is not that a number was disappointing. It is the shape:
**an assumption written seven phases before the tool that could check it, with
an expiry condition that fired on schedule, evaluated by the shipped predicate
evaluator rather than judged by eye, resolving against its own author.** That
is the entire Half A claim, executed on the only corpus where the outcome could
not have been arranged.

It is recorded as breached against *the facts this run was given* and not as
false about the world, and the distinction is not a hedge: offline the
segmenter's grouping is synthesised by the mock, so the measurement is evidence
about `praxis.llm.synthesis`. What it closes is the excuse. The number was
unmeasurable for seven phases, it is measurable now, and it is not in the
assumption's favour.

## 2. It measured its author, and then refused four times out of five

```
claude-opus-5: agent-implementation work is 1.5305x over, n=8, confidence=0.4476
claude-opus-5 / data-modelling: n=1, 1 estimates, 1 resolved
claude-opus-5 / frontend: n=1, 1 estimates, 1 resolved
claude-opus-5 / llm-integration: n=1, 1 estimates, 1 resolved
claude-opus-5 / scaffolding: n=1, 2 estimates, 1 resolved
```

One measurement and four refusals, each naming how far short it is.
`BiasDetective` will not state a factor below five resolved outcomes and there
is no override — not a default, not a keyword argument
([ADR 0024](../adr/0024-dispersion-widens-the-band-and-only-n-refuses.md)).
Below the threshold the arithmetic is never run, so there is no withheld number
on the returned object for a determined caller to reach.

**Four fifths of this system's output about its own author is a refusal, and
that is the product rather than a shortfall.** A factor fitted to one point is
indistinguishable, in a table, from one fitted to two hundred.

## 3. The claim this project had made about its own corpus was wrong

`OUT-0012` closed Phase 11 with a sentence that shaped the Phase 12 brief:
*five classes, eleven resolved outcomes, and every single one over-estimated.*
Phase 12 pulled the ratios out of the store rather than trusting it.

| Estimate | Class | Estimated | Actual | Ratio |
| --- | --- | --- | --- | --- |
| EST-0001 | scaffolding | 2.5 | 1.1 | 0.4400 |
| EST-0002 | data-modelling | 2.0 | 4.2 | **2.1000 under** |
| EST-0003 | llm-integration | 4.5 | 5.6 | **1.2444 under** |
| EST-0004 | agent-implementation | 5.5 | 4.2 | 0.7636 |
| EST-0005 | agent-implementation | 6.0 | 2.75 | 0.4583 |
| EST-0006 | agent-implementation | 6.5 | 4.6 | 0.7077 |
| EST-0007 | agent-implementation | 5.0 | 2.8 | 0.5600 |
| EST-0008 | agent-implementation | 5.5 | 2.1 | 0.3818 |
| EST-0009 | agent-implementation | 3.6 | 3.5 | 0.9722 |
| EST-0010 | agent-implementation | 3.6 | 2.0 | 0.5556 |
| EST-0011 | agent-implementation | 3.1 | 3.6 | **1.1613 under** |
| EST-0012 | frontend | 8.5 | 4.5 | 0.5294 |

*(These are the twelve outcomes that existed when the claim was made.
`OUT-0013` closed afterwards and is section 9.)*

Ratios are `actual_active / estimated_active`, the comparison `BiasDetective`
makes — active against active, because a phase that ran long waiting on
somebody else is not an estimation error.

**Nine over and three under. The claim was false.** Two whole classes point the
other way, and so does one row inside the class that speaks.

This is the finding that matters most about the demonstration, because of where
it came from. The false claim was in a **handover document written by the same
author**, it had already shaped a phase brief, and it survived because prose is
not checked. The ratios were in the store the whole time. The correction is now
[four tests](../../tests/demo/test_history.py) rather than a sentence.

## 4. Is the bias the estimator's, or the class's?

If every group points the same way, work class is not doing any work and
[ADR 0024](../adr/0024-dispersion-widens-the-band-and-only-n-refuses.md)'s
grouping key is decoration. Three measurements, none of them an opinion.

**Dispersion and direction.** Pooled over all twelve, the central tendency is
**0.7225** with a band of **[0.4346, 1.2010]** — which *contains 1.0*. Within
`agent-implementation` at n=8 it is **0.6534** with **[0.4491, 0.9506]** —
which *excludes it*. Log dispersion is **0.5082** pooled against **0.3749**
within.

> **The class group establishes a direction and the pooled group does not.**
> Pooled, this estimator's over-estimation is not distinguishable from noise at
> one sigma. Split by class, it is. That is evidence *for* the grouping key.

**Leave-one-out predictive error.** Each of the twelve rows predicted from the
other eleven, groups under `n = 5` falling back to no correction, scored as
mean absolute log error:

| Predictor | Mean abs log error |
| --- | --- |
| No correction | 0.5101 |
| One pooled factor | 0.4432 |
| Per class (ADR 0024) | **0.4316** |

Every correction beats no correction. The two keys are **0.0116** apart, which
is noise at n=12.

**And the awkward third measurement.** Restricted to the eight rows where the
two keys actually differ, **pooled is slightly better** — 0.3352 against
0.3452. Per class wins overall only because refusing at n=1 shields it from the
three under-estimated rows.

**So ADR 0024 stands and is not moved, and it is also not vindicated.** The key
isolates the only group whose bias is distinguishable from noise, which is a
real result; it does not predict better than pooling where the two can be
compared, which is a real limitation. Both sentences are in the record because
picking the flattering one is the failure this project exists to argue against.

## 5. The correction worked on the person who wrote it

`EST-0009` was the first estimate in this project's history that
`BiasDetective` could legitimately speak about, and the first to apply what it
said. Inside `agent-implementation`:

| | n | Geometric mean ratio | Read as |
| --- | --- | --- | --- |
| Logged with **no** correction | 5 | **0.5556** | 1.80× over |
| Logged **with** a correction | 3 | **0.8560** | 1.17× over |

Three points is not proof and the confound is real — the corrected phases are
also the later ones, and this author had learned other things by then. What can
be said is that the direction is right and the size is not small, and that the
estimate which *declined* to correct at `n = 0` immediately afterwards
(`EST-0012`) came in **1.8889× over** and graded `partial`, where the borrowed
factor would have graded `close`. That is `OUT-0012`, and it is the reason
`EST-0013` applied a correction the product itself would have refused.

## 6. What it could not find, and why that is not being patched

**There is no fusion flip on this corpus.** `praxis fuse` does not print an
empty screen; it says what it cannot do and why:

```
No estimated_as edges in the store, so there is nothing to price.
The edge is written by praxis extract, not here — it records that an assumption
turned out to be a quantified forward-looking claim (ADR 0016).
Offline this is the expected result even after extract has run: the mock
provider quotes text it was never shown, so the citation gate refuses most
claims and few assumptions survive to carry an edge. It is the gate working,
not a failure here.
```

Two structural reasons, both established in Phase 11: no real ADR assumption
*is* one of the dogfood estimates, and those edges are written by
`praxis extract` and not by a seeder. The honest routes to one are a `>=`
predicate whose subject a dogfood estimate really binds, or a real extraction
run against a provider whose citations survive the gate.

**Seeding them would have taken about twenty minutes and produced a better
demo.** It is the single most tempting thing in this repository and it has now
been declined twice, in Phase 11 and again here. A fusion layer demonstrated on
edges the demo wrote for itself is not a demonstration of anything.

**Most of what this system believes about itself is unchecked.** Of 152
assumptions: **1** breached, **18** holding, **18** expired, **115**
unverified. Three quarters have never been measured, and the dashboard renders
that as the majority it is rather than filtering it out of the chart.

## 7. The curator found three beliefs this project had written twice

`praxis govern` reads all 152 assumptions and proposes three merges, each a
pair constraining the same quantity with the same predicate in two different
ADRs:

```
Curated 152 assumption(s): 3 to merge, 0 to retire (0%), 149 left alone.
  A-0042 supersedes A-0012: ... the same quantity in the same way
                            (`db_corruption_events == 0`); the later reading stands
  A-0111 supersedes A-0104: (`flips_per_priced_edge <= 0.2`)
  A-0132 supersedes A-0115: (`adrs_added_in_phase >= 2`)
```

Three duplicated beliefs, written months apart by an author who did not
remember stating them the first time. That is the ordinary way an
organisation's memory decays, reproduced faithfully in a repository of
thirty-seven documents by one person — and it is the case `CuratorAgent` was
built for ([ADR 0031](../adr/0031-curation-is-supersedes-and-retraction.md)).
It writes a `supersedes` edge rather than deleting anything, so the store went
from zero `supersedes` edges to three and lost nothing.

**Nothing was retired**, which is also correct: retirement needs an assumption
nobody has touched for `IDLE_DAYS`, and this corpus is three weeks old.

The concede rate beside it — 1 of 1 challenged, 100% — is **not** evidence
about the challenger and the command says so itself: against the mock a bare
boolean comes back true seven times in ten, so the number measures schema
synthesis. It is printed with that caveat attached rather than withheld.

## 8. And it refuses questions it has no record for

```
$ praxis why "why not a javascript framework for the dashboard"
praxis why: no recorded decision rejected the option this question names
  refusal    empty_answer
```

The answer is in ADR 0036 in prose, and the archaeologist declined anyway
because no `rejected` row names that option in the words the question used.
`ArchaeologistAgent` retrieves and never generates
([ADR 0020](../adr/0020-the-archaeologist-retrieves-and-never-generates.md)): a
refusal is the cheap outcome and a confidently wrong quotation is the expensive
one. A system that answered this from adjacent prose would be the thing this
one was built not to be.

## 9. Then it scored this phase, against a prediction made in advance

`EST-0013` did something no earlier estimate in this project did: it applied a
correction **the product itself refuses to make**. Under ADR 0024's key the
group is `claude-opus-5 / scaffolding` at `n = 1`, and `BiasDetective` declines
it. The pooled factor **0.7225** over all twelve resolved outcomes was applied
anyway — and, crucially, **both numbers were written down before any Phase 12
file was created**, so this could be scored rather than argued.

| | Predicted active | Actual | Magnitude | Grade | \|log error\| |
| --- | --- | --- | --- | --- | --- |
| Raw | 4.5h | 2.7h | 1.6667× | `partial` | 0.5108 |
| **Corrected (0.7225)** | **3.3h** | 2.7h | **1.2222×** | **`close`** | **0.2007** |

**The correction cut the error by more than half, and moved the grade.** That
is the exact mirror of `OUT-0012`, where declining to borrow a factor at
`n = 0` turned a close into a partial. Two phases running, the calibration
half was right about its own author and the author's instinct was not.

**What the correction did not fix is the direction.** Even corrected, 3.3h
against 2.7h is still an over-estimate — the pooled factor was too timid rather
than wrong. Ten of thirteen outcomes are now over. The pooled band
`[0.4346, 1.2010]` contains 1.0 only because three under-estimates in two thin
classes drag it there, and a factor fitted to this author's
scaffolding-and-polish work alone would sit nearer 0.5 than 0.72. **At `n = 2`,
nothing is allowed to say so** — and `praxis calibrate` duly reports
`scaffolding: n=2, 2 estimates, 2 resolved` and refuses, which is exactly what
`EST-0013` predicted it would do.

**The work-class choice can now be read, because the test was written first.**
`EST-0013` chose `scaffolding` over minting a sixth class and recorded the
falsifier: *if `OUT-0013` lands far from Phase 0's 0.4400, that is evidence the
two do not belong in one group.* It landed at **0.8182** — nearly twice Phase
0's ratio, and the same direction. So the caveat was justified (polish of a
known system really is easier to estimate than greenfield discovery) and the
class now holds two points that agree on direction and disagree on size. That
is precisely the state `BiasDetective` exists to refuse to speak about.

**The estimate's risk note named the wrong risk first.** It said the likeliest
overrun was the README verification finding commands that did not work. They
all worked; every defect found was editorial. What actually consumed the budget
was a finding the estimate did not anticipate at all — `record_head`, which had
to be measured properly, argued against being fixed, and written up with enough
numbers that the phase taking it need not re-measure. **Declining work
carefully is not cheaper than doing it**, which was the routing review's second
risk and the only one of the three that held.

---

# The rest of the phase

## The README was verified, not proofread

Every command in the README was run against a **fresh `git clone` of the
remote** into a clean directory — not against the working tree, which holds a
warm `uv` cache, a seeded store and `.praxis-tmp/`, all of which hide exactly
the failures a stranger hits. `uv sync --all-groups`, `doctor`, `version`,
`config`, `init`, `demo seed`, `serve` — curled, including `/api/overview` and
`/api/queue` — then the corpus path and the four CI checks.

The install path worked. What did not:

- **Every phase after 0 was still marked unbuilt** in the status table, eleven
  phases after it stopped being true.
- **The shortest path to a real finding was not in the file at all.** Neither
  `praxis demo seed` nor `praxis serve` appeared anywhere in the quickstart.
- `corpus generate` writes **82** documents, not the 12 the README claimed.
- The corpus path needs its own `PRAXIS_DATA_DIR`, or it reads the demo store
  at the same time — the store is append-only, so the two merge.
- The repository layout block listed 2 of the 14 packages.

[`tests/test_readme.py`](../../tests/test_readme.py) now holds it: every
`uv run praxis …` in the file must resolve to a real command, every internal
link must exist, and the record count a stranger sees on their first run is
asserted against the seeder rather than typed in. It caught the count moving
from 608 to 620 within the hour, when ADR 0038 added itself to the corpus.

**The `--help` pass was narrowing, not filling.** An audit before estimating
found all 19 commands and every option already carried help text — zero gaps —
so `EST-0013` priced the smaller job. The work went to the root help, which now
names the three commands to start with and `PRAXIS_DATA_DIR`, and to the
entries that named a command without saying what comes back. Two tests hold it.

## The performance pass found three things and fixed two

Profiled with `cProfile`, not guessed at.

**`praxis demo seed` was committing once per record.** 642 commits, **0.919s of
a 2.8s run**, each one an fsync. The whole seed now runs in one transaction —
`praxis.store.connection.transaction` joins rather than nests, so every `add`
lands in it. Measured in-process against a file-backed store, seven runs:

| | Best | Median |
| --- | --- | --- |
| Before | 1.331s | 3.902s |
| After | **0.218s** | **0.223s** |

The variance collapsing matters as much as the median: 642 fsyncs are at the
mercy of the disk and one is not. And the better half is not speed — **a
half-seeded append-only store is no longer reachable**, which is a state
nothing could have tidied up afterwards.

**`schema_for` regenerated a Pydantic JSON schema on every structured call**, at
roughly 2ms each, on a function that is a pure function of its argument. Cached
on the model class and handed out as a `MappingProxyType`, because a shared
object that callers can edit is the bug that trade buys. 1000 calls: **1.937s →
0.0001s**.

**The third is not fixed, deliberately.** `record_head` is
`SELECT id, MAX(version) FROM record_version GROUP BY id`, and a view over an
aggregate cannot be flattened into its caller — so SQLite re-runs the whole
group-by on *every query that joins it*, which is every `current_*` view and
therefore every read in the system. `EXPLAIN QUERY PLAN` shows `SCAN
record_version` plus an `AUTOMATIC COVERING INDEX` built and thrown away per
call. Measured on two copies of one 816-version store, each on its own
connection:

| `record_head` definition | `links_from` |
| --- | --- |
| `MAX(version) GROUP BY id` (shipped) | 0.3694 ms |
| Correlated `WHERE version = (SELECT MAX…)` | **0.0078 ms** |

**47×**, and the gap widens with the store, because the current cost is linear
in total versions where the alternative is not. In one `praxis eval` run it is
1.530s inside `graph._links` and 1.262s inside `calibration_history` — 2.8s of
a 20s profile.

It is not fixed here because it redefines a core view, which is a **migration to
schema version 5** after six phases at version 4, and this phase was scoped to
presentation, performance, routing and documentation — not to the store. Doing
it quietly inside a polish pass is exactly what the brief ruled out. It is in
[`BACKLOG.md`](../../BACKLOG.md) with the numbers, so the phase that takes it
does not have to re-measure.

## The routing table was priced, and nothing moved

The brief asked whether every agent is on the cheapest tier meeting its bar per
Phase 10's ablation numbers. **The ablation table cannot answer that**, and
saying so is the first result: every `Cost/doc` cell in it reads `0.000000`
because the mock is free, and **no rung varies a model tier** — the ladder
ablates components. It contains no observation of any agent on any other tier.

So the evidence was built. `praxis eval` now prints what each agent *would*
cost at the real rates in `praxis/config/models.py`, from the tokens an offline
run really sends:

| Role | Projected USD/doc | Share |
| --- | --- | --- |
| reason | 0.038632 | **49.3%** |
| extract | 0.029935 | 38.2% |
| scan | 0.009759 | 12.5% |
| **Total** | **0.078326** | |

Two things fall out immediately. The `scan` tier is an eighth of the bill, so
re-tiering *into* it is a rounding error. And an 82-document run projects to
**$6.42** against a `cost_ceiling_usd` default of **$5.00** — the first live
eval run will stop part-way. Nobody could have known that before the column
existed.

**Nothing moved tier**, and
[ADR 0038](../adr/0038-the-routing-table-stands-and-the-projection-is-why.md)
argues each candidate rather than asserting the conclusion.
`AssumptionFormalizer` is 25% of the bill and its job reads like schema-filling
— it stays because **a wrong predicate is silent**: it parses, it evaluates, the
monitor believes it, and a decision is held or breached on an expression nobody
re-read. `OutcomeMatcher` is 24% and stays because a wrong pairing writes a
wrong ratio into the calibration history every later factor is computed from.
`ArchaeologistAgent` looked like the easiest win of all and turns out to make
**no calls in a corpus run at all** — it fires once per `praxis why` — so
re-tiering it saves nothing measurable.

**The cost optimisation that mattered already happened, and it was not a
re-tiering.** Five agents did not move to a cheaper model; they left the routing
table entirely and became arithmetic — `CalibratorAgent` in Phase 7,
`FusionBridge` and `CollateralAgent` in Phase 8, `CuratorAgent` and
`AbstentionGate` in Phase 9. That is five of twenty agents reduced to zero,
permanently, and the pattern behind all five is one thing: each was paying a
model to make a judgement **somebody upstream had already been paid to make**.
That is a better question to ask of the remaining fifteen than "could this run
on a smaller model".

## The pitch is a routing document

[`docs/PITCH.md`](../PITCH.md) argues nothing new. `docs/FUSION.md` is the
claim, the phase reports are the evidence, the ablation table is the metrics
and the dashboard is the demo — what was missing was an order to read them in.
What it adds beyond ordering is a **What is not true yet** section carrying the
zero fusion edges, the offline caveat, `n = 1` on four of five classes and the
115 unverified assumptions. Putting the limitations on the same page as the
claim is the only version of this pitch the project can make coherently.

---

## `EST-0013`, and the correction it applied

`EST-0013` is the first estimate in this project's history to **apply a
correction the product itself would have refused**. Under ADR 0024's key the
group is `claude-opus-5 / scaffolding` at `n = 1`, and `BiasDetective` declines
it. The pooled factor **0.7225** over all twelve resolved outcomes was applied
anyway, taking 4.5h raw to **3.3h**, with the raw figure kept in
`raw_active_quantity` so `OUT-0013` grades both.

The reasoning is in the estimate and it is the analysis in section 4 above:
pooling has twelve points where the key has one, leave-one-out says correcting
predicts ~13% better than not correcting, and `OUT-0012` had just recorded that
declining to correct cost accuracy. Confidence stayed at 0.40 rather than
rising, because the pooled band contains 1.0 and that is a real weakness in the
number being applied.

**The work class is `scaffolding` rather than a sixth class**, and the
alternative was argued rather than skipped. ADR 0023 names class invention as
the failure this project watches for — a run that invents a class per estimate
fragments a history until no group ever reaches `n = 5`, and six classes over
twelve phases with four permanently at `n = 1` is that failure spelled
politely. `frontend` earned its own class in Phase 11 on a claim Phase 12
cannot make. The honest caveat is recorded too: Phase 0 was greenfield
discovery and this is polish of a known system, so if `OUT-0013` lands far from
Phase 0's 0.4400 that is evidence the two do not belong in one group.

## What Phase 12 did not build

- **No fusion flip**, for the structural reasons in section 6. Not seeded.
- **No `record_head` fix**, for the reason above. It needs a migration, an ADR
  and a phase of its own.
- **No tier change**, because nothing in this repository could tell a good one
  from a bad one. The shortlist and its experiment are in ADR 0038.
- **No repair to the curator's merge recall**, which prints above 1.0 and so is
  not a recall. Both counts are right and the label is wrong; fixing it means
  deciding what the number should be, which is a metric definition rather than
  a polish task. In `BACKLOG.md`.
- **`.codex/` and `AGENTS.md` remain untracked**, for the sixth phase running.
