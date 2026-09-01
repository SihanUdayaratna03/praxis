---
id: ADR-0037
status: accepted
date: 2026-09-01
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0037 — The dashboard is the second network seam, and it is named

## Chosen

`praxis/web/server.py` becomes the **second and only other** module in the
package allowed to reach the network. `tests/test_boundaries.py` names it
beside `praxis/llm/anthropic.py` and keeps checking every other file.

`uvicorn` is **added to `NETWORK_MODULES`**, because it is the thing that
actually binds the socket and it was not in that set only because no web
framework existed when the set was written.

`fastapi` and `starlette` are handled by a **second, separate rule**: they may
be imported anywhere under `praxis/web/` and nowhere else. They open no
sockets — they parse requests — so the claim worth holding about them is not
"only one file may import this" but "the framework must not leak into an
agent, the store or the eval harness".

Serving is confined to the seam module: it constructs the app, mounts the
static files and binds the socket. The routes and the response models are
sibling modules that import FastAPI but never the server.

**This is a revision.** The first draft of this ADR, written before the code,
put all three names in `NETWORK_MODULES` and allowed them only in
`server.py`. Implementing it showed that rule forces every route handler into
the seam file, because an `APIRouter` is a FastAPI import — which would have
produced one 700-line module for the sake of a sentence. The two-rule version
holds the same two properties and costs nothing. Recorded here rather than
quietly fixed: an ADR that only ever documents decisions that survived contact
is a worse corpus than one that says which did not.

## Rejected

| Option | Why not |
| ------ | ------- |
| Add `fastapi` and `uvicorn` without touching `NETWORK_MODULES` | The tempting option, because it is the one that requires no edit: neither name is in that frozenset today, so a web server would import cleanly and the suite would stay green. That is precisely the failure the test's own docstring predicts — a rule that "survives the commit that states it and dies three phases later, when one convenient import makes a seam decorative without anything failing". A seam that passes because the check does not know about the new thing is not a seam. |
| One rule: all three names in `NETWORK_MODULES`, seam-only | This ADR's own first draft, rejected during implementation. `APIRouter` is a FastAPI import, so a routes module could not exist and every handler would have to live in the file that binds the socket. The rule would have been enforcing a file layout rather than a boundary. |
| Widen the rule to "any module under `praxis/web/` may reach the network" | A directory-shaped exemption grows without anyone deciding to grow it. Twelve files under `praxis/web/` would all be permitted to open a socket, and the twelfth would not be noticed. Naming one file means adding a second costs a visible edit to a test, which is the property the original seam was built to have. |
| Put the server outside the package, in a top-level `serve.py` | Would keep `praxis/` clean by moving the problem somewhere the boundary test does not look, which is hiding rather than deciding. It also breaks `praxis serve` as a console entry point and puts the dashboard outside the wheel. |
| Drop the boundary test as no longer useful now that the package is a server too | The test also enforces that `praxis.llm` never imports the store, that `sqlite3` stops at `praxis.store`, and that `praxis.predicates` never reaches a model. Those are three invariants from three different phases. Deleting the file to avoid one edit costs all of them. |

## What was known at the time

`NETWORK_MODULES` lists vendor SDKs, third-party HTTP clients, and the standard
library's `http`, `urllib`, `socket` and `ssl` — the stdlib entries added
deliberately so that "no HTTP client" does not quietly mean "no *third-party*
HTTP client". The set was written before any web framework existed in this
project, which is why none is in it.

The check reads source with `ast` rather than importing, and reduces a dotted
import to its root, so `from praxis.llm.anthropic import AnthropicProvider`
does not read as the SDK. Adding three names to the frozenset costs nothing at
runtime and is checked against every file in the package.

`praxis serve` binds `127.0.0.1` by default. A host flag exists, so binding
publicly is possible and is the user's explicit act rather than the default.

What is **not** known: whether `starlette` will stay the only transitive path
to a socket that matters. `uvicorn` also pulls `h11`, which is a protocol
parser rather than a client and is not in the set. The rule is about what a
Praxis module *imports directly*, and it always has been — it has never
attempted to be a transitive audit, and this ADR does not change that.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | Exactly two modules in the package reach the network | `network_seam_modules == 2` | `on_event("a third module is named in the boundary test")` |
| 2 | The web framework is confined to the web package | `web_framework_imports_outside_praxis_web == 0` | `on_event("a web framework is replaced")` |
| 3 | Only the seam module imports the server | `modules_importing_uvicorn == 1` | `on_event("a second module imports uvicorn")` |
| 4 | The dashboard binds a loopback address unless told otherwise | `default_bind_is_loopback == 1` | `on_event("the default host is changed")` |

## Consequences

**Accepted costs.** The sentence "only one module in Praxis knows a network
exists" is no longer true, and it was a good sentence. What replaces it is
weaker and still checkable: two modules know, both are named in a test, and
adding a third is an edit somebody has to make on purpose.

Widening `NETWORK_MODULES` also means a future contributor who imports FastAPI
in a route module gets a failure that looks like a mistake in the test rather
than a rule doing its job. The assertion message has to say why, and it does.

**Reversal cost.** Trivial in code and total in consequence. Removing the second
seam is deleting one entry from a list — but only after the dashboard it exists
for is gone, so the real reversal cost is ADR 0036's, not this one's.
