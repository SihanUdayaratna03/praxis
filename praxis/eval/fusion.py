"""Grading the fusion layer, which is mostly grading what it refused to say.

The fifth quarter of the metrics table, and it has the same awkward property as
the fourth: **most of it has no answer key**. A corpus can state that an
assumption is an estimate in disguise -- `praxis.corpus.templates` plants
`estimated_as` edges as ground truth and `praxis.eval.harness` already grades
recall on them. What a corpus cannot state is whether a *calibrated* number
violates a predicate, because that depends on the estimator's accumulated
history rather than on anything a document says.

So this grades three things, and only the first is a comparison against truth:

1. **The edges the corpus planted are the edges that get priced.** That number
   is `fusion_recall`, which `praxis.eval.harness` has reported since Phase 4.
   This module does not recompute it and does not add a second one.
2. **The refusals are correct.** Every verdict that declined to speak declined
   for a reason the store can be checked against -- below the threshold, no work
   class, no single subject. `refusals_hold` is false if any edge reported a
   factor for a group `BiasDetective` refuses, which would mean the threshold
   had been bypassed on the way through the bridge.
3. **A flip really is a flip.** Every `FLIPPED` result must carry a raw
   evaluation that held and a calibrated one that is violated. `flips_hold` is
   the phase's central claim as an assertion: a projection filed as a finding
   when the predicate never moved is the one output that would make the whole
   layer noise.

**The cross-document count has no answer key at all and is reported anyway.**
`BACKLOG.md` says planting cross-document `estimated_as` edges in the corpus
would fix the answer before anyone asked the question, so `cross_document` is a
count and never a score -- the same treatment Phase 7 gave the calibration
quarter, and for the same reason.

Nothing here takes a run. Everything is recomputed from the store, because a
grade taken from a pass's own return value reports what the agents said rather
than what survived being written.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from praxis.agents.collateral import CollateralAgent
from praxis.agents.fusion import FusionBridge, FusionVerdict, PricedAssumption
from praxis.domain.enums import FindingKind
from praxis.domain.records import Finding, Span
from praxis.store.repository import Repository

RATE_PLACES = Decimal("0.0001")
"""Four places, as every other rate in `praxis.eval` uses, so two numbers in one
table cannot disagree by rounding."""


@dataclass(frozen=True, slots=True)
class FusionScore:
    """What the fusion layer concluded, and what of it can be checked.

    Attributes:
        priced: `estimated_as` edges walked. The denominator for everything
            else here.
        by_verdict: How many edges landed on each verdict. The refusals are
            most of this and are the point of it, exactly as in the calibration
            quarter.
        flips: Edges where calibration turned a satisfied predicate into a
            violated one. The output the phase exists to produce.
        refusals_hold: Whether every edge that declined to speak declined for a
            reason visible in the store. False means the threshold was bypassed.
        flips_hold: Whether every flip really carries a held raw evaluation and
            a violated calibrated one. False is the phase's central claim
            failing.
        cross_document: Priced edges whose assumption and estimate were read
            from different documents. **A count, never a score** -- the corpus
            deliberately plants no cross-document edges as ground truth.
        misses: Estimates an outcome showed to have missed.
        damaging: Those with at least one decision resting on them.
        stale_findings: `STALE_DECISION` findings standing in the store.
        collateral_findings: `COLLATERAL_IMPACT` findings standing in the store.
    """

    priced: int = 0
    by_verdict: Mapping[str, int] = field(default_factory=dict)
    flips: int = 0
    refusals_hold: bool = True
    flips_hold: bool = True
    cross_document: int = 0
    misses: int = 0
    damaging: int = 0
    stale_findings: int = 0
    collateral_findings: int = 0

    @property
    def refused(self) -> int:
        """Edges that said nothing. The ordinary outcome, and usually all of them."""
        return self.priced - self.flips

    @property
    def flip_rate(self) -> Decimal:
        """Share of priced edges that produced a finding.

        Not a quality score in either direction, and worth stating because the
        number invites being read as one. A low rate means the store's groups
        are young or its estimators are consistent, both facts about the corpus.
        A *high* rate on a small corpus would be the suspicious reading, not the
        good one -- it would suggest the threshold was not being enforced.
        """
        if not self.priced:
            return Decimal(0)
        return (Decimal(self.flips) / Decimal(self.priced)).quantize(RATE_PLACES)

    @property
    def damaging_rate(self) -> Decimal:
        """Share of misses that something actually rested on.

        ADR 0027 assumption 3 predicts this stays at or below one half. It is
        reported so that assumption can expire against a number rather than an
        impression.
        """
        if not self.misses:
            return Decimal(0)
        return (Decimal(self.damaging) / Decimal(self.misses)).quantize(RATE_PLACES)


def grade_fusion(repository: Repository) -> FusionScore:
    """Grade what the fusion layer concluded, recomputed from the store.

    Args:
        repository: The store, after a fusion pass.

    Returns:
        The score, populated even for an empty store.
    """
    priced = FusionBridge(repository).price_all(now=datetime.now(UTC))
    damages = CollateralAgent(repository).survey()
    findings = repository.list_all(Finding)
    return FusionScore(
        priced=len(priced),
        by_verdict=_by_verdict(priced),
        flips=sum(1 for result in priced if result.flips),
        refusals_hold=all(_refusal_holds(result) for result in priced),
        flips_hold=all(_flip_holds(result) for result in priced),
        cross_document=sum(1 for result in priced if _crosses_documents(repository, result)),
        misses=len(damages),
        damaging=sum(1 for damage in damages if damage.damaging),
        stale_findings=_counted(findings, FindingKind.STALE_DECISION),
        collateral_findings=_counted(findings, FindingKind.COLLATERAL_IMPACT),
    )


def _refusal_holds(result: PricedAssumption) -> bool:
    """Whether an edge that said nothing had a reason to, and one that spoke had a factor.

    Checked in both directions, which is the lesson Phase 7 recorded about
    refusal properties: "every refusal has a reason" is satisfied trivially by a
    bridge that refuses everything, so the speaking case has to be constrained
    too.
    """
    if result.verdict is FusionVerdict.NO_FACTOR:
        return result.factor is not None and not result.factor.speaks
    if result.priced:
        return result.factor is not None and result.factor.speaks
    return bool(result.reason)


def _flip_holds(result: PricedAssumption) -> bool:
    """Whether a flip is really a flip: raw held, calibrated violated.

    The phase's central claim, as an assertion over whatever the store holds. An
    `UNKNOWN` on either side must never have been reported as a flip, which is
    the guard `praxis.agents.fusion.moved` exists for.
    """
    if not result.flips:
        return True
    before, after = result.raw_evaluation, result.calibrated_evaluation
    return before is not None and after is not None and before.holds and after.violated


def _crosses_documents(repository: Repository, result: PricedAssumption) -> bool:
    """Whether this edge's two ends were read from different documents.

    Counted and never scored. The corpus states an assumption and its estimate
    in one document on purpose -- planting the cross-document case as ground
    truth would fix the answer before anyone asked the question -- so a number
    here is evidence about a real corpus and nothing about this one.
    """
    if result.estimate is None:
        # A withdrawn estimate has no span to compare against. Not counted
        # either way: it is neither same-document nor cross-document, and
        # guessing would put a number in a column that has no answer key.
        return False
    # `require` rather than `get`: both records carry a `SpanRef` and the store's
    # foreign keys make an unresolvable one impossible, so a `None` here would be
    # a corrupt store rather than a missing span. Raising says that; returning
    # `False` would quietly report "not cross-document" about a store that can no
    # longer answer the question.
    assumption_span = repository.require(Span, result.assumption.span_id)
    estimate_span = repository.require(Span, result.estimate.span_id)
    return assumption_span.doc_id != estimate_span.doc_id


def _by_verdict(priced: tuple[PricedAssumption, ...]) -> dict[str, int]:
    """How many edges landed on each verdict, in the enum's own order.

    The enum's order rather than by count, so two runs over different stores
    produce rows a reader can compare down the page.
    """
    counted = {verdict.value: 0 for verdict in FusionVerdict}
    for result in priced:
        counted[result.verdict.value] += 1
    return {verdict: count for verdict, count in counted.items() if count}


def _counted(findings: tuple[Finding, ...], kind: FindingKind) -> int:
    """Standing findings of one kind."""
    return sum(1 for finding in findings if finding.kind is kind)
