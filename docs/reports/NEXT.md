# Handover — start of Phase 8

Read this first, then `CLAUDE.md`. Written at the close of Phase 7 so the next
session can start working instead of re-deriving state.

---

## Where we stopped

Phase 7 is merged, tagged and green. Nothing is in flight. **Both halves are
built, and the calibration half now answers.**

| | |
| --- | --- |
| `main` | `2f72fc8`, local and remote identical |
| Tag | `v0.7-phase-7` → `2f72fc8` (dereferenced through the remote, not assumed) |
| CI | 5/5 green on [#16](https://github.com/SihanUdayaratna03/praxis/pull/16) |
| Open PRs | none |
| Remote branches | `main` only |
| Working tree | clean |
| Suite | **2659 passed**, coverage **98.86%** (gate 85%) |
| Schema | version 4 — Phase 7 needed no migration |

```bash
cd "C:\Users\sihan\OneDrive\Desktop\Praxis Agents"
git checkout main && git pull
uv sync --all-groups
uv run praxis doctor            # expect OK, including one offline model call
uv run pytest                   # expect 2659 passed

# the whole pipeline, end to end and offline
uv run praxis init
uv run praxis corpus generate .praxis-tmp/corpus
uv run praxis ingest .praxis-tmp/corpus/documents
uv run praxis extract           # Half A
uv run praxis estimates         # Half B: extract -> classify -> match
uv run praxis calibrate         # Phase 7: groups, refuses, or measures
uv run praxis formalize
uv run praxis monitor
uv run praxis contradictions
uv run praxis why "why not Postgres full-text search"
```

**Every one of those still reports near-zeros offline, and that is still
correct** — the citation gate refuses 38 of 40 claims against the mock (ADR
0016), so one estimate reaches the store for the whole corpus. `praxis
calibrate` has a *second* reason for its zeros and it is not starvation:
this corpus resolves three outcomes across nine estimates, so no group would
reach five even fully populated, and `BiasDetective` would still refuse. **A run
reporting a factor against a corpus this size would have broken the threshold,
not beaten it.** `docs/reports/phase-7.md` says this in the report rather than
only in conversation.

**Shipped in Phase 7:** `praxis/agents/distribution.py` (the arithmetic),
`bias.py` (`BiasDetective`), `calibrator.py` (`CalibratorAgent`), `scoring.py`
(`ScoringAgent`), `calibration.py` (the store pass), `praxis/cli_calibrate.py`
(`praxis calibrate`), `praxis/eval/calibration.py` and the fourth quarter of the
metrics table, and a fix to `calibration_history` so it narrows in SQL. 199 new
tests in seven files, 228 new overall, every new module at 100%. ADRs 0024–0025.

---

## The result Phase 7 earned

**The manual discipline and the code now agree, and `hypothesis` proves it.**

For eight consecutive estimates this project hand-simulated one rule — refuse
below `n = 5`, don't force a correction. It is now a function, held as a
property over generated distributions, checked in **both** directions (a
refusal property is satisfied trivially by a detective that refuses
everything), with the factor **not computed** below the threshold rather than
computed and withheld, and with no argument on the signature to lower it.

**And then the threshold was crossed by this phase's own outcome.** `OUT-0008`
takes `agent-implementation` to `n = 5`. Run through the code Phase 7 shipped,
over this project's own history:

> `agent-implementation work is 1.7999x over, n=5, confidence=0.3875`

That is the product speaking about its own author for the first time.

---

## Phase 8 — the Fusion Layer

`ARCHITECTURE.md`'s fusion box has three names: `FusionBridge`,
`CollateralAgent`, `ReviewTriageAgent`. This is the phase the whole project
exists to reach. Six things worth knowing before planning it.

**Your calibration query exists, is tested, and is one call.**
`BiasDetective(repository).factor_for(owner=, work_class=)` returns a
`CalibrationFactor` carrying `factor`, `magnitude`, `n`, `considered`, `low`,
`high`, `confidence`, `direction`, `verdict` and `reason`. It is **one indexed
read plus `O(n)` arithmetic** — the narrowing genuinely reaches the database now,
asserted through `EXPLAIN QUERY PLAN` with the unnarrowed case as the control.
It **never returns `None` and never raises**, so an absence and an error are
never the same code path. `factor.describe()` renders the sentence
`ARCHITECTURE.md` promises. Do not build a second path to this.

**A factor is recomputed, never stored, and that is deliberate.** A calibration
finding exists for a person to read, but `Finding.prosecution` is prose and
`subject_id` is one node — so the finding is filed against an *anchor* and the
*query* goes through `BiasDetective`. A stored factor is stale the moment an
outcome lands, and a stale calibration factor is the failure this product exists
to catch. See `praxis/agents/calibration.py`'s module docstring.

**`estimated_as` is the edge `FusionBridge` writes, and one already exists.**
ADR 0016 has `AssumptionExtractor` writing the first `estimated_as` edge
wherever an assumption turned out to be a quantified claim — but only *inside*
an assumption it was paid to read. The cross-document half is Phase 8's, and it
is the one the corpus deliberately does not plant as ground truth (see
`BACKLOG.md`, "Cross-document edges in the corpus ground truth"): planting it
would fix the answer before anyone asked the question.

**Only arithmetic can breach.** ADR 0019, and it constrains the fusion
mechanism directly: `FusionBridge` may *recognise* that a predicate's subject is
an estimate in disguise (judgement, a model call), but whether the calibrated
value violates the predicate is evaluated by `praxis.predicates`, and a model
cannot reach `BREACHED`. Expect the arithmetic/judgement boundary to fall on
exactly that line — it has moved four phases running and it is the single most
reliable line item in this record.

**Cross-document outcome matching is waiting for you.** `BACKLOG.md`, deferred
from Phase 6: `OutcomeMatcher` looks in the estimate's own document only,
because ADR 0015 makes an offering exactly one document's spans. The entry says
to build it in Phase 8, "where the agent that owns the question already exists".

**A schema migration is unlikely but check rather than assume.** Phases 6 and 7
both needed none. `FindingKind.COLLATERAL_IMPACT` and `STALE_DECISION` already
exist from Phase 1, and `LinkType` already has the six edges. The question to
answer against `praxis/domain/records.py` and `001_core.sql` before pricing is
whether a *collateral* finding needs anything a `Finding` cannot hold — Phase 7
found `Finding` could carry the allegation but not the query, and that answer
shaped a component rather than the schema.

---

## Before writing `EST-0009`

**There is a factor now. This is the first estimate that can legitimately use
one, and using it silently would waste eight phases of discipline.**

Recompute rather than trust this paragraph, but as of the close of Phase 7 the
five `agent-implementation` ratios are **1.31× / 2.18× / 1.40× / 1.79× / 2.62×**,
every one an over-estimate. `BiasDetective` states:

| | |
| --- | --- |
| Factor | `0.5556` — multiply a raw estimate by this |
| Read as | **1.80× over** |
| Band | 1.35× to 2.41× |
| `n` | 5 |
| Confidence | **0.3875** |

Run it rather than copying the table:

```bash
uv run praxis calibrate --owner claude-opus-5 \
  --work-class agent-implementation --quantity <raw>
```

**Applying it is a decision, not a formality.** Three things to weigh, and
`EST-0009` should say which way it went and why in the same shape the last eight
refusals did.

1. **The confidence is low by construction.** `n/(n+5)` caps the sample term at
   exactly one half at the threshold, so a group that has only just cleared the
   bar reads as a coin-flip-grade signal. 0.3875 is the design working.
2. **The band is nearly a factor of two.** 1.35× to 2.41×. ADR 0024 made the
   band inseparable from the factor precisely so this cannot be dropped on the
   way to a headline number.
3. **The sub-population question is still open, and it is the one `OUT-0008`
   got wrong.** The factor is fitted across five phases that were four-fifths
   model-driven. Phase 8 is model-driven again — `FusionBridge` and
   `CollateralAgent` are both on the `reason` tier — which actually makes
   `EST-0009` a *better* fit for this factor than Phase 7 was. Say so if you
   apply it.

**Four carry-forward lessons, in the order they will cost you.**

1. **Price the arithmetic/judgement boundary as its own line item.** Fourth
   phase running, fourth time it moved a component to the arithmetic side. In
   Phase 8 it most likely falls between recognising an assumption-as-estimate
   and evaluating the calibrated value against the predicate.
2. **Put a real sub-population difference in the QUANTITY, not the
   confidence.** This is `OUT-0008`'s new lesson and it cost 2.62×. `EST-0008`
   correctly saw that five deterministic modules are cheaper than four
   model-driven agents, and expressed it by raising confidence 0.45 → 0.50 while
   leaving 5.5h alone. Wrong lever.
3. **Price the CLI.** Worked three phases running. Keep doing it.
4. **Price each component's refusal vocabulary inside the component.** Bought by
   `OUT-0005`, confirmed by `OUT-0006`, `OUT-0007` and `OUT-0008`. Still the most
   reliable correction in this record.

**Blocked time is still not an engineering prediction.** Five attempts, five
misses, in both directions: 20.0/12.8, 16.0/72.3, 24.0/17.3, 18.0/0.9, 18.0/2.0.
It measures when the author sleeps. Log it because the record has the field, and
compare active against active.

---

## Rules that have not changed

- **Log `EST-0009` before writing any Phase 8 file**, on the phase branch. A
  phase with no prediction is a hole in the Phase 12 demo.
- **`main` only moves through a reviewed, CI-green merge commit.** Never squash.
- **Long text goes to a file**, never inline: `--body-file`, `-F`. The shell here
  has a ~965-byte parse limit and PowerShell 5.1 mangles embedded quotes.
- **Commit after every component**, then push, then append to this file. Never
  batch. Phase 7 was cut in half by the session limit and lost nothing, because
  every completed component was committed and logged before the next began — the
  one uncommitted file at the cut was still on disk and was found by verifying
  `git status` rather than by trusting the handover.
- Progress explanations to the product owner are in **Sinhala**; everything in
  the repository is in English.

Three things to carry forward from Phase 7's own defects. **Both real bugs this
phase found were found by property tests, not by review** — `magnitude` keyed on
the direction rather than the factor, and a precision floor where a band
collapses. **Every property needs a control**: a refusal property passes against
an agent that refuses everything, and an `assume`-guarded property proves nothing
if the assumption is never satisfiable. Every property in `tests/agents/` now
carries its paired control; keep that up.

And: **`docs/dogfood/` is test fixture data.** `tests/agents/test_scoring.py::TestThisProjectsOwnHistory`
pins the real log — how many outcomes, how many classes, where the largest class
sits — and closing `OUT-0008` broke four of those tests, in CI, on a
documentation-only PR. That is the failure mode the pins exist for and they
worked. What did not work was the check before pushing: after editing the log,
run the **full** suite, not just `test_adrs.py` and `tests/corpus`. `EST-0009`
and `OUT-0009` will both trip these same tests, and `OUT-0009` in particular
takes `agent-implementation` to six, which is where the backtest starts scoring
for the first time.

---

## Phase 7 progress log

One line per component, appended as it landed.

- `877c6f9` **EST-0008 logged** — 5.5h active, 18.0h blocked, confidence 0.50,
  uncorrected for the eighth time at `n = 4`. The irony was explicit — this
  phase builds the component that refuses below 5 while its own class sits one
  short — and it changed nothing.
- `d2eeed4` **`calibration_history` narrows in SQL** (`2c414bb`). Found by
  sketching Phase 8's fusion query against the Phase 6 shape. Both narrowings
  had been applied in Python after `fetchall()`, so the index was never used.
  The **query plan** is now what tests assert, with the unnarrowed case as the
  control. 10 new tests.
- `6e04e96` **`distribution.py`** (`2740fa7`). Geometric mean, log-space spread,
  band, confidence — all `Decimal` inside a pinned `localcontext`. 34 tests,
  **100%**.
- `7c9b7ba` **`BiasDetective`** (`de8a5c1`). Refuses below `n = 5`, no override,
  factor **not computed** below it. Four verdicts, not a boolean. Phase 8's
  fusion query executed rather than sketched. A property found `magnitude` keyed
  on the direction rather than the factor. 44 tests, **100%**.
- `808bc84` **ADR 0024** (`064cd68`). Dispersion widens the band; only `n`
  refuses. `README.md` corrected: its confidence illustration was 0.79 and the
  shipping formula gives 0.59.
- `c642439` **`CalibratorAgent`** (`98ad5b1`). Pass-through as the primary path.
  Traceability held in its strongest form — a property recomputes the corrected
  figure from the factor the result cites. 26 tests, **100%**.
- `9da7c82` **`CalibratorAgent` becomes deterministic** + **ADR 0025**. Out of
  ADR 0006's `extract` row into `NON_LLM_AGENTS`. ADR 0006 **amended, not
  superseded**.
- `c3ac70d` **`ScoringAgent`** (`5cf1a00`). Prequential backtest, `graded`
  beside `score`. Against this project's own history it scores **exactly
  nothing**, which is the finding. 26 tests, **100%**.
- `979a5d2` **`calibrate_store`** (`22867a2`). A store pass, zero model calls,
  writes only on a change. Scope item 4 answered: no migration, and `Finding`
  carries the allegation but not the query. 32 tests, **100%**.
- `de41689` **`praxis calibrate`** (`e98aff7`). One command, two modes; the
  question writes nothing. Leads with the refusals. 22 tests, **100%**.
- `6fa5777` **Eval extension** (`961d368`). The fourth quarter of the table, and
  the only one with **no answer key** — a calibration factor is not something a
  document can state. 15 tests, **100%**.
- `7a13513` **`ARCHITECTURE.md`**, `8aded79` **`BACKLOG.md`** (five deferrals),
  `f51ee1a` **the phase 7 report**.

---

## Phase 8 progress log

One line per component, appended as it landed. Branch
`feat/phase-8-fusion-layer`, cut from `main` at `8797d1f`.

- `db3eec0` **EST-0009 logged** — 3.6h active, 12.0h blocked, confidence 0.45,
  and **the first bias correction this project has ever applied**. Eight
  estimates refused at `n < 5`; `OUT-0008` took `agent-implementation` to
  exactly five, so refusing a ninth time would have made `MINIMUM_SAMPLE`
  decorative. The factor was recomputed by running `bias.summarise` over the
  real log rather than read off this file: **1.7999x over, n=5, band 1.35x to
  2.41x, confidence 0.3875**. A raw 6.5h built bottom-up by the same procedure
  the five fitted estimates used becomes **6.5 x 0.5556 = 3.6h**, band 2.7h to
  4.8h. `OUT-0008`'s "wrong lever" lesson is corrected rather than repeated:
  the whole adjustment is in the **quantity**, and confidence moves **down**
  0.50 → 0.45. The sub-population objection is filed as a *condition*, and
  splitting the work class is refused in writing — every sub-class would fall
  below the threshold, which is ADR 0024's override arriving through a side
  door.
- `57c9bdb` **`FusionBridge`** + **ADR 0026**. Walks the `estimated_as` edges
  Phase 4 already writes, resolves each estimate's `(owner, work_class)`, asks
  `BiasDetective.factor_for` once per group, and evaluates the same predicate
  twice — raw and calibrated. **A finding fires on a flip, not on a
  correction.** The bridge makes **no model call** and leaves ADR 0006's
  `reason` row for `NON_LLM_AGENTS`: the judgement is real and
  `AssumptionExtractor` already pays for it (ADR 0016), while ADR 0019 had
  already closed the other end. Fifth consecutive phase in which the
  arithmetic/judgement boundary moved a component to the arithmetic side, and
  the **first in which the estimate called it in advance**. 25 tests, **100%**.

**Two things the tests found rather than confirmed**, both worth carrying:

1. **`Repository.get` returns a retracted record, not `None`.** Retraction is a
   statement *about* a record rather than the disappearance of one, so a
   component testing only for `None` will price a withdrawn record as though it
   still stood. `list_all` excludes them by default and `get` does not — the
   asymmetry is easy to miss and every later Phase 8 component walks edges the
   same way. The store's foreign key is what makes retraction the *only*
   reachable form of "the target is gone", asserted as the control.
2. **`FusionVerdict.UNDECIDED` cannot currently be reached through the bridge.**
   `subject_of` accepts a predicate only when `constraints_of` yields exactly
   one name, and `constraints_of` yields names only for simple `name OP literal`
   comparisons — a division, a string comparison or a second identifier makes it
   yield *nothing*, which is `NO_SUBJECT` several steps earlier. The guard is
   kept, `moved` is public so it can be tested directly, and ADR 0026 assumption
   4 expires the day the predicate language grows.

- `0f493b0` **`CrossDocumentMatcher`** (`praxis/agents/crossdoc.py`). Phase 6's
  deferral, and **ADR 0015 was not spent to buy it**. The invariant is about one
  *call*, not one estimate: this runs the existing single-document matcher once
  per candidate document, so every listing is still one document's spans and
  `offering_of` still raises on anything else. Cost is bounded by a
  deterministic document selector and a budget of three documents per estimate;
  home is read first and the walk stops at the first resolution, so the common
  case still costs one call. The fixtures had held this case since Phase 4 — the
  ADR says 4 weeks, the status update says 7 — and a control test asserts
  `OutcomeMatcher` alone still cannot make the pairing. 16 tests, **100%**.
  `BACKLOG.md`'s row is marked done with the reasoning rather than deleted.

- `6a52559` **`CollateralAgent`** + **ADR 0027**. The reverse direction, and
  almost none of it is new: Phase 1 built the walk (`impacted_by` over
  `DEPENDENCY_LINK_TYPES`, whose own docstring calls itself *the fusion query*),
  ADR 0021 made the threshold arithmetic, and `severity_for` is imported from
  `praxis.monitor.breach` rather than reimplemented. So it makes **no model
  call** either, and `FindingKind.COLLATERAL_IMPACT` finally has something
  writing it. Using the existing walk is load-bearing rather than tidy — a
  hand-rolled traversal would have followed the two-hop route through the
  assumption and missed `justified_by` entirely, which is the special-case-per-hop
  the edge directions exist to prevent. 23 tests, **100%**.

  **Sixth consecutive component to move to the arithmetic side**, and ADR 0027
  stops calling that a run of good corrections. Six is evidence about
  `ARCHITECTURE.md`, and the reading it takes is that the judgement is real and
  is being pushed steadily *earlier* — into extraction, paid for once and written
  down as an edge. The honest cost of a fusion finding therefore includes the
  `reason`-tier call Phase 4 made. That also predicts where it stops:
  `ChallengerAgent` and `ReviewTriageAgent`, whose input is prose nobody has
  reduced to a record, should keep their routes.

- `b77b7d8` **`fuse_store`** (`praxis/agents/fusion_pass.py`) + **ADR 0028**.
  Both directions over a store, zero model calls, and a second pass over an
  unchanged store writes **nothing** — not the same thing again. **ADR 0028
  changed `ARCHITECTURE.md`**: step 5 had said the forward direction emits an
  `AssumptionBreach`, and it must not. A breach is a predicate false against
  facts a run was *given*; a fusion flip is a predicate false against a number
  **nobody has observed yet**. So forwards files `STALE_DECISION` against the
  assumption and opens with *"Nothing has been measured yet"*, backwards files
  `COLLATERAL_IMPACT` against the estimate. Both kinds, and
  `LinkType.COLLATERAL_OF`, have existed since Phase 1 with nothing writing
  them. 14 tests, **100%**.

  **A third finding for the carry-forward list**, and it is the store's, not
  this phase's: building every finding before writing any hands them all the
  same id, because `Repository.next_id` reads the highest ordinal *in the
  store*. That is exactly what `StoreAllocator`'s docstring describes and it was
  walked into anyway. Ids are now allocated at write time, so a pass over a
  settled store spends none. Any later component that builds a batch of records
  before storing them will hit this.

**Still to build in this phase**, in the order `EST-0009` prices them:
`CollateralAgent` (the reverse walk, priced as its own component and *not* as a
direction flag on `FusionBridge`), the cross-document half of `OutcomeMatcher`
deferred from Phase 6 (the riskiest item — ADR 0015's single-document offering
must not be spent to buy it; cut it to the backlog rather than spend it), the
store pass, the CLI, and `praxis/eval/fusion.py` plus its four edits.
`ReviewTriageAgent` is **not** in this phase — `BACKLOG.md` places its first
consumer in Phase 9.
