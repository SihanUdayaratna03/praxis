"""AbstentionGate: what is concluded, and the far more interesting list of what is not.

Three claims run through this file.

The first is that **abstention is the output**. Every other component reports
its refusals beside its results; this one is made of them, so the tests treat a
`NEEDS_HUMAN` as a success and the classes are arranged accordingly.

The second is that **every rule is evaluated, not just the first**. A finding
with two defects reports two. A gate that short-circuited would send a person to
fix one problem and then send them straight back, and that is invisible from the
outside unless a test looks for it.

The third is that **each rule carries a control**, Phase 7's lesson: a test
asserting the gate abstains passes trivially against a gate that abstains on
everything, so each rule is paired with the same finding one field away, where
the gate must conclude.
"""

from __future__ import annotations

import pytest
from praxis.agents.abstention import (
    DEFAULT_CONFIDENCE_FLOOR,
    GATE_NAME,
    QUOTING_KINDS,
    AbstentionGate,
    Disposition,
    Insufficiency,
)
from praxis.config.models import NON_LLM_AGENTS, UnknownAgentError, role_for_agent, routed_agents
from praxis.domain.enums import FindingKind, RecordKind, Severity, Verdict
from praxis.domain.records import Finding

from tests.agents.test_challenger import AT


def make_finding(  # noqa: PLR0913 -- every field a test varies, named
    number: int = 1,
    *,
    kind: FindingKind = FindingKind.ASSUMPTION_BREACH,
    verdict: Verdict = Verdict.UPHELD,
    confidence: float = 0.8,
    spans: tuple[str, ...] = ("SPAN-cfaded768e68fc08",),
    severity: Severity = Severity.MEDIUM,
) -> Finding:
    """A finding that would be emitted, unless the test moves one field.

    Defaults to the *passing* case on purpose. Every abstention test below is
    then one named change away from it, which makes each test say exactly which
    rule it is about.
    """
    return Finding(
        id=f"F-{number:04d}",
        kind=kind,
        subject_kind=RecordKind.ASSUMPTION,
        subject_id=f"A-{number:04d}",
        prosecution=f"A-{number:04d} assumed under 50 GB; it is measured at 61 GB.",
        challenge="the quoted figure is current and about this index",
        verdict=verdict,
        severity=severity,
        confidence=confidence,
        evidence_span_ids=spans,
        detected_at=AT,
        created_by="AssumptionMonitor",
        created_at=AT,
    )


class TestWhatIsConcluded:
    """The one path that ends in a conclusion."""

    def test_an_upheld_evidenced_confident_finding_is_emitted(self) -> None:
        routing = AbstentionGate().route(make_finding())

        assert routing.disposition is Disposition.EMIT
        assert not routing.insufficiencies
        assert not routing.abstained

    def test_a_computed_finding_with_no_spans_is_still_emitted(self) -> None:
        """`evidence_span_ids` is documented as legitimately empty for these.

        A calibration factor is not something a document says, so requiring a
        citation would abstain on every fusion finding the product exists to
        produce. The rule is per kind, and this is the half that makes it worth
        having a rule rather than a blanket check.
        """
        routing = AbstentionGate().route(make_finding(kind=FindingKind.STALE_DECISION, spans=()))

        assert routing.disposition is Disposition.EMIT

    def test_routing_is_a_pure_function_of_its_arguments(self) -> None:
        """Nothing is stored, so two calls cannot disagree. ADR 0032."""
        gate, finding = AbstentionGate(), make_finding()

        assert gate.route(finding) == gate.route(finding)


class TestWhatIsWithheld:
    """One class per rule, each with the control that gives it meaning."""

    def test_a_finding_nothing_challenged_is_not_concluded(self) -> None:
        """`ARCHITECTURE.md` since Phase 0: nothing reaches a human unchallenged."""
        routing = AbstentionGate().route(make_finding(verdict=Verdict.UNDECIDED))

        assert routing.disposition is Disposition.NEEDS_HUMAN
        assert routing.insufficiencies == (Insufficiency.NEVER_CHALLENGED,)

    def test_the_same_finding_once_challenged_is_concluded(self) -> None:
        """The control for the never-challenged rule."""
        assert AbstentionGate().route(make_finding()).disposition is Disposition.EMIT

    def test_a_finding_below_the_confidence_floor_is_not_concluded(self) -> None:
        """Surviving a challenge does not make an unconfident finding confident."""
        routing = AbstentionGate().route(make_finding(confidence=DEFAULT_CONFIDENCE_FLOOR - 0.01))

        assert routing.insufficiencies == (Insufficiency.LOW_CONFIDENCE,)

    def test_a_finding_exactly_at_the_floor_is_concluded(self) -> None:
        """The control, at the boundary rather than far from it."""
        routing = AbstentionGate().route(make_finding(confidence=DEFAULT_CONFIDENCE_FLOOR))

        assert routing.disposition is Disposition.EMIT

    def test_a_quoting_finding_that_quotes_nothing_is_not_concluded(self) -> None:
        """It alleges a document says something and cites no span that says it."""
        routing = AbstentionGate().route(make_finding(kind=FindingKind.CONTRADICTION, spans=()))

        assert routing.insufficiencies == (Insufficiency.UNCITED,)

    def test_the_same_quoting_finding_with_a_span_is_concluded(self) -> None:
        """The control for the citation rule."""
        routing = AbstentionGate().route(make_finding(kind=FindingKind.CONTRADICTION))

        assert routing.disposition is Disposition.EMIT

    def test_a_finding_about_a_withdrawn_record_is_not_concluded(self) -> None:
        """`Repository.get` returns a retracted record rather than `None`.

        Which is why this is a check and not an absence: a finding about a
        withdrawn assumption looks exactly like a finding about a live one from
        inside the record.
        """
        routing = AbstentionGate().route(make_finding(), subject_withdrawn=True)

        assert routing.insufficiencies == (Insufficiency.SUBJECT_WITHDRAWN,)

    def test_the_same_finding_about_a_live_record_is_concluded(self) -> None:
        """The control for the withdrawn-subject rule."""
        routing = AbstentionGate().route(make_finding(), subject_withdrawn=False)

        assert routing.disposition is Disposition.EMIT

    def test_every_failed_rule_is_reported_and_not_only_the_first(self) -> None:
        """One round trip per defect is the cost of short-circuiting here.

        A person told a finding is uncited fixes the citation and is sent
        straight back because it was also about a withdrawn record.
        """
        routing = AbstentionGate().route(
            make_finding(kind=FindingKind.CONTRADICTION, verdict=Verdict.UNDECIDED, spans=()),
            subject_withdrawn=True,
        )

        assert routing.insufficiencies == (
            Insufficiency.NEVER_CHALLENGED,
            Insufficiency.UNCITED,
            Insufficiency.SUBJECT_WITHDRAWN,
        )

    def test_the_reason_says_what_would_answer_the_question(self) -> None:
        """A person is being asked, so the sentence has to name the gap."""
        routing = AbstentionGate().route(make_finding(confidence=0.2))

        assert "0.2" in routing.reason
        assert str(DEFAULT_CONFIDENCE_FLOOR) in routing.reason


class TestWhatIsDropped:
    """The challenger's concessions, which are not abstentions."""

    def test_an_overturned_finding_is_dropped_rather_than_questioned(self) -> None:
        """Nothing to emit and nothing to ask about -- the argument settled it."""
        routing = AbstentionGate().route(make_finding(verdict=Verdict.OVERTURNED))

        assert routing.disposition is Disposition.DROPPED
        assert not routing.abstained

    def test_an_overturned_finding_is_dropped_even_when_its_evidence_is_thin(self) -> None:
        """The challenger already decided it, so the evidence rules never run.

        A gate that abstained here would put a finding a person has effectively
        already dismissed back in front of them as a question.
        """
        routing = AbstentionGate().route(
            make_finding(verdict=Verdict.OVERTURNED, confidence=0.1, spans=()),
            subject_withdrawn=True,
        )

        assert routing.disposition is Disposition.DROPPED
        assert not routing.insufficiencies

    def test_the_drop_carries_the_argument_that_defeated_it(self) -> None:
        routing = AbstentionGate().route(make_finding(verdict=Verdict.OVERTURNED))

        assert "the quoted figure is current" in routing.reason


class TestTheRates:
    """The numbers the phase report prints, and their denominators."""

    def test_a_dropped_finding_is_not_in_the_abstention_denominator(self) -> None:
        """Otherwise the rate falls whenever the challenger concedes more.

        That would be one number moving another, and the two measure different
        components.
        """
        result = AbstentionGate().route_all(
            [
                make_finding(1),
                make_finding(2, verdict=Verdict.UNDECIDED),
                make_finding(3, verdict=Verdict.OVERTURNED),
            ]
        )

        assert result.considered == 2
        assert result.abstention_rate == pytest.approx(0.5)
        assert len(result.dropped) == 1

    def test_no_findings_reports_no_rate_rather_than_a_confident_pipeline(self) -> None:
        result = AbstentionGate().route_all([])

        assert result.considered == 0
        assert result.abstention_rate == 0.0

    def test_a_finding_failing_two_rules_is_counted_under_both(self) -> None:
        """ "How often does this rule fire" is the tuning question, not "who was
        blamed", so the counts sum to more than the abstentions."""
        result = AbstentionGate().route_all(
            [make_finding(1, verdict=Verdict.UNDECIDED, confidence=0.1)]
        )
        counted = result.by_insufficiency()

        assert len(result.abstained) == 1
        assert counted[Insufficiency.NEVER_CHALLENGED] == 1
        assert counted[Insufficiency.LOW_CONFIDENCE] == 1

    def test_withdrawn_subjects_are_resolved_once_for_the_whole_set(self) -> None:
        """A caller holding a store looks them up once rather than per finding."""
        result = AbstentionGate().route_all(
            [make_finding(1), make_finding(2)], withdrawn=["A-0002"]
        )

        assert [routing.disposition for routing in result.routings] == [
            Disposition.EMIT,
            Disposition.NEEDS_HUMAN,
        ]

    def test_routing_a_set_is_the_same_as_routing_each_of_it(self) -> None:
        """No cross-finding state, so a batch cannot decide differently."""
        gate = AbstentionGate()
        findings = [make_finding(1), make_finding(2, confidence=0.1)]

        assert gate.route_all(findings).routings == tuple(
            gate.route(finding) for finding in findings
        )


class TestTheGatesPlaceInTheSystem:
    """Where it sits in the routing table, and what it refuses to be given."""

    def test_the_gate_makes_no_model_call(self) -> None:
        """ADR 0032. Ninth consecutive component on the arithmetic side, and the
        easiest of them to argue: its whole input is two numbers and three
        enums."""
        assert GATE_NAME in NON_LLM_AGENTS
        assert GATE_NAME not in routed_agents()

    def test_asking_for_its_route_raises_rather_than_returning_one(self) -> None:
        with pytest.raises(UnknownAgentError, match="deterministic by design"):
            role_for_agent(GATE_NAME)

    def test_a_floor_outside_zero_to_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="between 0 and 1"):
            AbstentionGate(confidence_floor=1.5)

    def test_the_computed_kinds_are_exactly_the_ones_outside_quoting_kinds(self) -> None:
        """Pins the complement, so adding a `FindingKind` is a decision here.

        A new kind defaults to *not* owing a citation, which is the safe
        direction: the failure would be emitting an uncited finding rather than
        abstaining on every computed one.
        """
        assert {FindingKind.ASSUMPTION_BREACH, FindingKind.CONTRADICTION} == QUOTING_KINDS
        assert frozenset(FindingKind) - QUOTING_KINDS == {
            FindingKind.CALIBRATION_BIAS,
            FindingKind.COLLATERAL_IMPACT,
            FindingKind.STALE_DECISION,
        }
