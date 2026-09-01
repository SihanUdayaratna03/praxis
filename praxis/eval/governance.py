"""Grading the governance layer, which is mostly grading what it refused to conclude.

The sixth quarter of the metrics table, and it has the awkward property the
fourth and fifth both have — **most of it has no answer key** — plus one the
others do not.

## Three rates, and only one of them is a quality score

- **Concede rate.** How often the challenger defeated a finding it was arguing
  against. A rate near zero is a rubber stamp and a rate near one is a silencer;
  both are broken and neither is visible from the output alone.
- **Retirement rate.** What share of live assumptions the curator would
  withdraw. Not a target in either direction: a store of young assumptions
  retires nothing and is healthy, and a store of abandoned ones retires many and
  is also being handled correctly.
- **Abstention rate.** How often the gate declined to conclude. **Reported
  plainly and never driven toward zero**, the stance this project has taken on
  every refusal since `BiasDetective` declined to speak below `n = 5`.

## The concede rate cannot be earned offline, and that is stated rather than worked around

`praxis.llm.synthesis` answers a bare boolean true seventy per cent of the time
(`_TRUE_BIAS`), so an offline concede rate measures schema synthesis and not
reasoning. `mock_provider` records whether the run that produced the number was
against the mock, so a report can say whose number it is on the line where it
appears — and `decided` is carried beside the rate so a rate over a sample of
one cannot be read as a rate.

**No response schema was reshaped to move it.** `Judgement.upheld` mirrors
`Verdict`'s own polarity because the field should follow the record it is
written into. See ADR 0030's assumptions 1 and 2, which state the band a real
run has to land in.

## What can be checked without an answer key

Three internal claims, each tested in both directions because a claim of the
form "every X has a reason" is satisfied trivially by a system that produces no
X at all:

1. `verdicts_hold` — no finding carries a verdict without the challenge that
   reached it. `Finding`'s own validator refuses that from the other side, so a
   failure here means a record got into the store some other way.
2. `retirements_hold` — nothing retired had a live decision resting on it, and
   nothing retired was deleted. Both halves matter: the first is ADR 0031
   assumption 5, the second is invariant 7.
3. `abstentions_hold` — every emitted finding really clears all four rules, and
   every abstention really fails one. This is the gate's whole claim as an
   assertion.

## The one thing the corpus *can* grade

`supersedes` edges. Phase 9 plants them on the revision notes, where the
document states in words that an earlier assumption no longer stands, so
`merges_found` against `merges_expected` is a real recall against a real answer
key — the only number in this module that is a comparison against truth.

Nothing here takes a run. Everything is recomputed from the store, because a
grade taken from a pass's own return value reports what the agents said rather
than what survived being written.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from praxis.agents.abstention import AbstentionGate, Disposition, GateResult
from praxis.agents.curator import CurationResult, CuratorAgent, Refusal
from praxis.domain.enums import Verdict
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Finding, Link
from praxis.eval.metrics import RATE_PLACES
from praxis.store.repository import Repository


@dataclass(frozen=True, slots=True)
class GovernanceScore:
    """What the governance layer concluded, and what of it can be checked.

    Attributes:
        findings: Findings standing in the store. The denominator the challenge
            numbers are read against.
        challenged: Findings carrying a recorded challenge.
        decided: Findings carrying a verdict. The concede rate's denominator,
            and **not** `challenged` -- a challenge that reached no verdict is a
            real outcome and counting it as a non-concession would let an agent
            that never commits read as one that never concedes.
        conceded: Findings the challenger overturned.
        mock_provider: Whether the run behind these numbers used the mock. When
            true the concede rate is a property of `praxis.llm.synthesis` and
            not evidence about the challenger, and a report must say so.
        assumptions: Live assumptions the curator considered.
        merges: Pairs it would collapse.
        merges_expected: `supersedes` edges the corpus labels. **The only
            answer key in this module.**
        retirements: Idle assumptions it would withdraw.
        kept_under_a_decision: Idle assumptions it refused to withdraw because a
            live decision rests on them. Reported apart from the other refusals
            because it is the one that is a finding rather than housekeeping.
        emitted: Findings the gate concluded on.
        abstained: Findings it routed to a person instead.
        abstained_with_cause: Abstentions that really fail at least one rule.
            The numerator of `abstention_precision`.
        by_insufficiency: How many abstentions each rule caused. A finding
            failing two rules contributes to both, so these sum to at least the
            abstention count.
        verdicts_hold: Whether every verdict in the store carries the challenge
            that reached it.
        retirements_hold: Whether every retirement is free of live dependants
            and every retracted record is still readable.
        abstentions_hold: Whether the gate's own rules agree with its verdicts,
            in both directions.
    """

    findings: int = 0
    challenged: int = 0
    decided: int = 0
    conceded: int = 0
    mock_provider: bool = True
    assumptions: int = 0
    merges: int = 0
    merges_expected: int = 0
    retirements: int = 0
    kept_under_a_decision: int = 0
    emitted: int = 0
    abstained: int = 0
    abstained_with_cause: int = 0
    by_insufficiency: Mapping[str, int] = field(default_factory=dict)
    verdicts_hold: bool = True
    retirements_hold: bool = True
    abstentions_hold: bool = True

    @property
    def concede_rate(self) -> Decimal:
        """Share of decided challenges that overturned the finding.

        Zero when nothing was decided, which is **no measurement** rather than a
        challenger that never concedes. `decided` is reported beside it so the
        two cannot be confused, and against the mock the number is a property of
        schema synthesis either way.
        """
        if not self.decided:
            return Decimal(0)
        return (Decimal(self.conceded) / Decimal(self.decided)).quantize(RATE_PLACES)

    @property
    def abstention_precision(self) -> Decimal:
        """Share of abstentions that really fail a rule. The brief's metric.

        Zero when nothing abstained, which is no measurement -- `abstained` is
        reported beside it. `abstentions_hold` is the same claim as a boolean,
        and this is the number that says by how much it missed.
        """
        if not self.abstained:
            return Decimal(0)
        return (Decimal(self.abstained_with_cause) / Decimal(self.abstained)).quantize(RATE_PLACES)

    @property
    def retirement_rate(self) -> Decimal:
        """Share of live assumptions the curator would withdraw.

        Not a quality score in either direction, and worth saying because the
        number invites being read as one. A store of assumptions written this
        month retires nothing and is healthy; a store somebody abandoned retires
        many and is being handled correctly. ADR 0031 assumption 4 puts a ceiling
        on it -- above a half is demolition rather than curation -- and that is
        the only reading it supports.
        """
        if not self.assumptions:
            return Decimal(0)
        return (Decimal(self.retirements) / Decimal(self.assumptions)).quantize(RATE_PLACES)

    @property
    def abstention_rate(self) -> Decimal:
        """Share of emittable findings on which no conclusion was reached.

        The denominator is emitted plus abstained, and deliberately not every
        finding: one the challenger overturned was never a candidate for
        emission, so counting it would make this fall whenever the concede rate
        rose. Two different components, two different numbers.
        """
        considered = self.emitted + self.abstained
        if not considered:
            return Decimal(0)
        return (Decimal(self.abstained) / Decimal(considered)).quantize(RATE_PLACES)

    @property
    def merge_recall(self) -> Decimal:
        """Of the `supersedes` edges the corpus labels, how many were proposed.

        The only comparison against an answer key here. Zero over zero is
        reported as zero and means the corpus planted none, which is what a
        corpus generated with `--revisions 0` does.
        """
        if not self.merges_expected:
            return Decimal(0)
        return (Decimal(self.merges) / Decimal(self.merges_expected)).quantize(RATE_PLACES)

    @property
    def claims_hold(self) -> bool:
        """Whether all three internal claims survived. One line for a report."""
        return self.verdicts_hold and self.retirements_hold and self.abstentions_hold


def grade_governance(
    repository: Repository, *, merges_expected: int = 0, mock_provider: bool = True
) -> GovernanceScore:
    """Grade what the governance layer concluded, recomputed from the store.

    Args:
        repository: The store, after a governance pass.
        merges_expected: `supersedes` edges the corpus labels, from the answer
            key. Passed in rather than read here, because this module grades a
            store and the answer key belongs to the harness that loaded it.
        mock_provider: Whether the run used the mock. Carried into the score so
            a report can say whose number the concede rate is.

    Returns:
        The score, populated even for an empty store.
    """
    findings = repository.list_all(Finding)
    curation = CuratorAgent(repository).curate(at=datetime.now(UTC))
    gate = AbstentionGate().route_all(findings, withdrawn=_retracted_assumptions(repository))
    return GovernanceScore(
        findings=len(findings),
        challenged=sum(1 for finding in findings if finding.challenge is not None),
        decided=sum(1 for finding in findings if finding.verdict is not Verdict.UNDECIDED),
        conceded=sum(1 for finding in findings if finding.verdict is Verdict.OVERTURNED),
        mock_provider=mock_provider,
        assumptions=curation.considered,
        merges=len(curation.merges) + _standing_merges(repository),
        merges_expected=merges_expected,
        retirements=len(curation.retirements),
        kept_under_a_decision=sum(
            1 for entry in curation.declined if entry.refusal is Refusal.RESTS_ON_A_DECISION
        ),
        emitted=len(gate.emitted),
        abstained=len(gate.abstained),
        abstained_with_cause=sum(1 for routing in gate.abstained if routing.insufficiencies),
        by_insufficiency={
            insufficiency.value: count
            for insufficiency, count in gate.by_insufficiency().items()
            if count
        },
        verdicts_hold=all(_verdict_holds(finding) for finding in findings),
        retirements_hold=_retirements_hold(repository, curation),
        abstentions_hold=_abstentions_hold(gate, findings),
    )


def _verdict_holds(finding: Finding) -> bool:
    """Whether a verdict carries the challenge that reached it.

    Both directions. `Finding`'s validator refuses a verdict with no challenge,
    so the interesting half is the other one: a challenge with no verdict is
    *legitimate* -- it is what the confidence floor produces -- and a grader that
    treated it as a defect would report the floor working as the store broken.
    """
    if finding.verdict is Verdict.UNDECIDED:
        return True
    return finding.challenge is not None and bool(finding.challenge.strip())


def _retirements_hold(repository: Repository, curation: CurationResult) -> bool:
    """Whether every retirement was free to make, and nothing was deleted.

    Two claims, and they fail differently. ADR 0031 assumption 5 says nothing a
    live decision rests on is ever retired; invariant 7 says a withdrawn record
    stays readable. A curator that deleted its retirements would satisfy the
    first and violate the second, which is why both are checked.
    """
    for retirement in curation.retirements:
        if _resting_on(repository, retirement.assumption.id):
            return False
    return all(
        repository.get(Assumption, record.id, version=1) is not None
        for record in repository.list_all(Assumption, include_retracted=True)
    )


def _abstentions_hold(gate: GateResult, findings: tuple[Finding, ...]) -> bool:
    """Whether the gate's dispositions agree with its own rules, both ways.

    An emitted finding must fail no rule and an abstained one must fail at
    least one. Stating only the first would pass against a gate that abstained
    on everything, which is the control discipline every property in
    `tests/agents/` carries.
    """
    by_id: dict[str, Finding] = {finding.id: finding for finding in findings}
    for routing in gate.routings:
        if routing.disposition is Disposition.EMIT and routing.insufficiencies:
            return False
        if routing.disposition is Disposition.NEEDS_HUMAN and not routing.insufficiencies:
            return False
        if routing.disposition is Disposition.DROPPED:
            finding = by_id.get(routing.finding_id)
            if finding is None or finding.verdict is not Verdict.OVERTURNED:
                return False
    return True


def _resting_on(repository: Repository, assumption_id: str) -> bool:
    """Whether any live decision rests on this assumption."""
    edges = repository.links_to(assumption_id, types=(LinkType.ASSUMES,))
    found = (repository.get(Decision, edge.source_id) for edge in edges)
    return any(record is not None and not record.retracted for record in found)


def _standing_merges(repository: Repository) -> int:
    """`supersedes` edges already in the store, from an earlier governance pass.

    Counted alongside the merges a fresh curation proposes, because the curator
    refuses a pair whose edge already stands -- so grading the proposals alone
    would score a governed store at zero and an ungoverned one at full marks,
    which is the metric upside down.
    """
    return sum(
        1
        for link in repository.list_all(Link)
        if link.link_type is LinkType.SUPERSEDES and not link.retracted
    )


def _retracted_assumptions(repository: Repository) -> list[str]:
    """Ids of assumptions that have been withdrawn.

    Read with `include_retracted` and filtered, rather than by differencing two
    listings: the second form would report an assumption as live if it were
    missing from both, and a missing record is a different failure.
    """
    return [
        record.id
        for record in repository.list_all(Assumption, include_retracted=True)
        if record.retracted
    ]
