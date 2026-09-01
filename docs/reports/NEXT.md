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

- `d3c2e54` **`praxis fuse`** (`praxis/cli_fuse.py`). The demo surface, and
  against a seeded store it prints the sentence the project exists to produce:
  *"A-0002 assumed migration_weeks = 4; migration work is 1.8000x under, n=5,
  confidence=0.5000 implies 7.2000 — flipped"*. A projection is printed under
  *"nothing has been measured yet"* and a measurement under *"this already
  happened"* — ADR 0028 in the schema, and this so a reader cannot misread one
  as the other. Leads with the refusals, each explained in a reader's words
  rather than the enum's. `--dry-run` computes through the same two agents the
  pass uses, so it cannot disagree with what a real run would write. 13 tests,
  **100%**.

  **The empty-store message was rewritten after running the real pipeline end
  to end**, which is the reason to run it. The first version said "run `praxis
  extract` first" — wrong in exactly the case a judge will hit: extract *has*
  run, the mock's citations were refused, and few assumptions survived to carry
  an edge. It now names the citation gate and says this is the gate working.

- `6b24c98` **`praxis/eval/fusion.py`** — the fifth quarter of the metrics
  table, as a module plus four edits and not four edits alone (OUT-0006's
  lesson, third application). Most of it has **no answer key**, and for a
  sharper reason than Phase 7's: a corpus *can* state that an assumption is an
  estimate in disguise, and does, but no corpus can state whether a *calibrated*
  number violates a predicate. So two booleans carry the claim —
  `refusals_hold` (no edge reported a factor for a group `BiasDetective`
  refuses) and `flips_hold` (every finding came from a predicate that really
  moved). Both tested in **both** directions. `cross_document` is a count and
  never a rate, because `BACKLOG.md` refuses to plant the ground truth that
  would score it. 12 tests, **100%**.

  **Two defects found by running the harness rather than reading it**: the
  report had two sections headed `## Fusion`, and the shared verdict renderer
  said "no groups in the store" under a heading about edges.

**Still to build in this phase**, in the order `EST-0009` prices them:
`CollateralAgent` (the reverse walk, priced as its own component and *not* as a
direction flag on `FusionBridge`), the cross-document half of `OutcomeMatcher`
deferred from Phase 6 (the riskiest item — ADR 0015's single-document offering
must not be spent to buy it; cut it to the backlog rather than spend it), the
store pass, the CLI, and `praxis/eval/fusion.py` plus its four edits.
`ReviewTriageAgent` is **not** in this phase — `BACKLOG.md` places its first
consumer in Phase 9.

---

## Phase 9 progress log

One line per component, appended as it landed. Branch
`feat/phase-9-adversarial-governance`, cut from `main` at `a79dc0e`
(`v0.8-phase-8` dereferenced through `git ls-remote`, not assumed).

**Three untracked paths carried forward untouched.** `.codex/`, `AGENTS.md` and
`docs/assets/` were flagged at the Phase 8 handover as deliberately left alone,
and they still are: still untracked, not committed, not moved, not deleted, and
the ruff `T201` exemption was **not** widened for them. They cannot affect CI —
`.github/workflows/ci.yml` runs `actions/checkout`, which materialises committed
files only, so nothing outside the index is visible to any job. Confirmed rather
than assumed.
- **`ChallengerAgent`** + **ADR 0030** (`praxis/agents/challenger.py`). Argues
  against a finding and records what survived, onto `Finding.challenge` and
  `Finding.verdict` — fields Phase 1 built for it, so **no new storage**. The
  first component in seven to make a model call, and ADR 0027 called it in
  advance: its input is a `prosecution` in prose that nobody reduced to a
  record. An unconfident verdict is recorded as **no verdict**, and an empty
  rebuttal forfeits the verdict with it, because `Finding`'s own validator
  refuses a verdict with no challenge behind it. 29 tests, **100%**.
- **`CuratorAgent`** + **ADR 0031** (`praxis/agents/curator.py`). Merging is
  `LinkType.SUPERSEDES`, retiring is `Repository.retract` — both Phase 1's, both
  already documented as doing exactly this, so **no migration and no new
  `AssumptionStatus` member**. **No model call**: what it decides is a
  `CONTRADICTS` edge Phase 5 paid a `reason` call for, two parsed predicates,
  two timestamps and an audit trail. Eighth consecutive component on the
  arithmetic side. *Never fires* is defined precisely — `UNVERIFIED`, no
  `AssumptionMonitor` event in the trail, and `IDLE_DAYS` since the **first**
  version, read off `audit_for` because `revise` overwrites `created_at`. The
  refusal that matters: an idle assumption a live decision rests on is a finding
  for a person, not dead weight. 38 tests, **100%**.
- **`AbstentionGate`** + **ADR 0032** (`praxis/agents/abstention.py`). Sits
  immediately downstream of the challenger and asks the other question: the
  challenger tests whether the *argument* holds, this tests whether the
  *evidence* does. Four checks — never challenged, below the confidence floor, a
  quoting kind that cites nothing, a withdrawn subject — and **all four run**, so
  a person is told every defect rather than sent back once per fix. **No model
  call and nothing stored**: a disposition is recomputed, the same argument
  `praxis.agents.calibration` makes about a factor, and it is what keeps this
  phase migration-free on the third axis. Ninth consecutive component on the
  arithmetic side. 25 tests, **100%**.
- **`govern_store`** (`praxis/agents/governance.py`). All three agents over a
  store, in an order that cannot move: the gate reads the verdict the challenger
  writes, and it re-reads the store *after* curation so a finding about an
  assumption this very pass retracted is not concluded on. **A second pass over
  an unchanged store writes nothing and costs no model call**, because the
  challenger refuses to re-argue a decided finding. **A merge does not retract
  the loser** — the `supersedes` edge is the whole claim, and withdrawing the
  earlier record would take half of every revision out of `list_all`. 21 tests,
  **100%**.
- **`praxis govern`** (`praxis/cli_govern.py`). The demo surface, and the only
  command whose headline output is a count of refusals. Leads with the
  abstentions, grouped by which rule fired and each explained in words a reader
  can act on. **The concede rate carries the sentence that saves it**: against
  the mock it is a property of `_TRUE_BIAS = 0.7`, not of any reasoning, and
  that is printed on the line where the number appears. `--dry-run` computes
  through the same agents and makes no call. 21 tests, **100%**.

  **Three defects found by running it against the real pipeline rather than
  reading it**, which is the reason to run it. The empty store printed "nothing
  changed" when it had never had anything to change; the dry run asserted "A-0003
  has been retracted" about a record still standing, because it was feeding the
  gate retirements it had only proposed; and the whole argument section vanished
  on a dry run, so a reader was left to infer from a missing heading that no
  verdict had been reached.
- **The corpus gains one edge type** (`praxis/corpus/templates.py`). Checked
  before assuming a gap, the discipline Phases 5 and 6 both applied: the offline
  pipeline was run end to end first, and it extracts **1 decision, 0 assumptions,
  0 findings** from 16 documents, because ADR 0016's citation gate refuses 38 of
  40 claims. So every Phase 9 corpus number is a correct zero. What the corpus
  *does* hold is 4 revision notes, each saying in words that an earlier
  assumption no longer stands — a stated `supersedes`, planted beside the
  `contradicts` already there. Findings and challenges are **not** planted: a
  finding is produced rather than stated, and planting one would grade the
  harness against itself.
- **`praxis/eval/governance.py`** — the sixth quarter of the metrics table, as a
  module plus edits to `harness`, `report` and `matching` (OUT-0006's lesson,
  fourth application). Three rates, **one** answer key and three booleans. The
  answer key is `merge_recall` against the `supersedes` edges the corpus now
  plants; the three rates have none. `mock_provider` travels *inside* the score,
  so the concede rate can never be printed without the sentence that qualifies
  it. Every boolean tested in **both** directions, including the ones only a
  monkeypatched gate can falsify — a consistency claim that can only be true is
  not a claim. 23 tests, **100%**.

  **The mock's concede rate, measured rather than guessed:** over 40 synthetic
  findings the mock answered about **6**, decided 5 and conceded 1 —
  **0.2000**, inside the 5%–60% band, and *not evidence about the challenger*.
  The dominant fact is the other one: 34 of 40 went **unargued**, because
  `MockProvider` synthesises about two array entries per call whatever the batch
  holds. The agent reports them as `unjudged` rather than as concessions nobody
  made, which is the refusal working.
- `tests/ingest/test_adapters.py` — **a pre-existing latent flake `hypothesis`
  found in CI on this PR**, not a Phase 9 defect and not a product bug.
  `TEXT_BODIES` filtered on `str.strip() != ""` as a proxy for "will survive
  normalisation", and the two disagree on exactly one character: a body of
  nothing but `\ufeff` is non-empty to `strip()` (a BOM is not whitespace) and
  empty after normalisation (which removes it), so the adapter refuses it —
  correctly, since no span could ever address it. A property about *re-reading*
  was failing on an input that never gets read once. The filter now asks
  normalisation directly instead of guessing.

---

## Where Phase 9 stopped

Phase 9 is merged, tagged and green. Nothing is in flight.

| | |
| --- | --- |
| `main` | `2c9f56b`, local and remote identical |
| Tag | `v0.9-phase-9` → `2c9f56b`, dereferenced through `git ls-remote`, not assumed |
| CI | 5/5 green on [#19](https://github.com/SihanUdayaratna03/praxis/pull/19) |
| Open PRs | none |
| Working tree | clean apart from four untracked paths, below |
| Suite | **2948 passed**, coverage **98.97%** (gate 85%) |
| Schema | version 4 — no migration, fourth phase running |

**Untracked and deliberately left alone:** `.codex/`, `AGENTS.md`,
`docs/assets/`, and now `docs/design/` (which appeared during the phase and was
treated the same way). None is committed, moved or deleted; the ruff `T201`
exemption was not widened for any of them; and none can affect CI, because
`actions/checkout` materialises committed files only.

## What `OUT-0010` says, and the mistake it invites

**2.0h active against 3.6h predicted — 1.8000x over.** That is, to four figures,
the very factor that was applied to the estimate. The raw 6.5h would have been
3.25x over, so the correction removed a little over half the error rather than
all of it.

**This is the first row where a corrected estimate missed in the same direction
by the same magnitude as the uncorrected ones, and it invites correcting twice.
Do not.** `EST-0009` established the rule — correct once, in the bottom-up
procedure — and one row is not a trend. The prequential walk is what answers the
question, and `OUT-0010` is what made it answerable: `agent-implementation` is
now at **n = 7**, so `ScoringAgent` grades **two** rows for the first time. It
says the correction **helped on both** and worsened neither.

If corrected figures keep landing near 1.8x over, the honest response is that
the **factor** is too small, not that the procedure needs two applications — and
the factor moves on its own as the sample grows. It already has:

| | at `n = 6` | at `n = 7` |
| --- | --- | --- |
| Factor | 0.5527 | **0.5084** |
| Read as | 1.8093x over | **1.9670x over** |
| Band | 1.39x – 2.35x | **1.42x – 2.72x** |
| Confidence | 0.4329 | **0.4404** |

**Recompute it rather than copy this table.**

## The new observation `EST-0011` should act on

The procedure was wrong in **both directions at once**. It over-estimated the
hours and *under*-estimated the volume: 6 modules at 2249 production lines
against a predicted ~1600, about 40% low. So hours-per-line is what the fitted
factor is really correcting, and the line-count line item is not carrying its
weight. Price components by **refusal count and integration points** rather than
by predicted lines, and see whether that procedure needs a smaller correction.

## Carry-forward, in the order it will cost you

1. **Run the CLI against the real pipeline, not just its tests.** Third phase
   running that this finds defects reading does not — two in Phase 8, three
   here.
2. **Price each component's refusal vocabulary inside the component.** Fifth
   phase running, still the most reliable correction in this record.
3. **Price the CLI.** Fifth phase running.
4. **Price the arithmetic/judgement boundary as its own line item.** Sixth phase
   running, and the second in which the estimate called the direction in
   advance. Phase 9 was the first in which it called a component moving *back*
   to the model side.
5. **Blocked time is not an engineering prediction.** Seven attempts, seven
   misses. Log it because the record has the field; compare active against
   active.

## Rules that have not changed

- **Log `EST-0011` before writing any Phase 10 file**, on the phase branch.
- **`main` only moves through a reviewed, CI-green merge commit.** Never squash.
- **Long text goes to a file**, never inline: `--body-file`, `-F`.
- **Commit after every component**, then push, then append here. Never batch.
- **Commit messages are short from now on** — ADR 0029. Phases 0–8 are never
  rewritten.
- Progress explanations to the product owner are in **Sinhala**; everything in
  the repository is in English.

---

## Phase 10 progress log

Branch `feat/phase-10-eval-harness`, cut from `main` at `b43b21e`. One line per
component, appended after it was committed and pushed.

**The four untracked paths were checked again before the branch was cut and
left alone again.** `.codex/`, `AGENTS.md`, `docs/assets/` and `docs/design/`
are untracked and not gitignored either — `git check-ignore` exits 1 on all
four. None can reach CI: `.github/workflows/ci.yml` starts every one of its
three jobs with `actions/checkout@v7.0.1`, which materialises committed files
only. Nothing was committed, moved, renamed or deleted, and no ruff exemption
was widened for any of them. This is the second phase running that they have
been confirmed and passed over, and the answer has not changed.

- **`EST-0011` logged before any Phase 10 file was written.** 3.1h active
  against a 6.0h raw bottom-up, corrected once by the fitted factor
  (`agent-implementation`, n=7, 1.9670x over, factor 0.5084 — recomputed with
  `BiasDetective` over `docs/dogfood/` rather than copied from the Phase 9
  table, and it agrees). Blocked 3.9h, the geometric mean of all ten actuals
  and not an engineering prediction. **Priced by refusal vocabulary (16 paths)
  and integration points (20), with no line count predicted at all** — which is
  what `OUT-0010` asked for, having been wrong on hours and volume in opposite
  directions at once. The class decision is stated rather than inherited: no
  new agents in this phase, but `EST-0004` and `EST-0008` are already in
  `agent-implementation` on the same grounds, and splitting the class at n=0
  would throw away the project's only calibration signal to make a taxonomic
  point.

- **ADR 0033 — the style change reaches comments and docstrings.** Second stage
  of ADR 0029's decision, not a new one. Phase 10 code onward gets one or two
  plain lines; depth goes to an ADR and the code points at it. Phases 0–9 are
  never restyled, for the reason ADR 0007 gives about history. `CLAUDE.md`'s
  Style section says so and names the ADR. All three of 0033's predicates parse
  against the Phase 5 grammar, checked with `read_adr_predicates` rather than
  assumed — the corpus is now 132 hand-written predicates at a 0.9924 parse
  rate.

- **Eight more corpus topics, taking it to sixteen.** Arithmetic, not taste:
  eight topics across four templates is thirty-two document shapes, so the
  sixty-document corpus the phase needs was repeating every shape twice and the
  extra documents measured nothing. Sixteen gives sixty-four, and `--documents
  60 --revisions 8` now writes 68 documents with **no repeated topic-and-template
  pair at all**, checked by listing them rather than assumed. Eight
  `contradicts` and eight `supersedes` pairs planted, up from four each. New
  work classes (`frontend`, `security`, `ml`) and three owners who had only been
  meeting attendees before, so calibration has more than one group to be silent
  about. `tests/corpus/test_topics.py` is new and is where the risk actually
  lived — it parametrises over every topic and checks that the reversal
  predicate's satisfying witness names the same quantity the predicate's
  violating witness does, which is what makes the planted contradiction settleable
  by interval arithmetic instead of by a model.

- **The corpus gained its controls, and the default corpus is now 82 documents.**
  `praxis/corpus/controls.py` is new and holds three passes that run after the
  revisions: six **clean controls** (an on-call handover, a deploy log) with
  *zero* ground-truth items, so anything extracted from one is a false positive
  outright; four **adversarial memos** where all five extractable-looking
  sentences are labelled negatives — a conditional, a deferral, another team's
  decision, a refused proposal and a question; and four **orphan notes**, each
  stating one real assumption that no `assumes` edge anywhere points at, which
  is the retirement candidate `CuratorAgent` has never had a denominator for.
  `DEFAULT_DOCUMENTS` 12 → 60, `DEFAULT_REVISIONS` 4 → 8, and the counts are
  grouped into a `Controls` object so `generate_corpus` keeps a signature a
  person can read. `GENERATOR_VERSION` 3 → 4, because the generated text
  changed. Two of the sixteen predicted refusal paths were real and are now
  closed: a zero-item document is a shape `_document_problems` had never seen,
  and a negative control count is refused rather than clamped. The other
  prediction held too — `GroundTruthItem`'s validator does refuse fields on a
  distractor, so the adversarial memo plants none.

- **The segmenter can be told to run on the floor alone.** `floor_only=True`
  skips the model for every window and takes one span per block, which is the
  ablation's baseline rung. `BACKLOG.md` predicted this would be "a
  configuration change rather than a rewrite when the harness exists" and it
  was: one flag, one early return, and `IngestionPipeline` already accepted an
  injected segmenter, so it cost none of the integration points priced for it.
  The refusal path predicted as number 6 was real and is closed — the floor
  makes **zero** calls rather than one it throws away, because the ablation
  reports cost per document and a baseline that spent the scan tier would
  misprice every rung above it.

- **ADR 0034 — the eval harness runs a coherently-citing mock.** The blocker
  the phase had to answer: offline the mock draws its cited ordinal and its
  quotation independently, so the gate refuses 28 of 39 claims and every number
  downstream is a correct zero. An ablation table cannot show a layer adding
  anything to zero. The mode is opt-in, the default draw is untouched, and the
  citation gate is *not* disabled — invariant 6 has no convenience exception, so
  the gate's own contribution is computed counterfactually from the refusal
  record instead. The ADR answers `BACKLOG.md`'s Phase 9 objection to
  special-casing the mock rather than ignoring it, and three Phase 10 backlog
  entries are added, including the honest alternative (recorded fixtures) and
  why invariant 1 forbids it.

- **The mock can draw its ordinal and its quotation together, and `praxis eval`
  asks it to.** ADR 0034 built. `passages_in` indexes a prompt's `[N]` listing,
  `_Answerer` pins one passage per object before filling any field — so an
  ordinal and a quotation in the same claim agree however the schema orders them
  — and restores the outer pin afterwards, because an extraction answer is a
  list of claims each citing its own passage. **The measured effect is the
  phase's turning point: citation integrity 0.2821 → 0.9075**, fabrications
  0.3077 → 0.0000, and the pipeline now runs end to end offline for the first
  time — 44 assumptions stored where there were 0, 18 predicates parsed, 73
  estimates classified, 13 resolved, 28 fusion edges priced, 9 findings argued.
  ADR 0034 assumption 2 (`>= 0.8`) holds on its first measurement. All three
  refusal paths predicted for this component were real: a passage with nothing
  quotable is left out of the index rather than pinned, a prompt with no listing
  falls back to the default draw, and the flag off is asserted byte-identical to
  the Phase 2–9 answer.
- **One defect the coherent mock exposed, fixed in the next commit rather than
  hidden here:** `fusion_recall` divides every `estimated_as` edge in the store
  by the number the corpus labels, so now that spurious edges exist it reports
  **9.3333**. A ratio over 1 is not a recall. It was latent for six phases
  because nothing offline ever wrote more edges than the key had.

- **Fusion recall counts only the labelled pairs an edge really reached.** The
  defect the previous commit exposed. The numerator was every `estimated_as`
  edge in the store and the denominator was the number the corpus labels, so
  the two were over different sets — a ratio that can exceed 1, and did, at
  **9.3333**. It joins through the assumption and estimate pairings now, which
  is the same join `praxis.eval.memory` already makes for the same reason: the
  store's ids and the key's ids are allocated independently and nothing relates
  them but the passage both cite. The total edges written are still reported,
  beside the recall rather than inside it, because an edge the key does not
  label is unjudgeable rather than wrong. Recall on the probe corpus is now
  **0 of 3** against 28 edges written, which is the honest reading: the mock's
  citations are coherent but the passage is still drawn at random, so no edge
  joins the right assumption to the right estimate.

- **Calibration MAE improvement is a reading of the backtest, not new
  arithmetic.** The brief names it; `Backtest` already carried `raw_error` and
  `corrected_error` over the same scored rows, so `mae_improvement` reports the
  gap both ways — absolute and as a share of the error there was to remove — and
  keeps `measured` beside them. `scored == 0` has to stay distinguishable from a
  correction that changed nothing, because offline no group reaches
  `MINIMUM_SAMPLE` and both would print zero. The improvement is signed on
  purpose: a calibrator that made estimates worse is the failure the metric
  exists to catch.

- **Abstention precision is `abstentions_hold` as a number.** The brief names
  it and the gate's boolean already asserted it: of the findings routed to a
  person, how many really fail at least one rule. The boolean says whether the
  gate contradicted itself; the rate says by how much, which is what a table
  rung needs. Nothing withheld reports **no measurement** rather than zero, the
  same convention `concede_rate` uses for nothing decided.

- **`evaluate` takes a `Stages`, so a rung is a configuration rather than a
  second harness.** Six flags — the floor segmenter and the five passes above
  extraction — each gating one call in the function that already ordered them.
  All on is asserted byte-identical to what Phases 4–9 ran, because a default
  that drifted would have moved every number those phases reported. `Stages`
  lives in its own module so `harness` and `ablation` can share it without
  importing each other. The floor rung spends nothing on the segmenter, which
  is what keeps the cost column comparable between rungs.

- **ADR 0035 — the ablation ladder is cumulative.** Rung 1 is the floor with
  extraction alone, each rung adds one component, and the top rung is the
  ordinary graded run. Leave-one-out was rejected for a real reason rather than
  a stylistic one: the stages are ordered by dependency — the monitor needs
  compiled predicates, fusion needs calibrated factors, governance needs
  findings — so removing a middle component prints a difference about the
  missing dependency rather than about the component. A store per rung, because
  a rung reading what an earlier rung wrote would grade a pipeline nobody ran.
  The citation gate is *not* a rung: invariant 6 has no off switch, and ADR 0034
  already settled that its contribution is counterfactual.

- **ADR predicate parse rate, re-checked after 0033, 0034 and 0035 landed:
  139 of 140, `0.9929`.** ADR 0001's first assumption asks for `>= 0.9` and it
  holds. All 140 expiry conditions parse. The single unreadable row is still
  ADR 0015's `mis_attribution_rate / fabrication_rate outside [0.5, 2.0]`,
  which is prose in a predicate column rather than a grammar gap.

- **The ladder is built, and writing its tests found two things.** `ablate`
  runs each rung in a store of its own and reduces the graded result to a row.
  Two defects the tests caught rather than the table:

  **`EvalResult` had no total call count.** `run.calls` is the extraction run's
  alone, so the floor rung and the learned segmenter reported the *same* 52
  calls and ADR 0035 assumption 2 looked false. `evaluate` already summed every
  stage's calls for its log line and then threw the number away; it is on the
  result now, and the floor rung really does call fewer times than the rung
  above it.

  **Abstention precision is 1.0000 on every rung below governance, and that is
  correct.** The gate is arithmetic (ADR 0032) so it is recomputed on any store,
  and below the governance rung nothing has argued the findings — every one
  fails `never_challenged`, so every abstention really does fail a rule. The
  precision is trivially perfect and says nothing. `emitted` is the column that
  distinguishes the two, and the row carries it for that reason. The test that
  first asserted a precision of zero there was asserting the wrong thing.

- **The ladder renders as markdown and as JSON, and `_row` became
  `table_row`.** Rendering lives apart from arithmetic, the same split
  `praxis.eval.report` makes, in a module of its own only because that file is
  already 800 lines. The one thing shared with it is the markdown row helper,
  made public rather than copied so the two tables cannot drift into different
  shapes. `deltas` is the reading the ladder exists for: what each rung moved
  precision and recall by against the rung below it, signed, so a component that
  made things worse shows as a negative rather than as a small number.

- **`praxis eval --ablate` runs the ladder.** One flag on the command that
  already existed, rather than a second command: the corpus argument, the
  ground-truth check, `--json`, `--markdown` and the provenance line are all the
  same, and only what is run and what is rendered differ. `--keep` is refused
  beside it with a sentence saying why — seven rungs mean seven stores, so
  "keep the scratch store" has no answer, and the remedy printed is to run one
  rung with `--keep` instead. The flag is opt-in because the ladder costs seven
  runs of the corpus, which offline is time and against a real provider is
  seven times the money.

- **Two backlog entries, one of which is about the estimate itself.** ADR 0035
  promised the first: a rung per agent inside the memory pass is refused for the
  same reason leave-one-out was, and getting it needs a ladder that is not a
  prefix of the pipeline. The second is that `EST-0011` priced **ten** rungs and
  seven shipped, because there are seven stage boundaries in `evaluate` and the
  missing three could only come from splitting the memory pass. Recorded in
  `BACKLOG.md` rather than rounded away — the estimate is dogfood data, and a
  subject edited to match its outcome is the one thing that would make the
  Phase 12 demo worthless.

- **The first real ladder run showed three rungs as identical rows, so two
  columns were added.** Calibration, fusion and governance write *findings*,
  not claims, so extraction precision is blind to all three and the table said
  nothing about them. The JSON had the difference all along — abstentions ran
  34 → 35 → 56 across those rungs — but the markdown could not show it.
  `Findings` and `Flips` are columns now. This is the ablation's own failure
  mode caught by running it: a table whose rows are identical is a statement
  about the columns, not about the pipeline.

- **ARCHITECTURE.md records the ladder.** Three modules added to the evaluation
  section and to the tree: `stages`, `ablation`, `ablation_report`. The
  paragraph says the two things a reader needs that the code does not say — that
  three rungs write findings rather than claims, which is why a findings column
  exists, and that the citation gate is not a rung because invariant 6 has no
  off switch.

- **The ladder's first run breached an assumption from Phase 3, which is the
  point of having built it.** ADR 0011 assumption 1 says block grouping beats
  the paragraph floor by `segmenter_f1 - paragraph_floor_f1 >= 0.05`, and its
  expiry is `when(phases_completed >= 10)` — this phase. The floor and the
  learned segmenter produce **byte-identical** counts: 38 true positives, 408
  false positives, 124 false negatives, F1 **0.1250** both. The difference is
  **0.0000**, and `praxis.predicates` evaluates the predicate to `Truth.FALSE`
  — checked through the shipped evaluator rather than by eye. The segmenter's
  only measured effect is **82 extra model calls**.

  **It is not being recorded as false about the world.** Offline the segmenter's
  grouping is synthesised by the mock rather than reasoned, so this is evidence
  about `praxis.llm.synthesis`. What it does close is the excuse: the number was
  unmeasurable before this phase and is measurable now, and it is not in the
  assumption's favour. In `BACKLOG.md` for re-measurement on the first live run.

- **`docs/reports/phase-10.md` is written, and it leads with the breach rather
  than the table.** The ablation is the deliverable; the assumption it broke is
  the result. The report also states the three deviations plainly — seven rungs
  against the ten `EST-0011` priced, a flag rather than a subcommand, and the
  citation gate not being a rung — and records that Half B is an F1 *regression*
  (0.1250 → 0.1118) rather than describing the recall rise alone.
