---
id: ADR-0017
status: accepted
date: 2026-08-26
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0017 — A small total predicate language with three-valued evaluation

## Chosen

Assumption predicates are a **hand-written recursive-descent grammar** over
comparisons of identifiers, numbers, strings and booleans, joined by `and`,
`or` and `not`, with a closed set of call forms. Evaluation is **three-valued**:
true, false, or unknown-with-a-reason. An identifier nothing has measured makes
the predicate unknown rather than false.

Expiry conditions are a second, smaller grammar — `on_event("...")`,
`after("...")`, `when(<predicate>)` — parsed and rendered separately, because
"is this still true" and "is it time to look again" are different questions and
folding them into one expression would make an expired assumption
indistinguishable from a violated one.

Two properties are load-bearing and both are property-tested with `hypothesis`:

- **Evaluation is total.** No input raises. A malformed predicate fails at
  *parse* time, and a parsed predicate always evaluates to one of three values.
  The monitor runs unattended over a whole store, so a predicate that could
  raise would be a predicate that stops the pass.
- **Rendering round-trips.** `render(parse(s))` parses to the same tree, so the
  stored form can be normalised without changing what it claims. That is what
  lets `x<=50` and `x <= 50` land in one blocking bucket in ADR 0018 rather
  than two.

The language is deliberately not Turing-complete, has no arithmetic beyond
comparison, and cannot call out to anything. It is a language for *stating a
condition*, not for computing one.

## Rejected

| Option | Why not |
| ------ | ------- |
| Python expressions via `eval` or `ast.literal_eval` | An assumption predicate arrives from a model and from documents nobody audited. `eval` is arbitrary execution on that input, and `literal_eval` cannot express a comparison at all. Even sandboxed, the grammar a reader has to learn becomes "Python, minus an undocumented subset". |
| An existing rules engine or CEL-style library | Adds a dependency that owns the semantics of the one thing invariant 3 says must be deterministic code we can property-test. It would also decide the two-valued/three-valued question for us, and that answer is the substance of this record rather than a detail. |
| Two-valued evaluation, treating unmeasured as false | Makes every assumption nobody has measured read as breached. That is the single most damaging output this system can produce: it floods a person with findings on their first run and teaches them the findings are noise. Unknown is not a weaker false; it is a different fact. |
| Store the predicate as prose and ask a model at evaluation time | Invariant 3. A predicate whose truth depends on sampling is not a predicate, and the whole claim of the monitor is that a breach is arithmetic on facts. It would also make two runs over one store disagree. |
| One grammar for predicates and expiry conditions | The monitor has to tell "this is false" from "it is time to re-check", and a single expression cannot carry that distinction without a convention that is really two grammars wearing one name. `after("2026-12-31")` is not a claim about the world. |
| A richer grammar now — arithmetic, quantifiers, temporal operators | Every construct is a construct the formalizer must be taught to emit and a reader must learn. The measured parse rate says what the current grammar covers; widening it before that number says it is short would be building for an imagined corpus. |

## What was known at the time

ADR 0001 recorded, on 2026-08-09 and with no parser in existence, that a DSL
built in Phase 5 would be able to read predicates written by hand before it.
Sixty-four such predicates existed across `docs/adr/` by the time this was
built. **Sixty-three parse — 0.9844 against a threshold of 0.9** — and every
expiry condition parses. `praxis/eval/adrs.py` recomputes that from the files
rather than asserting it, so widening the grammar to make it hold would be
visible as a grammar change.

The one row that does not read is ADR 0015's third assumption, `outside [0.5,
2.0]`, which is English rather than an expression. It was left as a finding on
purpose: adding an interval-membership operator for a single instance would be
answering a measurement with a feature request.

Not known: whether a *model* asked to write in this grammar hits it as reliably
as a person does. The formalizer's parse rate answers that, and offline it
measures the plumbing rather than a model (ADR 0016's caveat applies unchanged).
Also not known is how much of a real corpus's assumptions are expressible at
all — every predicate measured so far was written by one author who knew the
grammar he was aiming at.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | The grammar can read predicates written by hand before it existed | `adr_predicates_parsed_rate >= 0.9` | `when(adr_count > 40)` |
| 2 | The grammar is wide enough as it stands, and will not need repeated extension to keep that rate | `predicate_grammar_extensions_after_phase_5 <= 2` | `when(adr_count > 40)` |
| 3 | A model asked to write in this grammar produces a checkable predicate most of the time | `formalizer_checkable_rate >= 0.7` | `on_event("the first live extraction run")` |
| 4 | Nothing that fails to compile is silently lost, so a bad parse rate is always visible as marked records rather than as absence | `uncheckable_predicates_silently_dropped == 0` | `when(store_assumptions > 500)` |

## Consequences

**Accepted costs.** There is a grammar to maintain, document and teach, and it
is the second language in the repository. Every assumption an author writes has
to be aimed at it, which is a real constraint on how an ADR is written — the
`outside [0.5, 2.0]` row is what that constraint looks like when it bites.

Three-valued evaluation means the monitor's most common output is "nothing
measured this". That is honest and it is also unsatisfying, and it stays
unsatisfying until Half B writes outcomes. A reader seeing a store full of
`unverified` is seeing the mechanism waiting rather than failing.

Normalising the stored predicate means the model's exact spelling is not kept.
The round-trip property is what makes that safe, and the previous version stays
readable in the store, so the original text is recoverable rather than lost.

**Reversal cost.** High for the language, low for any one construct. The
grammar's shape is baked into the stored predicates, the blocking index and the
monitor, so replacing it would mean re-formalizing every assumption in every
store — which is a model call per record. Adding or removing a single operator
is a parser change plus a rendering case, and the round-trip property test says
immediately whether it was done correctly.
