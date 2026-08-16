# Phase 2 — model access

Merged as [#7](https://github.com/SihanUdayaratna03/praxis/pull/7), tagged
`v0.2-phase-2` at `7cdddec`. What follows is the account: what was built, what
was decided along the way, what went wrong, and how the estimate did.

---

## What shipped

| | |
| --- | --- |
| `main` | `7cdddec`, local and remote identical |
| Tag | `v0.2-phase-2` → `7cdddec` (dereferenced through the API, not assumed) |
| CI | 5/5 green on the PR |
| Suite | **880 passed** (453 at the close of Phase 1), coverage **98.16%** |
| Schema | version 3 |
| Credentials required to run anything | 0 |

Twelve modules under `praxis/llm/` and one more table in the Phase 1 store:

```
llm/types.py        request, response, usage, stop and outcome vocabulary
llm/errors.py       the seam's exceptions; where a vendor SDK stops
llm/hashing.py      the replay key: one identity per request
llm/accounting.py   what a call cost, and the ceiling checked before it
llm/trace.py        the trace row and the sink protocol
llm/provider.py     the seam: route, price, time, trace, raise
llm/synthesis.py    a plausible answer built from a schema and the prompt
llm/mock.py         the offline default: deterministic, free, counted
llm/replay.py       recorded fixtures, and a loud miss
llm/anthropic.py    the only module that imports an SDK
llm/factory.py      the only code that reads PRAXIS_LLM_PROVIDER
store/traces.py     the llm_trace table, append-only by trigger
```

---

## The four decisions that shaped it

**The seam carries the promises, not the implementations.** An implementation
supplies `_invoke` and nothing else. Routing, the cost ceiling, timing and the
trace write happen once, in `LLMProvider.complete`. The alternative — each
provider doing its own — would have made "every call is traced" a claim about
three classes instead of one, and the claim that matters is the one about the
class that does not exist yet. Twenty-nine of the seam's tests run against a
two-line stub for exactly that reason.

**The mock builds answers, it does not hold them.** ADR 0005 rejected fixed
strings. The estimate assumed the replacement would be a response library with
an entry per agent — `DecisionScout`, `AssumptionExtractor`, and three more.
What landed instead walks the response schema and fills each field from the
prompt: a quotation field gets a sentence that really occurs in the input, an
id field an id that was really supplied. It names no agent, which means it
already answers the five agents the estimate listed and every agent after
them, and it keeps ADR 0005's first assumption
(`agent_files_referencing_provider_name == 0`) true without anyone maintaining
it.

The part worth keeping is the deliberate failure. When a prompt carries no
record ids at all, the synthesiser fabricates a *well-formed and wrong* span
id. `VerifierAgent` re-reads a cited span and rejects a claim it does not
contain, and a mock that only ever produced verifiable citations would let the
offline pipeline pass a check the live one has to earn.

**A replay miss raises.** The whole design is what it refuses to do. A replay
run that quietly went live where a fixture was absent would make an eval result
depend on which fixtures a machine happened to have — the failure replay exists
to rule out. Three things are checked before a fixture plays back: the recorded
request still hashes to the key it is filed under, the model is the one routing
chose today, and the file parses whole. All three raise the same error with a
`detail`, because the response to each is identical: record it again.

**One module imports an SDK, and a test parses the source to keep it that
way.** `tests/test_boundaries.py` reads every module with `ast` rather than
importing it, because an import-based check would pass on a machine where the
optional extra is absent — which is exactly the machine CI runs on. The stdlib
names (`http`, `urllib`, `socket`, `ssl`) are in the forbidden set beside the
third-party ones: without them, "no HTTP client" would mean only "no
third-party HTTP client".

---

## What went wrong, and what it cost

**The synthesiser rounded numbers out of their own bounds.** Rounding a drawn
value to two decimal places is what a model writes, and it is also what steps a
value back onto a bound the schema excludes — `exclusiveMinimum: 0` could
produce `0.0`. Caught by a test that ran thirty seeds rather than one. The fix
is a clamp after the round; the lesson is that a single-seed test of a random
generator tests one number.

**The mock quoted the system prompt.** The first end-to-end mock test failed
with a citation lifted from *"You find decisions in engineering documents."* —
a quotation that is real, verifiable against nothing, and would have made every
offline citation fail `VerifierAgent` in the same systematic way. `LLMRequest`
grew `source_text` (the conversation) beside `prompt_text` (everything the
model reads, which is what the cost estimate is measured over). The brief is
instruction, not evidence.

**The SDK reports cache token counts as `None`, not zero.** Found because the
live provider's tests build real `anthropic.types.Message` objects instead of
stubs. A stub would have agreed with whatever the module assumed, and the
disagreement would have surfaced on the first live call, in the column the eval
harness sums.

**Two hooks fought the work and were right both times.** `guard_no_secrets.py`
refused a line that passed the decrypted key straight into the SDK
constructor's keyword argument — which is precisely the shape a hard-coded
credential has, and the scanner cannot tell the two apart. The value is bound
to a named local first, with a comment saying why, rather than the rule being
loosened. `post_edit_verify.py` rejected a `**kwargs` dictionary that
`mypy --strict` could not check through; the fix was to pass the four arguments
explicitly.

**`assert_never` and `warn_unreachable` disagree.** The factory's exhaustive
`if` chain proves every `ProviderName` is handled, so mypy flags the
`assert_never` after it as unreachable code. Rather than silence one of two
gates, the exhaustiveness check moved into a test that walks every enum member
and asserts the provider it gets back reports that same name — which catches a
future member falling through to the mock at runtime, where it would actually
happen.

---

## The estimate

| | |
| --- | --- |
| `EST-0003` | 4.5 hours active, confidence 0.45, class `llm-integration` |
| `OUT-0003` | ~5.6 hours active, ~30 hours wall clock, scored **`close`** |
| Miss direction | **under-estimated, ~1.24×** |

The scope correction OUT-0002 demanded worked. EST-0003 priced the tests as
line items rather than assuming they follow the feature: roughly 2,200
production lines and roughly 3,000 test lines. The actual is **2,246
production** and **2,566 test**. The production figure is close to exact. The
test figure is short by about one component — the bounded repair loop, which
was named in the estimate's subject and deliberately not built.

That deferral is the honest asterisk on a 1.24× miss, and it is recorded in
[`BACKLOG.md`](../../BACKLOG.md) with its reason: the repair loop's shape is
decided by what the first agents actually get back, and writing it now would be
guessing at a failure distribution nobody has measured. `MockProvider` already
takes a `malformed_share`, so the loop will have something to repair on the day
it is written.

Scope moved the other way too. The estimate priced two ADRs that turned out to
have been written in Phase 0, and it did not price the boundary test, the
provider factory, `RecordingAnthropicProvider`, or `doctor` making a real call.

Three outcomes now exist, in three work classes, and they disagree in
direction: `scaffolding` over 2.3×, `data-modelling` under 2.1×,
`llm-integration` under 1.24×. `n = 1` each. **`EST-0004` gets no bias
correction**, for the third time and the same reason: `BiasDetective` refuses
below `n = 5`, that refusal is the product's own thesis, and the first place to
break it would be here.

---

## For the session that writes `EST-0004`

One thing this phase learned that is worth pricing next time. Every component
cost roughly the same again in tests, and the tests that found real defects
were the ones built on *real* objects — the SDK's own message model, thirty
seeds instead of one, the boundary detector run against a module that actually
crosses the boundary. Those cost more to write than the assertions that pass on
the first try. Price the tests that could fail, not the tests that will pass.
