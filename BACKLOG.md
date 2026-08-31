# Backlog

Work deliberately not done, with the reason. Nothing in this repository says
`TODO: implement later`; it says "see BACKLOG.md" or it does not exist.

Each item records **why not now**, because that is the part that decays. An
entry whose reason has stopped being true should be promoted or deleted.

## Dated decision points

Not deferred work — questions with an owner, a trigger, and a deadline. Unlike
the entries below, these expire: reaching the trigger without a decision is
itself the failure.

### Repository visibility — decide at the start of Phase 8

| | |
| --- | --- |
| **Decision** | Stay private for now. Revisit going public around Phase 8, before competition submission. |
| **Decided by** | Sihan Udayaratna, 2026-08-09 |
| **Trigger** | Phase 8 begins (the fusion layer — the phase that makes the project worth showing) |
| **Latest safe date** | Before submission. Going public *after* submission wastes the benefits entirely. |

**What going public would buy.** Real server-side branch protection, free — it
is currently refused with `403 Upgrade to GitHub Pro`, so `main` is protected
only by client-side hooks. Judges could browse the network graph directly and
see each phase as its own strand, which is a deliberate artifact of ADR 0007.
Unlimited Actions minutes.

**What it costs.** The work becomes visible before submission, including the
incomplete phases and the parts where the reasoning is still wrong.

**Why Phase 8 and not sooner.** Phase 8 is where the product's central claim
first exists in code. Before that the repository shows scaffolding and half a
thesis; after it, the thing being judged is actually there. Waiting also means
the ADR corpus and commit history are substantial enough to be evidence rather
than a promise.

**When the trigger fires, do this:**

```bash
gh repo edit SihanUdayaratna03/praxis --visibility public --accept-visibility-change-consequences
gh api -X PUT repos/SihanUdayaratna03/praxis/branches/main/protection --input <ruleset>
```

The ruleset to apply is in the branch-protection row below. Confirm with the
owner before flipping visibility — it is outward-facing and effectively
irreversible in reputation terms even though the flag itself is not.

## Deferred from Phase 0

| Item | Why not now |
| ---- | ----------- |
| Branch protection on `main` | **Attempted and refused**, not assumed: `PUT /repos/.../branches/main/protection` returns `403 — Upgrade to GitHub Pro or make this repository public to enable this feature`. Blocked on the visibility decision above, so it unblocks at Phase 8. Until then the same rules are enforced client-side by the pre-commit `no-commit-to-branch` hook and `guard_git_workflow.py`. ADR 0007 assumption 3 tracks this. Ruleset to apply verbatim: `required_status_checks.strict = true` over the five CI contexts (`lint + types`, `build distribution`, `tests (ubuntu-latest, py3.12)`, `tests (ubuntu-latest, py3.13)`, `tests (windows-latest, py3.12)`), `allow_force_pushes = false`, `allow_deletions = false`. |
| Move the working copy off the OneDrive-synced path | The owner chose the location. File-sync tools have a poor record around SQLite WAL files, so this becomes a real risk in Phase 1 when the store exists. ADR 0003 assumption 5 is written to fire. The database is gitignored and rebuildable, so the blast radius is a rebuild. |
| Coverage reporting to a service (Codecov or similar) | Needs a token, which contradicts the no-credentials-in-CI stance. Coverage is enforced locally by a `fail_under` gate and uploaded as a CI artifact. Revisit only if a judge asks for a badge. |
| `praxis doctor --json` | No machine consumer yet. Add when the Phase 11 demo script needs to assert on it. |
| Dependency vulnerability scanning | Four runtime dependencies, all first-party-maintained. Add before Phase 12 when the dependency surface is final. |
| A `CONTRIBUTING.md` | Single contributor. `CLAUDE.md` carries the conventions. Write one if the project is opened up. |

## Deferred from Phase 2

| Item | Why not now |
| ---- | ----------- |
| ~~The structured-output layer: Pydantic model → reduced JSON schema, parse, and the repair loop~~ | **Built in Phase 3**, by the first agent that needed one, exactly as this entry said it would be: `praxis/llm/structured.py`, tested against `MockProvider(malformed_share=...)`. Kept here because the reason it waited turned out to be right — the reduction is shaped by what the API actually rejects, and that was read from the docs rather than guessed. |
| Streaming responses | The seam makes one non-streaming call, capped at `MAX_TOKENS_WITHOUT_STREAMING`. Still true after Phase 3: `SegmenterAgent` windows a document into 40 blocks precisely so no single answer approaches the cap. Streaming changes the trace row's shape (one row per attempt becomes one row per attempt plus a stream of deltas). Add it with the first agent that needs an answer over 16k tokens. |
| Prompt caching (`cache_control` on the request) | The pricing multipliers are in `praxis/config/models.py` and `cost_of` already prices cache reads and writes, so turning it on cannot silently make the cost column wrong. Phase 3 produced the first stable prefix — `SegmenterAgent`'s system prompt is identical on every call — but one agent's system prompt is far below the minimum cacheable length, so turning it on would cost a cache write and buy nothing. Revisit when several agents share a long preamble. |

## Deferred from Phase 3

| Item | Why not now |
| ---- | ----------- |
| A `Finding` for a rejected citation | The pipeline reports refused spans in its result and logs one warning each; it does not write a record. `FindingKind` has no member for a fabricated citation, and the SQL `CHECK` constraints mirror that enum, so adding one is a migration — for a record nothing reads until the reporting layer exists. Build it with the first consumer, in Phase 9 or Phase 11. The evidence is already in the right shape: `SpanRejection` carries the defect and the detail a `Finding.prosecution` would quote. |
| Cross-document edges in the corpus ground truth | Item ids are corpus-global and `ExpectedLink` can already name any of them, so nothing structural is missing. The templates only assert edges inside one document because the interesting cross-document case — an ADR's assumption against a status update's estimate — is exactly what `FusionBridge` has to *infer*, and planting it as ground truth before Phase 8 exists would fix the answer before anyone has asked the question. Add it with the Phase 10 harness, once the metric it feeds is written. |
| Streaming or incremental ingestion | Unchanged from Phase 0's entry below, and now measured rather than assumed: the whole 12-document corpus ingests in one call per document. Revisit if a real integration lands. |
| Segmentation quality measurement | ADR 0011 assumption 1 (`segmenter_f1 - paragraph_floor_f1 >= 0.05`) cannot be evaluated without a grader, and the grader is Phase 10. The floor is built and the pipeline already runs on it whenever a model answers badly, so the ablation is a configuration change rather than a rewrite when the harness exists. |

## Deferred from Phase 6

| Item | Why not now |
| ---- | ----------- |
| ~~Cross-document outcome matching~~ **Done, Phase 8** | `praxis/agents/crossdoc.py`. The worry recorded here was that it would cost ADR 0015's *one offering, one document* invariant. It did not, because that invariant is about one **call** and not about one estimate: `CrossDocumentMatcher` runs the existing single-document matcher once per candidate document, so every listing is still one document's spans, `offering_of` still raises on anything else, and the added cost is calls rather than passages -- the same trade ADR 0015's own accepted-costs section makes for re-rendering a listing per window. A deterministic document selector and a budget of three documents per estimate keep that cost bounded, and the home document is read first so the common case costs exactly what it cost in Phase 6. `tests/agents/test_crossdoc.py` asserts the invariant against `offering_of` itself, so weakening it later is a visible deletion rather than a side effect. |
| A `Finding` for an unmatched estimate | An unmatched estimate is recorded as an `unresolved` `Outcome` (ADR 0022), which is a row Phase 7's query already sees and counts. Turning it into a `Finding` as well would be two records for one fact, and `FindingKind` has no member for it, so it is a migration for a record nothing reads until the reporting layer exists. Revisit in Phase 9 or Phase 11 with the first consumer, the same way the rejected-citation finding is being carried. |
| A record that a document was read and yielded nothing | `EstimationPipeline` skips a document only once it has *produced* an estimate, so one that yielded none is read again on every pass. `ExtractionPipeline` has had exactly this shape since Phase 4 for a document holding no decision, and the cause is the same: an append-only store records writes, and an attempt that wrote nothing leaves no trace to check. Fixing it means a new record or a column for "this agent has read this document", which is a migration for a fact nothing else consumes. Worth doing when a corpus is large enough that re-reading it is a cost somebody notices; measured, not guessed. |
| Calendar time against effort time in unit conversion | `converted` uses one stated convention — eight hours a day, five days a week — and writes it into `Outcome.notes` whenever it fires. A document saying "three weeks" may mean calendar weeks, and this reads it as fifteen working days. The `active` / `blocked` split is what limits the damage, since waiting time is meant to be recorded separately rather than folded into the effort figure. Revisit if a real corpus shows the two being confused; a second convention chosen without that evidence would be a guess with a constant in front of it. |

## Deferred from Phase 7

| Item | Why not now |
| ---- | ----------- |
| Reading `Estimate.conditions` when deciding what a miss means | ADR 0021 named this and ADR 0024 records that it is still true: an estimate 2x out because its scope tripled and one 2x out because the estimator is optimistic produce the same ratio, and only the second is a calibration signal. `Estimate.conditions` holds the material that would tell them apart and nothing reads it. Doing it needs a model — "did this condition hold" is a judgement about prose — which would put a generation step underneath a number the whole phase exists to keep arithmetic. The honest place for it is a separate component that annotates a row rather than one that adjusts a factor, and the first consumer is `ReviewTriageAgent`, now deferred to Phase 11 (see below). |
| A second explanation language | ADR 0025 assumption 1 fires on exactly this. `CalibratorAgent`'s explanation is a template precisely so it cannot restate a number wrongly, and translation is the one requirement that genuinely argues for a model — but it is a translation of a fixed sentence, not a description of a number, so it belongs in a different component with a different failure mode. Nothing in the competition scope needs it. |
| A reported factor below the precision floor | `REPORTED_PLACES` is four decimals, so a factor near `0.0001` has nothing underneath it and its band rounds up onto the centre however wide the sample scatters. Found by `hypothesis` and pinned by its own test rather than papered over. Left alone deliberately: more places would print precision the sample does not have, and refusing small factors would be a second threshold on magnitude with no story behind it. A factor of one ten-thousandth means the work took a ten-thousandth of the estimate, which is a data problem rather than a bias. |
| Whether `MINIMUM_SAMPLE` is five for an organisation that is not this one | ADR 0024 states five as a judgement rather than a derivation, and says so: a sample of seven outcomes cannot support fitting a threshold, and fitting one to it would be over-fitting with extra steps. `n` and `considered` are reported beside every factor so a reader who disagrees can re-derive without re-running anything. Revisit when a corpus exists with several estimators and more than one class above the threshold. |
| A second index for `work_class` without an owner | `estimate_owner_work_class` is `(owner, work_class)` and SQLite uses only a leading prefix, so narrowing by class alone falls back to a scan. Recorded by a test rather than repaired: both readers the index exists for — `BiasDetective` asking about one group, `FusionBridge` pricing one assumption — always carry the owner, and `BiasDetective` reads every group in one unnarrowed pass rather than one query per class. A second index would cost every estimate write to serve a CLI convenience. |

## Deferred from Phase 8

| Item | Why not now |
| ---- | ----------- |
| `ReviewTriageAgent` | One ranked queue over all three finding kinds. Named in `ARCHITECTURE.md`'s fusion box and deliberately not built in Phase 8: that phase is what first produces `STALE_DECISION` and `COLLATERAL_IMPACT` findings, so building the consumer alongside the producer would have meant designing a queue against no real queue. **Deferred again from Phase 9, and the reason has changed rather than repeated.** Phase 9 built the gate that decides what may *enter* a queue, and building the ranking in the same phase would have designed the consumer against a producer written the same week. It is also still the first consumer of `Estimate.conditions` (deferred from Phase 7), which needs a model to judge whether a condition held. Phase 11, with the reporting layer. Until then a reader queries three kinds separately, which ADR 0028 records as an accepted cost, and `praxis govern` reports the abstention split that a ranking would consume. |
| Keeping the cross-document walk going after a resolution | `CrossDocumentMatcher` stops at the first document that resolves an estimate. Continuing would let it notice two documents claiming different actuals, which is real — but it multiplies model spend on the common case (the home document answers) to buy a contradiction this corpus cannot produce, since the generator states an estimate and its actual in one document. Noticing that two *stored* records disagree is `ContradictionDetector`'s job over the store. Revisit when a real corpus shows the disagreement happening. |
| A rate beside the cross-document count | `praxis/eval/fusion.py` reports `cross_document` as a count and never a score, because the corpus plants no cross-document `estimated_as` edges as ground truth and planting them would fix the answer before anyone asked the question. A denominator would need a corpus somebody else wrote. |
| `FusionVerdict.UNDECIDED` becoming reachable | Currently unreachable through the bridge: `subject_of` accepts a predicate only when `constraints_of` yields exactly one name, and `constraints_of` names nothing for a division, a string comparison or a second identifier — so every predicate an evaluator could leave `UNKNOWN` is refused as `NO_SUBJECT` first. The guard is kept and tested through `moved` directly, and ADR 0026 assumption 4 expires the day `constraints_of` learns to name the subject of a compound predicate. Not repaired, because there is nothing broken. |
| A finding for a `RELIEVED` edge | Calibration excusing a predicate the raw estimate violated is reported and raises nothing. It is information, and it is not evidence the assumption is sound: the factor is a tendency across a class of work, not a measurement of this piece of it, and letting a track record of optimism argue an assumption out of trouble is the wrong direction for this product to be able to reason in. Revisit if a reviewer asks for it with a case. |
| A corrected estimate stored where a raw one is expected | Found by closing `OUT-0009`. `EST-0009` is the first dogfood estimate whose `active_quantity` is already bias-corrected, and `ScoringAgent`'s prequential walk assumes every row is raw -- its question is whether applying the correction *would have* helped. Handed the corrected figure it applies the factor twice and reports a working correction as harmful. Patched in the log by promoting the raw figure to `raw_active_quantity`, which `dogfood_rows` prefers. **Not** patched in `praxis/`, and deliberately: the product does not have this bug. `CalibratorAgent` is a pass-through that explains (ADR 0025) and `calibrate_store` writes findings rather than revised estimates, so no corrected estimate is ever stored back into the history it was computed from. If that ever changes -- if a phase decides to persist a calibrated estimate -- `Estimate` needs a raw column and `praxis.store.reports.calibration_history` needs to select it, or every factor in the system starts decaying toward one. |

## Deferred from Phase 9

| Item | Why not now |
| ---- | ----------- |
| A `Finding` for a rejected citation, and one for an unmatched estimate | Carried from Phases 3 and 6, and **the reason has finally changed**: Phase 9 built the consumer those entries were waiting for. `AbstentionGate` now routes findings, so a rejected-citation finding would have somewhere to go. It is still not built, for the reason that has not changed: `FindingKind` has no member for either, the SQL `CHECK` constraints mirror that enum, and adding one is a migration. Phase 9 shipped zero migrations by design and would not have spent that on two record kinds nothing reads until the reporting layer exists. Phase 11, with `ReporterAgent`. |
| A second challenging pass over findings the first left undecided | An undecided finding is the one thing `ChallengerAgent` re-argues, so a second pass happens naturally on the next run and costs a call. Making it a loop *inside* one pass would spend a `reason`-tier call per finding per attempt to move a verdict the confidence floor already declined, and the floor is not noise -- it is the agent saying the argument is genuinely close. `AbstentionGate` routes those to a person, which is the correct destination for something nobody was willing to decide. Revisit if a real run shows a large undecided population that a second look would settle. |
| Curating anything other than an `Assumption` | `CuratorAgent` reads assumptions only. Decisions accumulate too, and a superseded decision is exactly what `LinkType.SUPERSEDES` was defined for -- the ADR convention in the corpus is about decisions, not assumptions. Not built because the two rules that fire here do not transfer: a decision has no predicate, so the duplicate rule has nothing to compare, and `ContradictionDetector` blocks decisions on prose alone. A decision curator needs its own rule, and inventing one against a corpus with nine decisions would be fitting to noise. |
| A concede rate that means something offline | `MockProvider` answers a bare boolean true seven times in ten and synthesises about two array entries per call whatever the batch holds, so an offline run argues a minority of findings and concedes at a rate fixed by `_TRUE_BIAS`. **Deliberately not repaired.** Making the mock answer about every ordinal would make it a better challenger than it is a model, and every other agent's offline numbers would then be measuring a mock that had been special-cased for one of them. ADR 0030 assumptions 1 and 2 state the band a *real* run has to land in, and that is where the number gets earned. |
| `IDLE_DAYS` fitted to anything | Sixty days is a judgement stated as one, the way ADR 0024 states `MINIMUM_SAMPLE = 5`. There is no data to fit it to: this project has one corpus and generated it. It is reported beside every retirement so a reader who disagrees can re-derive without re-running anything. Revisit when a real corpus exists with assumptions old enough to disagree about. |

## Deferred from Phase 10

| Item | Why not now |
| ---- | ----------- |
| Recorded fixtures replacing the coherently-citing mock | The honest way to get real citations offline is `ReplayProvider` over fixtures recorded from a live run, and [ADR 0034](docs/adr/0034-the-eval-harness-runs-a-coherently-citing-mock.md) rejects it for one reason: recording them needs a credential, and invariant 1 says nothing may. A fixture set nobody without a key can regenerate rots the first time a prompt version moves. Revisit the day this project has a funded key — the recording machinery already exists. |
| An ablation rung per prompt version | The table ablates *layers*. Ablating prompt versions is the other axis and it is the one ADR 0014 built the version column for, but with one version of each prompt there is nothing to compare. Revisit when a prompt reaches v2 for a reason other than a typo. |
| Grading the corpus's clean controls against a live provider | The controls make a hallucination rate measurable, and offline the number they produce is a property of the mock. Nothing to do about that here; the number becomes evidence on the first live run, which is where ADR 0034 assumption 3 expires. |

## Deferred by design (revisit with data, not opinion)

| Item | Why not now |
| ---- | ----------- |
| Dynamic model routing by measured input difficulty | Would make cost non-deterministic and break the reproducibility the ablation table depends on (ADR 0004, ADR 0006). Revisit once Phase 10 reports real per-role token counts. |
| Caching identical LLM calls across eval runs | Premature before there is a measurement showing the eval harness is slow. Deterministic replay already covers the correctness case. |
| Incremental / streaming ingestion | Phase 3 handles a fixed local corpus. Streaming matters only if a real integration lands, which is out of scope for the competition. |
| Multi-user or multi-tenant storage | Single-writer is an explicit assumption in ADR 0003. Adding tenancy now would complicate every query for a requirement nobody has. |
| Anything beyond markdown, txt and json as a source | Email, chat exports, transcripts and issue trackers are named in the architecture as later adapters. The `SourceAdapter` protocol exists and has three implementations, so a fourth is additive — and ADR 0011 assumption 2 is written to fire if a new source kind turns out to need its own block rules. |

## Known risks being carried

| Risk | Current stance |
| ---- | -------------- |
| The gap between mock-realistic and real model behaviour is unmeasured | ADR 0005 assumption 3. The first live run is set up to be a measurement rather than a surprise. |
| `DecisionScout` on the cheapest tier may not have adequate recall | ADR 0006 assumption 2. The fix is a one-line re-tier; the point is to find out from the eval harness rather than guess now. |
| Hand-written predicates may not parse once the Phase 5 DSL exists | ADR 0001 assumption 1. A small fixed set of files to migrate, and a finding the system is designed to surface. |
