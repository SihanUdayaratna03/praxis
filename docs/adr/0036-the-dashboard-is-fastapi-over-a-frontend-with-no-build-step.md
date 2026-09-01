---
id: ADR-0036
status: accepted
date: 2026-09-01
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0036 — The dashboard is FastAPI over a frontend with no build step

## Chosen

Phase 11's dashboard and landing page are served by a **read-only FastAPI
application** in `praxis/web/`, with a frontend of hand-written HTML, CSS and ES
modules in `praxis/web/static/` — **no build step, no framework, no CDN, and no
npm in this repository**.

`fastapi` and `uvicorn` become declared runtime dependencies. Charts are inline
SVG drawn in JavaScript from the JSON the API returns.

Every read goes through `praxis.store.repository`, `praxis.store.reports`,
`praxis.store.traces` or `praxis.eval`. A query the dashboard needs and the
store does not have is added to `reports.py` beside `calibration_history()`,
never as SQL in a route handler.

**No web framework was previously chosen.** Phase 0 recorded facts *about* a
future dashboard — ADR 0003 says it is read-mostly and expires its
single-writer assumption `on_event("Phase 11 dashboard gains write
endpoints")`, and ADR 0008 names "a Phase 11 dashboard query" as a reason to
enforce append-only in a SQLite trigger rather than in Python. Neither names a
framework. This ADR makes that choice for the first time.

## Rejected

| Option | Why not |
| ------ | ------- |
| The standard library's `http.server` | Zero new dependencies, and it makes me write the two things most likely to be wrong: my own routing and my own static-file serving. Serving files off a URL path is a directory-traversal footgun that ruff's `S` rules would flag and that I would have to argue my way past. Starlette's `StaticFiles` has had that scrutiny and I have not. The dependency is the cheaper risk. |
| Flask | Would work, and brings no typing. FastAPI's response models are Pydantic, which is already a declared dependency, so the API's shapes are checked by `mypy --strict` and by the same validation layer the domain records use. A second, untyped way to describe a payload in a project this strict about types is a regression. |
| React, Vue or Svelte from a CDN | Breaks invariant 1 and the offline-first premise outright. A `<script src="https://cdn...">` means the dashboard renders blank on a machine with no network — for a product whose first trust badge reads **offline-first**, that is not an irony, it is a defect. |
| React with a local build step | Same frameworks, no CDN, and it puts `npm` and `node_modules` into a repository whose entire toolchain is `uv` and whose CI has three jobs that install nothing else. The dashboard is roughly a dozen views over a JSON API; it does not need a virtual DOM, and buying one costs a second package manager, a second lockfile and a second supply chain forever. |
| A charting library (Chart.js, D3, Plotly) | Unreachable offline for the CDN reason above, and vendoring a minified bundle into the repository puts a blob nobody reviews under version control. The charts here are sparklines, two overlaid distribution curves, and a four-node provenance chain. That is a few hundred lines of SVG path arithmetic, which is reviewable, and it is the kind of arithmetic this project already property-tests elsewhere. |
| Server-rendered Jinja templates instead of a JSON API | Simpler for a first screen and it forecloses the thing the phase is for. The trace panel and the drill-down are interactive; rebuilding a page to expand a reasoning panel would be worse. A JSON API also makes the dashboard's reads testable without rendering anything. |

## What was known at the time

The store is SQLite, single-writer, and read-mostly for this purpose (ADR
0003). The dashboard adds **no** write endpoints, so ADR 0003's assumption 3
does not expire in this phase — it is checked and left standing, deliberately.

`tests/test_boundaries.py` forbids every module in the package but
`praxis/llm/anthropic.py` from importing a network module, and forbids
`sqlite3` outside `praxis.store`. The second rule is what keeps the "no inline
SQL in a route" promise mechanical rather than aspirational. The first is
handled in ADR 0037.

Resolved versions, read off the resolver rather than recalled: `fastapi`
0.141.1, `uvicorn` 0.52.4, `starlette` 1.6.0. Against the existing lock this
adds **four** packages — `fastapi`, `starlette`, `uvicorn` and `click`.
`anyio`, `h11`, `idna`, `annotated-doc`, `annotated-types`, `typing-extensions`
and `typing-inspection` are already locked, and `pydantic` is already a
declared dependency. Typer 0.27.1 no longer depends on `click`, so `click`
arrives with `uvicorn` and is genuinely new.

What is **not** known: whether the SVG charts stay readable as the store grows.
They are drawn from whatever the API returns, and a calibration history of
several hundred estimates would need binning that this phase does not build.
Also not known: whether a browser is the right place for the graph view at all
once the store holds thousands of edges — the demo store holds tens.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The dashboard stays read-only, so ADR 0003's single-writer assumption holds | `dashboard_write_endpoints == 0` | `on_event("the dashboard gains a write endpoint")` |
| 2 | The frontend needs no build step and no package manager but uv | `frontend_build_steps == 0` | `on_event("a javascript toolchain is added")` |
| 3 | The page renders with no network beyond the local server | `frontend_external_origins == 0` | `on_event("an asset is loaded from a remote origin")` |
| 4 | No SQL reaches a route handler; every read is a store function | `inline_sql_in_web_layer == 0` | `on_event("praxis.web imports sqlite3")` |
| 5 | Hand-drawn SVG stays legible at demo scale | `dashboard_chart_points <= 500` | `when(store_estimates > 500)` |

## Consequences

**Accepted costs.** Four new runtime packages on a project that had five, and
one of them — `uvicorn` — exists only to bind a socket. Every chart is code I
own, so a class of bug that a charting library would have fixed years ago is
now mine to fix. Writing SVG by hand is slower than writing a `<Chart>` tag, and
that cost is priced explicitly as a term in `EST-0012`.

The frontend has no type checking. `mypy --strict` covers the API and stops at
the browser, and there is no TypeScript to replace it. The mitigation is that
the JSON contract is a Pydantic model on one side and that the views are small,
not that the JavaScript is checked. This is the weakest part of the decision.

**Reversal cost.** Low for the backend, moderate for the frontend. The route
layer is thin over store functions that would survive any framework, so
swapping FastAPI is a rewrite of one module. Adopting a JS framework later
means rewriting the views but not the API — and it means accepting the build
step this ADR declined, which is the part that would be hard to undo.
