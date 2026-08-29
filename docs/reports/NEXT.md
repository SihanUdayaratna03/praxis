# Handover — start of Phase 6

Read this first, then `CLAUDE.md`. Written at the close of Phase 5 so the next
session can start working instead of re-deriving state.

---

## Where we stopped

Phase 5 is merged, tagged and green. Nothing is in flight. **Half A is closed.**

| | |
| --- | --- |
| `main` | `ccb01d0`, local and remote identical |
| Tag | `v0.5-phase-5` → `ccb01d0` (dereferenced through the API, not assumed) |
| CI | 5/5 green on [#12](https://github.com/SihanUdayaratna03/praxis/pull/12) |
| Open PRs | none |
| Remote branches | `main` only |
| Working tree | clean |
| Suite | **2179 passed**, coverage **98.68%** (gate 85%) |
| Schema | version 4 — Phase 5 needed no migration |

```bash
cd "C:\Users\sihan\OneDrive\Desktop\Praxis Agents"
git checkout main && git pull
uv sync --all-groups
uv run praxis doctor            # expect OK, including one offline model call
uv run pytest                   # expect 2179 passed

# the phase 5 pipeline, end to end and offline
uv run praxis init
uv run praxis corpus generate .praxis-tmp/corpus
uv run praxis ingest .praxis-tmp/corpus/documents
uv run praxis extract
uv run praxis formalize         # compile assumptions into predicates
uv run praxis monitor           # evaluate them; writes only what changed
uv run praxis contradictions    # blocking -> arithmetic -> a model
uv run praxis why "why not Postgres full-text search"
```

**Every one of those four will report zeros offline, and that is correct.** The
citation gate refuses almost every extraction against the mock (ADR 0016), so
one record reaches the store and the memory agents have nothing to work on.
`praxis why` exits 1 with `empty_answer` for the same reason — a refusal is the
cheap outcome and a wrong match is the expensive one. What the run verifies is
that the pipeline holds together end to end without credentials, not that the
numbers are good.

**Shipped in Phase 5:** the predicate language (`praxis/predicates/` — lexer,
parser, three-valued evaluator, interval arithmetic, expiry grammar, world
state); `AssumptionFormalizer` and its store pass; `AssumptionMonitor`,
`praxis/monitor/` and the breach finding; deterministic blocking plus
`ContradictionDetector` and its store pass; `ArchaeologistAgent`; the corpus
revision notes and the monitoring section at `FORMAT_VERSION` 2;
`praxis/eval/memory.py` and `praxis/eval/adrs.py`; four CLI commands. 638 new
tests. ADRs 0017–0020.

**Deliberately not shipped:** anything that writes an `Outcome` — that is Phase
6 and two zeros in the Phase 5 report are waiting on it.

The full account is in [`phase-5.md`](phase-5.md). **Read its metrics section
before writing `EST-0007`**, and its closing section before pricing anything.

---

## The result Phase 5 earned

**ADR 0001's first assumption is answered and it holds.** 80 hand-written
predicates across `docs/adr/`, **79 parse — 0.9875** against its own threshold
of 0.9, and all 80 expiry conditions parse. `praxis/eval/adrs.py` recomputes it
from the files each time rather than asserting it.

The one unreadable row is ADR 0015's third assumption (`outside [0.5, 2.0]`,
which is English). **It stays a finding** — a test names it by file, so widening
the grammar for one instance fails rather than quietly turning the measurement
into a feature request. Do not "fix" it.

---

## Two zeros in the Phase 5 table, and they mean different things

Anyone reading `phase-5.md`'s metrics needs both of these, because the table
alone cannot distinguish them.

1. **Formalization reads `0 of 0` because extraction stored nothing.** The
   citation gate refused 38 of 39 claims offline — Phase 4's known behaviour,
   ADR 0016 — so one record reached the store for the whole corpus and the
   memory agents made zero model calls. The formalizer's offline path *does*
   work: given an assumption it compiles `index_size_gb <= 50` correctly, and
   `tests/agents/test_formalization.py` covers it at 100%. **Upstream
   starvation, not a broken formalizer.**
2. **The monitor's store-derived binding reads zero because nothing writes an
   `Outcome` yet.** `measured_in` binds an identifier only where an assumption
   carries an `estimated_as` edge to an estimate an `Outcome` resolves.
   **Mechanism waiting, not mechanism failing** — and Phase 6 is what makes it
   move.

`aged_misreported_as_breached` is 0 and that one means what it says: zero by
construction, because only arithmetic can breach (ADR 0019).

---

## Phase 5's own numbers

| | |
| --- | --- |
| `EST-0006` | 6.5h active, 24.0h blocked |
| `OUT-0006` | **≈4.6h active**, ≈17.3h blocked, scored `close` |
| Error | **1.40× over**, on the measure the estimate was stated in |

Active is four session windows bounded from both ends by commit timestamps.
Blocked is elapsed wall clock between them and measures when the author slept.
**Compare active against active.**

`agent-implementation` is now at **n = 3, and all three are over-estimates** —
1.31×, 2.18×, 1.40×. It is the only work class in this corpus with a direction
rather than a scatter.

**No bias correction has been applied, six times running.** `BiasDetective`
refuses below n = 5 and so does its author. That judgement now looks better
rather than worse: the three ratios span 1.31× to 2.18×, so a factor fitted to
the first two points would have over-corrected this one. Two more outcomes in
this class and the Phase 12 demo has something real to say.

---

## Before writing `EST-0007`

Three things `OUT-0006` recorded, in the order they will cost you.

1. **Price the CLI.** Phase 4 shipped `cli_eval.py` unpriced; Phase 5 shipped
   `cli_monitor.py` and its 18 tests unpriced. Two phases running is a pattern,
   not an oversight. `EST-0007` should carry a line item for it.
2. **A component described as "an extension" may have a subject of its own.**
   The eval work was priced as four line items against four existing modules and
   became five — `praxis/eval/memory.py` exists because the record-to-item join
   needed somewhere to live that was not `harness.py`. Same shape as
   `OUT-0004`'s corpus generator, one size smaller.
3. **Price each agent's refusal vocabulary inside the agent.** This is the
   correction `OUT-0005` bought and it kept working. An agent's happy path is
   one function; the code that says no is the rest of the file.

The six-line-item pricing of the predicate DSL worked — the parser landed near
its line-item reading rather than at the 2× a one-line-item pricing produced in
Phase 3. Keep pricing parsers and harnesses by component.

---

## Phase 6 — Half B

`ARCHITECTURE.md`'s Half B column: `EstimateExtractor`, `WorkClassifier`,
`OutcomeMatcher`, `BiasDetective` (no LLM), `CalibratorAgent`, `ScoringAgent`
(no LLM).

Four things worth knowing before planning it.

**`BiasDetective` and `ScoringAgent` are deterministic code and `NON_LLM_AGENTS`
enforces it.** Invariant 3, and `praxis doctor` fails if either acquires a model
route. Brier, log score, MAPE, bias factors and intervals are arithmetic.

**`BiasDetective`'s whole point is refusing.** It answers with a factor, an `n`
and an interval, and refuses below n = 5. Run against this project's own
history it will decline on every class — n = 3 in one, n = 1 in three others.
That is correct behaviour and the Phase 6 report should say so plainly before
anyone reads it as a failure.

**`Outcome` is what unlocks the two zeros above.** Once something writes one,
`measured_in` binds a quantity, an assumption can be breached by a measured
miss, and the fusion chain the product exists for runs end to end for the first
time. Check those numbers rather than assuming they moved.

**The corpus already contains three outcome items** and `ItemKind.OUTCOME` is
deliberately excluded from `GRADED_KINDS` in `praxis/eval/harness.py` — listing
it with a permanent zero would have read as a regression. Phase 6 adds it there,
and `praxis/eval/memory.py` is where its grading joins the rest.

**A schema migration is likely.** Phase 5 needed none; Half B may. Check
`praxis/domain/records.py` and `001_core.sql` before assuming either way, and
say which in the estimate's conditions — `EST-0006` did, and that made the
condition checkable afterwards.

---

## Rules that have not changed

- **Log `EST-0007` before writing any Phase 6 file**, on the phase branch. A
  phase with no prediction is a hole in the Phase 12 demo.
- **`main` only moves through a reviewed, CI-green merge commit.** Never squash.
- **Long text goes to a file**, never inline: `--body-file`, `-F`. The shell here
  has a ~965-byte parse limit and PowerShell 5.1 mangles embedded quotes.
- **Commit after every component**, then push, then append to this file. Never
  batch.
- Progress explanations to the product owner are in **Sinhala**; everything in
  the repository is in English.

One thing to carry forward: `praxis/agents/formalization.py` shipped at the end
of a session with ruff and mypy green and **no tests**, under a stop signal. No
other module here has ever shipped that way, the handover named it as the first
thing to close, and the next session closed it at 100% before starting anything
else. That worked — but the cheaper lesson is not to let a component reach a
commit without its tests in the first place.

---

## Phase 6 progress log

One line per component, appended as it lands. Written so a session that dies
mid-phase can be resumed from the last line rather than from a diff.

- `3801800` **EST-0007 logged** — 5.0h active, 18.0h blocked, uncorrected for the
  seventh time (`agent-implementation` at n = 3, threshold 5). Branch
  `feat/phase-6-half-b-agents` cut from `main` at `931055c`, pushed.
