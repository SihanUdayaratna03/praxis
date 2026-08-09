---
id: ADR-0005
status: accepted
date: 2026-08-09
decision_maker: Sihan Udayaratna
impact: high
supersedes: null
superseded_by: null
---

# 0005 — Offline-first `LLMProvider` with three implementations

## Chosen

All model access goes through one `LLMProvider` interface with three
implementations: `MockProvider` (deterministic, offline, the default),
`AnthropicProvider` (live), and `ReplayProvider` (recorded fixtures). Selection
is `PRAXIS_LLM_PROVIDER`. `MockProvider` is rich enough to drive the entire
pipeline, test suite, eval harness, CLI and dashboard with no network and no
credentials.

## Rejected

| Option | Why not |
| ------ | ------- |
| Call the Anthropic SDK directly, add tests with a mocking library later | Retrofitting a seam through twenty agents is a rewrite. More importantly, ad-hoc mocks in test files are not the same artifact as a mock that must satisfy the real pipeline end to end — the second finds integration bugs the first cannot. |
| Record/replay only, no synthetic mock | Replay cannot produce a first recording without a key, which is the situation this project actually starts in. It also cannot answer "what does the pipeline do on input it has never seen", which the synthetic corpus generator in Phase 3 requires. |
| A mock returning fixed strings | Would satisfy the type checker and nothing else. The pipeline would run and prove nothing, and the eval harness would report metrics about the mock. |

## What was known at the time

No API key is available and there is no committed date for one. CI runs on
GitHub Actions with no secret configured, deliberately. The eval harness in
Phase 10 must produce comparable numbers across commits, which requires the
model layer to be reproducible independently of any live service.

Not known: how faithfully a deterministic mock can imitate the failure modes
that matter — malformed structured output, near-miss citations, confident
wrong answers — and therefore how much of the pipeline's robustness is
genuinely exercised offline versus only appearing to be.

## Assumptions

| # | Assumption | Predicate | Expiry condition |
| - | ---------- | --------- | ---------------- |
| 1 | A deterministic mock can exercise the whole pipeline without special-casing inside agents | `agent_files_referencing_provider_name == 0` | `when(agents_implemented >= 8)` |
| 2 | Switching to live models requires editing `.env` and nothing else | `files_changed_to_enable_live_models == 1` | `on_event("an API key becomes available")` |
| 3 | Mock outputs are realistic enough that agents written against them work on real responses | `live_vs_mock_agent_failure_delta <= 0.15` | `on_event("the first live run completes")` |
| 4 | CI never needs a credential | `ci_secrets_referenced == 0` | `when(phases_completed >= 6)` |

## Consequences

**Accepted costs.** `MockProvider` is real software with its own maintenance
burden, and it must be extended every time an agent needs a new response shape.
Assumption 3 is the uncomfortable one: until a live run happens, the gap
between mock-realistic and real is unmeasured, and some of what looks like a
working pipeline may be the mock being agreeable. The predicate is written so
that the first live run is a measurement rather than a surprise.

**Reversal cost.** Effectively zero in the useful direction — adding the live
provider is additive. Removing the seam later would be expensive, which is the
point of putting it in before any agent exists.

## Note on framing

This is presented in the README as good engineering rather than as a
workaround, because it is: deterministic replay is what makes an agent system
testable, makes eval results comparable between commits, and makes a bug
reproducible instead of anecdotal. The missing key is what forced the decision;
it is not what justifies it.
