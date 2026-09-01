# Phase 11 — dashboard, demo and landing page

> The phase that makes Phases 1–10 visible to someone who has never read a line
> of the code. Its most useful output is not the dashboard: it is that the
> dashboard, fed this project's own history, shows a decision this project made
> and a measurement that broke it.

## What shipped

| | |
| --- | --- |
| Branch | `feat/phase-11-dashboard`, cut from `main` at `8be20f3` |
| Sub-branches | 7, each merged `--no-ff`: adr, reads, web-shell, landing, demo, dash-shell, provenance, dash-views |
| New modules | `praxis/web/` (5), `praxis/demo/` (2), `praxis/store/dashboard.py`, `drilldown.py`, `praxis/cli_serve.py`, `cli_demo.py` |
| New commands | `praxis serve`, `praxis demo seed` |
| ADRs | 0036, 0037 |
| Schema | version 4 — **no migration**, for the sixth phase running |
| Dependencies | 4 new to the lock: `fastapi`, `starlette`, `uvicorn`, `click` |
| ADR predicates | 148 of 149 parse, **0.9933** (was 0.9929) |
| Suite | coverage **98.53%** (gate 85%); every new module at 100% |

## A framework was asked for and declined

The product owner asked for React. It is not being used, and the reason is
recorded here rather than left as a silent omission.

**ADR 0036 chose no build step, no framework, no CDN, and it chose that for a
reason that React does not survive.** Invariant 1 says Praxis runs with no
credentials and no network. A CDN `<script src="https://…">` makes the
dashboard render blank on a machine with no network — for a product whose first
trust badge reads *offline-first*, that is not an irony, it is a defect. The
alternative, a local build, puts `npm` and `node_modules` into a repository
whose entire toolchain is `uv` and whose CI installs nothing else: a second
package manager, a second lockfile, a second supply chain, forever. Every phase
from 0 onward has refused that kind of dependency, including at the LLM layer,
where the vendor SDK is optional and quarantined behind one named module.

**The request was really about visual quality, and a framework is not where
that comes from.** React would not have drawn one pixel differently. What
produced the quality here is the discipline the phase priced for: every view
opened in a real browser and compared against `docs/assets/` and
`docs/design/`, then corrected. That loop caught the landing page's hero width
and its three-line headline where the asset wraps to two; it caught sparklines
that were a straight line dressed as data; it caught a drill-down whose
Findings panel said *nothing alleged* while the table beside it showed a
breach; and it caught the header and rail footer staying blank on any deep
link. None of those are framework problems and none would have been found by a
test.

The depth in the reference — the blue-glass layering, the lit edges — is
layered `box-shadow` with an inset highlight, CSS custom properties, and inline
SVG. It is about 1,400 lines of hand-written CSS and JS, it has zero external
requests, and `tests/web/test_static.py` enforces that.

## The demo dataset is this repository

`praxis demo seed` writes **603 records** read out of files already committed
here: 36 ADRs become decisions carrying their **149** assumption rows,
`docs/dogfood/` becomes 12 estimates and 12 outcomes, and 149 `assumes` edges
join them.

**Phase 10's 68-document corpus was checked first and does not serve.** It
grades extraction, and offline it populates almost nothing: the citation gate
refuses 38 of 40 claims (ADR 0016) so one estimate reaches the store, no
calibration group could reach `MINIMUM_SAMPLE` even fully populated, and fusion
recall is 0 of 3. A dashboard built on it renders correct zeros.

**Nothing is invented, and a test proves it.** Every span is replayed against
its document's bytes and asserted equal — all 208 of them, invariant 6. Every
decision's `chosen` is asserted to be the span text; every predicate is
asserted to appear in the table row it cites; `3.1` is asserted to still be
`3.1`, invariant 4. The one field with no source is `confidence`, which
`Decision` and `Assumption` require and an ADR does not carry: it is a single
named constant with the reason written beside it, rather than a different
invented number per record.

`docs/dogfood/facts.json` carries eighteen measurements this project has
actually published, so the monitor has something real to evaluate against.

## The finding: a real decision, broken by a real measurement

149 predicates evaluated. **15 hold, 17 have expired, 116 are unverified
because nothing has measured them, and one is breached.**

> The predicate `segmenter_f1 - paragraph_floor_f1 >= 0.05` evaluated false
> against the facts this run was given, so the assumption "Block grouping
> extracts better than the deterministic paragraph floor" no longer holds.
> Resting on it: D-0011.

That is ADR 0011 assumption 1, the one Phase 10's ablation ladder measured at a
difference of **0.0000**. It is the payoff moment the phase exists to render,
and the dashboard draws it as the design does: four linked cards — decision,
assumption, evidence, verdict — under an amber *Re-examine this decision*
callout, with the finding's own prosecution printed verbatim underneath. Not a
table row.

**116 unverified is the honest majority and the dashboard shows it as such.**
Most of this project's assumptions have never been measured. A panel that hid
that would be the more flattering lie.

## Calibration speaks, and refuses, on the same screen

| Group | n | Verdict |
| --- | --- | --- |
| `agent-implementation` | 8 | **1.5305× over**, confidence 0.4476 |
| `data-modelling` | 1 | refused — 4 short of 5 |
| `llm-integration` | 1 | refused — 4 short of 5 |
| `scaffolding` | 1 | refused — 4 short of 5 |
| `frontend` | 0 | refused — *not one resolved outcome* |

One speaking group beside four refusals is a better demonstration of the
discipline than four factors would have been. **A refused group draws no bar at
all**: a zero-length bar reads as "no bias", which is the opposite of "not
enough evidence to say".

The Calibration Lens applies the group's factor to the median estimate of that
group, so both numbers are hours rather than multipliers — raw **5.3h**,
corrected **3.4h**, band **2.4–5.0**, n **8**, confidence **0.45**.

## No fusion flip, and that is the data rather than a gap

The store holds **zero `estimated_as` edges**, so `/api/fusion` prices nothing.
This was predicted before the demo was built and it was not engineered around.

Two reasons, both structural. No real ADR assumption *is* one of the dogfood
estimates — those edges are written by `praxis extract` (ADR 0016), not by a
seeder, and manufacturing them would have been the one piece of fabricated data
on a dashboard about not fabricating data. And even with edges, this corpus
cannot flip a `<=` predicate: the only group with enough history
**over**-estimates at 1.53×, so calibration makes every quantity *smaller*,
which relieves an upper bound rather than violating one.

The provenance strip renders a flip whenever one exists — the code path is
built and tested against a fixture graph that has an `estimated_as` edge — and
renders the measured breach otherwise. When neither exists it states the reason
instead of going blank.

## The design assets, and what was chosen where

Three renders were provided and they do not agree. `docs/assets/` is cooler and
denser, with a seven-item rail and a graph canvas; `docs/design/` is warmer,
with a six-item rail and a linear provenance strip.

**The smallest reasonable interpretation, stated as the brief asked:**
`docs/assets/` is the build target for layout, density and palette — it is the
more implementation-ready of the two and it is the render that appears inside
the landing page's own product shot, so the two assets are self-consistent.
`docs/design/` contributes the amber accent and the two panels `assets/` lacks:
the linear Decision Provenance strip and the Review Queue. The provenance strip
is how the Fusion payoff is rendered, because it is already laid out as the
argument chain.

One thing was deliberately not copied: the numbers. Every figure in those
renders is invented. All of it comes from the store.

**The landing page's hero product shot is live, not a screenshot.** The asset
puts a picture of the dashboard there. It reads `/api/overview` and
`/api/fusion` instead, so the page cannot claim a number the store does not
hold — and on an empty store it says so and names the command that fills it. A
screenshot would have been faster and would have been the only fabricated thing
on the page.

## Deviations, stated rather than buried

**The landing page is a scope addition.** The original Phase 11 brief specified
dashboard and demo only. It was added at the product owner's direction, with
design assets provided, and it is the largest single piece of the phase after
the dashboard itself.

**The new store reads are not in `reports.py`, which the brief asked for.**
That file was already 364 lines against a ~400-line rule and these would have
taken it past 900. They went to `praxis/store/dashboard.py` and, when that hit
420 lines mid-strand, `praxis/store/drilldown.py`. Same package, same
invariant — no SQL leaves `praxis.store`, and
`test_only_the_store_knows_the_database_driver` still enforces it mechanically.

**Three of the nine reads `EST-0012` priced were never written.**
`BiasDetective.all_factors()` and `reports.calibration_history()` have answered
what the calibration charts need since Phase 7; adding a store read that
recomputed either would have been the parallel query path the brief warned
against. `findings_for` collapsed into the review queue's own read.

**ADR 0037 was revised during implementation rather than quietly fixed.** Its
first draft put `fastapi`, `starlette` and `uvicorn` all in `NETWORK_MODULES`
and allowed them only in `server.py`. But `APIRouter` is a FastAPI import, so
that rule makes a routes module impossible and forces every handler into the
file that binds the socket — a rule enforcing a file layout rather than a
boundary. The shipped version is two rules: `uvicorn` is seam-only, and the
framework is confined to `praxis/web/`. An ADR corpus that only records the
decisions which survived contact is a worse corpus than one that says which did
not.

**`connect()` gained a `cross_thread` flag.** Starlette hands every request to
a threadpool and the store's handle is bound to one thread on purpose — the
module comment had anticipated exactly this and refused it. The flag is
explicit, documented, threaded through `connect_from_settings` and
`open_repository`, set by exactly one caller, and the web layer holds a
`threading.Lock` for the length of each request.

**The seeder writes an `unresolved` outcome for an estimate nothing answered.**
ADR 0022 says an unmatched estimate is an unresolved outcome, never a silence.
Adding it put `EST-0012` into the calibration history as a fifth group whose
refusal reads differently from the others.

## What the boundary test cost, and why it was worth it

`tests/test_boundaries.py` would have stayed green. Neither `fastapi` nor
`uvicorn` was in `NETWORK_MODULES`, because no web framework existed when that
set was written — so a dashboard would have imported cleanly and the seam would
have become decorative without anything failing. That is word for word the
failure the test's own docstring predicts.

The sentence "only one module in Praxis knows a network exists" is no longer
true. What replaces it is weaker and still checkable: two modules know, both
are named in a test, and adding a third is an edit somebody has to make on
purpose.

## The frontend has a gate, because there is no npm

`tests/web/test_static.py` is the linter. No asset may reference a remote
origin — `https://`, `http://`, a protocol-relative `//cdn`, or a CSS `url()` —
every referenced asset must exist, every page must parse and declare a title
and a language, and every id a script writes to must be declared somewhere. The
detector is watched failing on pastes it has never seen, the way the boundary
test's already was.

It caught a real gap during the phase: `app.js` writes to `#provenance-slot`,
which no page declares because the view creates it. The check now reads ids out
of script templates too.

## One defect the coverage report found

`praxis/store/drilldown.py` had two branches no test reached, and writing tests
for them found a real inconsistency rather than dead code. `Repository.get`
returns a retracted record rather than `None`, and an edge is never retracted
with its target — so an edge outlives what it points at. The list views filter
retracted rows in SQL; the drill-downs did not. A withdrawn assumption still
appeared in a decision's chain while the index beside it did not count it.

## The estimate

`EST-0012` predicted **8.5h active, uncorrected**, and 4.2h blocked, in a work
class — `frontend` — where this project had **n = 0**. No factor existed, none
was borrowed, and `agent-implementation`'s fitted 0.6534 was deliberately left
alone on `EST-0002`'s stated grounds.

The unit was new: eighteen render targets, nine store reads, eight plumbing
points, and a **design-fidelity loop** priced at 1.1h — the term that exists
because every prior phase had binary correctness and this one's acceptance test
is "does it look like the picture".

**`OUT-0012`: 4.5h active against 8.5h predicted — a 1.8889× over-estimate,
graded `partial` by `quality_for` rather than labelled by hand.** Blocked came
in at 2.7h against 4.2h, and this time the blocked figure was a product-owner
pause rather than a session limit.

**The refusal to correct was right on the evidence and still cost accuracy,
which is the finding worth carrying.** `EST-0012` declined to borrow
`agent-implementation`'s 0.6534 factor because `frontend` was at n=0. Applying
it would have given **5.55h** against an actual of 4.5h — ratio 1.2342, which
`quality_for` grades **`close`**, where the uncorrected figure grades
`partial`. Both things are true: at n=0 there was no principled basis to
correct, and correcting would have been better. What it suggests is that this
estimator's over-estimation may be a property of the *estimator* rather than of
the work class — a hypothesis n=1 cannot confirm, and one the Phase 12 analysis
should test across all five classes rather than assume.

**The unit held up better than the total.** Eighteen render targets were priced
and eighteen were built. Nine store reads were priced and six were written —
the other three were unnecessary because Phase 7 already answered them, which
is a research failure at estimate time rather than a pricing error. The
design-fidelity loop, priced at 1.1h, was the term that most nearly held: four
defects were caught by browser comparison that no test would have found. **The
over-estimate is concentrated in the per-target rate, not in the counts** — the
next frontend estimate should keep this unit and lower the minutes.

`frontend` moves from n=0 to n=1 and still refuses.
