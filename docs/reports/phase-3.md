# Phase 3 — Ingestion and Segmentation

> Merged as [#9](https://github.com/SihanUdayaratna03/praxis/pull/9), tagged
> `v0.3-phase-3`. 1149 tests, 98.09% coverage, CI 5/5 green.

The first phase with real agents in it, and the phase that decides whether
every later one is trustworthy. Every claim any agent ever makes will carry a
`span_id`, and that span is worth exactly as much as the offsets behind it.

---

## The decision the phase turns on

Segmentation is two decisions, and only one of them is a model's.

**Where a document may be cut** is fixed deterministically:
`praxis/ingest/blocks.py` splits it into headings, paragraphs, list items,
whole tables and whole fenced code, and property tests hold that grid to two
things — every non-whitespace character lies in exactly one block, and a
block's text is a *slice* of its own document rather than a rebuild from the
lines it was scanned out of.

**Which blocks belong together** is the model's, and it answers in **block
numbers**. The offsets are read off the grid and the text is sliced out of the
document by `Span.covering`.

That is the whole of [ADR 0011](../adr/0011-semantic-segmentation-over-a-deterministic-block-grid.md),
and the difference it makes is not stylistic. Had the model been asked for byte
offsets, span integrity would rest on `VerifierAgent` catching every fabricated
one; instead a fabricated offset is not a thing the pipeline can express, and
`VerifierAgent` runs anyway. A guarantee that depends on a check is weaker than
one that depends on a type — and the check is still there, because "impossible
by construction" and "checked regardless" is the only pairing worth making a
promise out of.

The grid is also the floor. A group that cannot be honoured exactly is dropped
with a reason, blocks no group claimed become spans of their own, and a window
the model refused or never answered usably degrades to one span per block. The
worst available answer costs segmentation quality and never correctness. That
is what makes ADR 0011's first assumption a cheap one to be wrong about: if
Phase 10 measures the grouping at parity with the paragraph floor, the fix is
to stop making the call, which is a deletion rather than a rewrite.

---

## What shipped

| Module | Lines | What it is |
| ------ | ----: | ---------- |
| `ingest/adapters.py` | 406 | Markdown, text and JSON into the exact bytes every offset addresses |
| `ingest/blocks.py` | 267 | The deterministic grid, and the degradation floor |
| `ingest/segmenter.py` | 346 | The first agent. Block numbers in, spans out |
| `ingest/verifier.py` | 275 | The citation gate. Deterministic, and always will be |
| `ingest/pipeline.py` | 310 | Adapter → segmenter → verifier → store, traced |
| `ingest/errors.py` | 77 | What can go wrong between a file and a span |
| `llm/structured.py` | 421 | Pydantic → the API's dialect, and the repair loop |
| `corpus/groundtruth.py` | 397 | The answer key's shape, and `verify_corpus` |
| `corpus/templates.py` | 349 | Four document shapes, four extraction problems |
| `corpus/topics.py` | 239 | The material: eight engineering decisions |
| `corpus/generator.py` | 180 | Writes the corpus, then verifies it against itself |
| `corpus/drafting.py` | 138 | Assembling a document while recording where it landed |

Plus `praxis ingest` and `praxis corpus generate`, a re-ingestion lookup by
content hash in `praxis/store/reports.py` — using the index Phase 1 built for
exactly that and had never queried — and `cli_tables.py`, which is the CLI's
rendering moved out of its wiring.

**No migration.** The `document` and `span` tables already existed and nothing
needed a new `FindingKind` member, so the schema is still at version 3.

### Three things worth reading the code for

**Normalisation is three transformations and no more.** Drop the byte-order
mark, collapse CRLF and a bare CR to LF, normalise to NFC. Each closes a way
one document could produce two byte lengths on two machines, and therefore two
sets of content-addressed span ids. What is deliberately *not* done matters as
much, and the tests assert it: no trailing-whitespace stripping, no re-wrapping,
no added final newline. Each would be harmless to read, would move every offset
after it, and buys nothing.

**A JSON source is re-rendered**, one `path: value` block per leaf, and its
spans address the rendering rather than the file. Prose left inside a JSON
string is prose behind escape sequences — every quotation would carry a literal
`\n` and no span would resolve against anything a human recognises. This is the
one place in the system where "the document" and "the file" are different text,
and it is why the corpus's answer key is written in `Document.content`
coordinates rather than file coordinates.

**The structured-output layer is subtraction.** The API's dialect rejects
numerical bounds, string bounds and most array bounds with a **400 rather than
ignoring them**, so a Pydantic model cannot be sent as written. The four steps
— strip the unsupported keyword, say it in the description instead, close every
object and require everything, validate locally against the original — are the
ones the official SDKs document performing, read from the docs on 2026-08-16
and cited in the module. The consequence is worth stating: **a bound the API
cannot enforce is asked for in prose and enforced in Python.**

---

## The corpus, and why it exists now

Phase 10 grades extractions automatically, which needs a corpus whose answers
are written down in a shape a program can compare against. It lands in Phase 3
because it is also Phase 3's own test data — inventing that twice would have
produced two corpora that disagree.

[ADR 0012](../adr/0012-machine-gradeable-corpus-ground-truth.md) records the
format: byte offsets and the quotation at them, stable ids, typed edges, per
field an expected value and a comparison mode, and `is_distractor` for text
that looks extractable and must not be extracted.

The mechanism matters more than the shape. Ground truth is emitted **by
construction** — a document is assembled from fragments and the byte range of
each is recorded as it is appended. The obvious shortcut, finding the fragment
in the finished text, mislabels silently every time a phrase repeats, and a
generator drawing from fixed phrase menus repeats phrases constantly. That
failure has no symptom: the offsets resolve, the quotation matches, and only
the meaning is wrong.

Two properties of the result are worth keeping:

- **Every ground-truth range is a whole block of the grid.** Not required for
  grading — byte overlap would work either way — and it means a citation score
  below one is about the extractor rather than about the grid.
- **Every generated ADR carries two assumptions**: one about the world, one
  about how long the work will take. The second is a quantified forward-looking
  claim, which is an estimate wearing an assumption's clothes, and it carries
  the `estimated_as` edge. The corpus therefore contains the fusion
  relationship the product exists to find, labelled, before the agent that has
  to find it is written.

---

## The one change to Phase 2 code

`synthesis.py` gained `ordinals_in`. The mock now answers a numbered listing
with labels it was actually shown — the same argument `identifiers_in` already
made about ids, applied to the other kind of reference this pipeline hands a
model.

It is worth recording *why* this was not left alone. Measured before the
change: every synthesised group referred to a block that was never offered, so
every group was refused and offline segmentation was always the floor. That is
a systematic failure rather than a realistic one, and it has a second cost that
matters more — **the code that honours a good grouping would never have run
offline.** Segmentation would have looked correct in unit tests and degraded in
every real run, which is exactly the gap ADR 0005's assumption 3 is about.

The two ends of a range are still drawn independently, so a pair is as likely
to be reversed as ordered. A mock that emitted well-formed ranges would be
imitating a competent model rather than a model, and the code that refuses a
reversed range would go untested.

---

## Things that cost something

**A test built from two identical-looking literals tests nothing.** The NFC
test compared `"café"` with `"café"` — which, typed into one source file, are
the same string. It passed while asserting nothing. Both spellings are now
built from `chr(0x00E9)` and `chr(0x0301)`, and the test asserts they differ
before asserting normalisation collapses them. This is the same shape as the
single-seed defect Phase 2 recorded, in a different disguise.

**`stats.records` omits kinds with no rows.** It is a `GROUP BY`, so
`records[RecordKind.SPAN]` raises `KeyError` on an empty store rather than
returning zero. Two pipeline tests asserting "nothing was stored" failed for
that reason and not for the reason they were testing.

**Ruff's auto-fix removes an import added before its use lands.** Known since
Phase 1 for the `post_edit_verify` hook; it also applies to a manual `ruff
check` run between two edits. Adding the import and its use in one edit is the
only reliable order.

**A `conftest` fixture in a `@given` test needs a health-check suppression.**
`HealthCheck.function_scoped_fixture` is the one, and the alternative — moving
the fixture into the strategy — would have generated a new settings object per
example for no benefit.

**The generated prose needed reading.** "This rests on the index stays under 50
GB" is what a template produces when a phrase written to follow "We are
assuming" is dropped after "rests on". Nothing automated would have caught it;
the corpus is text a model has to read, and it has to read like text.

---

## The estimate

| | |
| --- | --- |
| `EST-0004` | 5.5 hours active, 20 hours blocked, confidence 0.45, class `agent-implementation` |
| `OUT-0004` | ~4.2 hours active, ~12.8 hours blocked, scored **`close`** |
| Miss direction | **over-estimated, ~1.3×** |

**The active figure is the least certain number in this corpus.** Phase 3 ran
across two sessions, and the first one ended on a session limit *without a
final commit* — so its length is bounded below by commit timestamps and above
by the five-hour limit, with nothing recording where inside that range the work
actually stopped. OUT-0002 and OUT-0003 could bound their windows from both
ends. This one cannot, and the figure should be read as the midpoint of a
defensible range rather than as a measurement.

### Where the estimate was right, and where it was not

The scope correction that OUT-0002 demanded and OUT-0003 confirmed worked
again, on one axis and not the other:

| | Estimated | Actual |
| --- | ---: | ---: |
| Test lines | ~2,600 | **2,826** |
| Production lines | ~1,900 | **3,740** |

The test figure is close to exact for the third phase running. The production
figure is nearly **2× under**, and the reason is legible: the estimate's
conditions priced "the synthetic corpus generator" as one line item. It became
five modules and 1,301 lines — a ground-truth format with its own validation, a
drafting layer, four templates, the topic material, and the generator itself.
Everything else in the phase landed close to what a line-item reading would
have predicted.

That is a better error than the Phase 1 one. Phase 1 under-priced *the tests
that make a feature trustworthy*, which is a category mistake. This
under-priced *one named component*, which is an ordinary estimation miss — and
it is visible only because the estimate priced line items at all.

The conditions also held where they were checkable: no migration was needed, no
live API call was made, the repair loop was built inside this phase, and the
segmenter's floor was built before its LLM path.

### The pattern, still not corrected for

Four outcomes now exist, in four work classes, `n = 1` each:

| Phase | Class | Direction |
| --- | --- | --- |
| 0 | `scaffolding` | over 2.3× |
| 1 | `data-modelling` | under 2.1× |
| 2 | `llm-integration` | under 1.24× |
| 3 | `agent-implementation` | **over ~1.3×** |

**`EST-0005` gets no bias correction**, for the fourth time and the same
reason. `BiasDetective` refuses below `n = 5`, that refusal is the product's
own thesis, and the first place to break it should not be the author of the
rule. The directions do not even agree with each other — this phase went back
over after two under — which is precisely the shape of data a human over-reads
and the reason the threshold exists.

---

## For the session that writes `EST-0005`

Three things this phase learned that are worth pricing next time.

**Price components, not phases, and count the components honestly.** The one
line item that went badly wrong was the one described in four words. If an
estimate names something as a single item and that item has its own format, its
own validation and its own tests, it is not one item.

**A phase that ends without a commit costs its own measurability.** The Phase 3
active figure is soft because a session limit landed between two commits. A
cheap fix for Phase 4: commit before a session is likely to end, even if the
component is not finished — a `wip:` commit on a strand branch is not in the
history `main` will ever see, and it is the difference between a bounded window
and a guess.

**The remaining work is orchestration, and orchestration is where determinism
gets decided.** Phase 4 owns the state machine, and the property that matters —
two runs over one corpus with one seed producing identical numbers — is
already true of everything Phase 3 built and is easy to lose. The segmenter's
spans, the corpus's bytes and the answer key are all functions of their inputs
today, and each has a test saying so.
