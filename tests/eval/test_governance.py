"""Grading governance: three rates, one answer key, and three claims with controls.

The claims under this file.

**A rate with no denominator is not a rate.** Zero conceded out of zero decided
is *no measurement*, and reads identically to a challenger that never concedes
unless the denominator travels with it. The same trap sits under all three
rates and each gets a test.

**The concede rate carries its provider.** Offline it measures
`praxis.llm.synthesis._TRUE_BIAS` and not reasoning, so `mock_provider` is part
of the score rather than a footnote somewhere else.

**Every internal claim is tested in both directions.** Phase 7's lesson, and it
bites hardest here: "every retirement was free to make" is satisfied trivially
by a curator that retires nothing, and "the gate agrees with its rules" by a
gate that emits nothing. Each boolean therefore has a store that makes it false.

**The abstention denominator excludes drops.** A finding the challenger
overturned was never a candidate for emission, so counting it would make the
abstention rate fall whenever the concede rate rose -- one component's number
moving another's.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from praxis.agents.abstention import (
    AbstentionGate,
    Disposition,
    GateResult,
    Insufficiency,
    Routing,
)
from praxis.agents.curator import (
    IDLE_DAYS,
    CurationResult,
    CuratorAgent,
    Retirement,
)
from praxis.domain.enums import (
    DecisionScope,
    FindingKind,
    Impact,
    RecordKind,
    Severity,
    Verdict,
)
from praxis.domain.links import LinkType
from praxis.domain.records import (
    Assumption,
    Decision,
    Finding,
    Link,
    RejectedOption,
    Span,
)
from praxis.eval.governance import GovernanceScore, grade_governance
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=IDLE_DAYS + 400)
ACTOR = "test"

BODY = "# ADR\n\nThe index stays under 50 GB for the next year.\n"


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def span(store: Repository) -> Span:
    source = MARKDOWN_ADAPTER.normalise(BODY.encode("utf-8"), source_uri="adr.md")
    document = document_from(source, doc_id="DOC-0001", ingested_at=OLD)
    store.add(document, actor=ACTOR, reason="fixture", at=OLD)
    return store.add(
        Span.covering(
            document, 0, len(document.content.encode("utf-8")), created_by=ACTOR, created_at=OLD
        ),
        actor=ACTOR,
        reason="fixture",
        at=OLD,
    )


def write_assumption(
    store: Repository, span: Span, *, number: int = 1, predicate: str = "index_size_gb <= 50"
) -> Assumption:
    return store.add(
        Assumption(
            id=f"A-{number:04d}",
            statement="the index stays under 50 GB",
            predicate=predicate,
            expiry_condition='on_event("the index is re-sharded")',
            span_id=span.id,
            confidence=0.8,
            created_by="AssumptionFormalizer",
            created_at=OLD,
        ),
        actor=ACTOR,
        reason="fixture",
        at=OLD,
    )


def write_finding(  # noqa: PLR0913 -- every field a test varies, named
    store: Repository,
    span: Span,
    *,
    number: int = 1,
    subject: str = "A-0001",
    kind: FindingKind = FindingKind.ASSUMPTION_BREACH,
    verdict: Verdict = Verdict.UNDECIDED,
    challenge: str | None = None,
    confidence: float = 0.8,
    spans: tuple[str, ...] | None = None,
) -> Finding:
    return store.add(
        Finding(
            id=f"F-{number:04d}",
            kind=kind,
            subject_kind=RecordKind.ASSUMPTION,
            subject_id=subject,
            prosecution=f"{subject}: the case against it.",
            challenge=challenge,
            verdict=verdict,
            severity=Severity.MEDIUM,
            confidence=confidence,
            evidence_span_ids=(span.id,) if spans is None else spans,
            detected_at=OLD,
            created_by="AssumptionMonitor",
            created_at=OLD,
        ),
        actor=ACTOR,
        reason="fixture",
        at=OLD,
    )


def write_decision(store: Repository, span: Span, assumption_id: str) -> Decision:
    decision = store.add(
        Decision(
            id="D-0001",
            title="the product search index",
            chosen="OpenSearch on managed nodes",
            rejected=(RejectedOption(option="Postgres", reason="ranking quality"),),
            decision_maker="Nadeesha",
            decided_at=OLD,
            scope=DecisionScope.TEAM,
            impact=Impact.HIGH,
            span_id=span.id,
            confidence=0.9,
            created_by=ACTOR,
            created_at=OLD,
        ),
        actor=ACTOR,
        reason="fixture",
        at=OLD,
    )
    store.add(
        Link.between(
            LinkType.ASSUMES,
            decision.id,
            assumption_id,
            rationale="the decision rests on it",
            confidence=1.0,
            created_by=ACTOR,
            created_at=OLD,
        ),
        actor=ACTOR,
        reason="fixture",
        at=OLD,
    )
    return decision


class TestTheRates:
    """Three numbers, and the denominator each one needs to mean anything."""

    def test_the_concede_rate_is_over_decided_challenges(
        self, store: Repository, span: Span
    ) -> None:
        write_assumption(store, span)
        write_finding(store, span, number=1, verdict=Verdict.UPHELD, challenge="held")
        write_finding(store, span, number=2, verdict=Verdict.OVERTURNED, challenge="gave way")
        write_finding(store, span, number=3, challenge="argued and left open")

        score = grade_governance(store)

        assert score.challenged == 3
        assert score.decided == 2
        assert score.concede_rate == Decimal("0.5000")

    def test_nothing_decided_is_no_measurement_rather_than_a_rate_of_zero(
        self, store: Repository, span: Span
    ) -> None:
        """The trap this whole file exists for, in its simplest form."""
        write_assumption(store, span)
        write_finding(store, span)

        score = grade_governance(store)

        assert score.decided == 0
        assert score.concede_rate == Decimal(0)

    def test_the_abstention_denominator_excludes_what_the_challenger_dropped(
        self, store: Repository, span: Span
    ) -> None:
        """Otherwise the abstention rate falls whenever the concede rate rises.

        Two components, two numbers, and one must not move the other.
        """
        write_assumption(store, span)
        write_finding(store, span, number=1, verdict=Verdict.UPHELD, challenge="held")
        write_finding(store, span, number=2)
        write_finding(store, span, number=3, verdict=Verdict.OVERTURNED, challenge="gave way")

        score = grade_governance(store)

        assert score.emitted == 1
        assert score.abstained == 1
        assert score.abstention_rate == Decimal("0.5000")

    def test_abstention_precision_is_one_when_every_abstention_names_a_rule(
        self, store: Repository, span: Span
    ) -> None:
        """The number behind `abstentions_hold`. It says by how much, not whether."""
        write_assumption(store, span)
        write_finding(store, span, number=1, verdict=Verdict.UPHELD, challenge="held")
        write_finding(store, span, number=2)

        score = grade_governance(store)

        assert score.abstained == 1
        assert score.abstained_with_cause == 1
        assert score.abstention_precision == Decimal("1.0000")
        assert score.abstentions_hold

    def test_nothing_withheld_is_no_measurement_rather_than_a_precision_of_zero(
        self, store: Repository, span: Span
    ) -> None:
        write_assumption(store, span)
        write_finding(store, span, verdict=Verdict.UPHELD, challenge="held")

        score = grade_governance(store)

        assert score.abstained == 0
        assert score.abstention_precision == Decimal(0)

    def test_the_retirement_rate_is_over_live_assumptions(
        self, store: Repository, span: Span
    ) -> None:
        write_assumption(store, span, number=1)
        write_assumption(store, span, number=2, predicate="query_latency_ms <= 200")
        write_assumption(store, span, number=3, predicate="team_size >= 4")
        write_decision(store, span, "A-0003")

        score = grade_governance(store)

        assert score.assumptions == 3
        assert score.retirements == 2
        assert score.kept_under_a_decision == 1
        assert score.retirement_rate == Decimal("0.6667")

    def test_an_empty_store_reports_zeros_and_no_division(self, store: Repository) -> None:
        score = grade_governance(store)

        assert score == GovernanceScore()
        assert score.concede_rate == Decimal(0)
        assert score.retirement_rate == Decimal(0)
        assert score.abstention_rate == Decimal(0)
        assert score.abstention_precision == Decimal(0)
        assert score.merge_recall == Decimal(0)


class TestTheOneAnswerKey:
    """Merge recall, the only comparison against truth in this module."""

    def test_a_proposed_merge_counts_against_what_the_corpus_labels(
        self, store: Repository, span: Span
    ) -> None:
        write_assumption(store, span, number=1)
        write_assumption(store, span, number=2)

        score = grade_governance(store, merges_expected=2)

        assert score.merges == 1
        assert score.merge_recall == Decimal("0.5000")

    def test_a_merge_already_written_still_counts(self, store: Repository, span: Span) -> None:
        """The metric would otherwise be upside down.

        The curator refuses a pair whose `supersedes` edge already stands, so
        grading its proposals alone would score a governed store at zero and an
        ungoverned one at full marks.
        """
        write_assumption(store, span, number=1)
        write_assumption(store, span, number=2)
        store.add(
            Link.between(
                LinkType.SUPERSEDES,
                "A-0002",
                "A-0001",
                rationale="already curated",
                confidence=1.0,
                created_by="CuratorAgent",
                created_at=OLD,
            ),
            actor=ACTOR,
            reason="fixture",
            at=OLD,
        )

        score = grade_governance(store, merges_expected=1)

        assert score.merges == 1
        assert score.merge_recall == Decimal("1.0000")

    def test_no_labelled_edges_reports_zero_rather_than_dividing(
        self, store: Repository, span: Span
    ) -> None:
        """A corpus generated with `--revisions 0` plants none."""
        write_assumption(store, span)

        assert grade_governance(store, merges_expected=0).merge_recall == Decimal(0)


class TestTheProviderTravelsWithTheNumber:
    """Offline the concede rate is a property of schema synthesis."""

    def test_the_mock_is_recorded_in_the_score(self, store: Repository) -> None:
        assert grade_governance(store, mock_provider=True).mock_provider

    def test_a_live_provider_is_recorded_too(self, store: Repository) -> None:
        """The control. A flag that is always true says nothing."""
        assert not grade_governance(store, mock_provider=False).mock_provider


class TestTheThreeClaims:
    """Each boolean, and the store that makes it false."""

    def test_verdicts_hold_when_every_verdict_carries_its_challenge(
        self, store: Repository, span: Span
    ) -> None:
        write_assumption(store, span)
        write_finding(store, span, verdict=Verdict.UPHELD, challenge="held")

        assert grade_governance(store).verdicts_hold

    def test_an_undecided_finding_with_a_challenge_is_not_a_defect(
        self, store: Repository, span: Span
    ) -> None:
        """It is the confidence floor working, and a grader must not call it broken."""
        write_assumption(store, span)
        write_finding(store, span, challenge="argued and left open")

        assert grade_governance(store).verdicts_hold

    def test_retirements_hold_when_nothing_retired_is_depended_on(
        self, store: Repository, span: Span
    ) -> None:
        write_assumption(store, span)

        assert grade_governance(store).retirements_hold

    def test_retirements_do_not_hold_when_one_would_take_a_decision_with_it(
        self, store: Repository, span: Span, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control that makes the boolean mean something.

        The curator refuses this case, so the only way to reach it is to make
        the curator wrong -- which is exactly what the grader exists to catch if
        it ever happens for real. ADR 0031 assumption 5.
        """
        assumption = write_assumption(store, span)
        write_decision(store, span, assumption.id)

        def retire_anyway(self: object, *, at: datetime) -> CurationResult:
            return CurationResult(retirements=(Retirement(assumption, idle_days=999),))

        monkeypatch.setattr(CuratorAgent, "curate", retire_anyway)

        assert not grade_governance(store).retirements_hold

    def test_a_retracted_assumption_is_still_readable_at_version_one(
        self, store: Repository, span: Span
    ) -> None:
        """Invariant 7, checked as a fact about the store rather than a promise."""
        write_assumption(store, span)
        store.retract(Assumption, "A-0001", actor=ACTOR, reason="curated")

        score = grade_governance(store)

        assert score.retirements_hold
        assert store.get(Assumption, "A-0001", version=1) is not None

    def test_abstentions_hold_when_the_gate_agrees_with_its_rules(
        self, store: Repository, span: Span
    ) -> None:
        write_assumption(store, span)
        write_finding(store, span, number=1, verdict=Verdict.UPHELD, challenge="held")
        write_finding(store, span, number=2, confidence=0.1)

        score = grade_governance(store)

        assert score.abstentions_hold
        assert score.by_insufficiency["never_challenged"] == 1
        assert score.by_insufficiency["low_confidence"] == 1

    def test_abstentions_do_not_hold_when_a_drop_carries_no_overturned_verdict(
        self, store: Repository, span: Span, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control from the direction a real defect would come from.

        A gate that dropped a finding nobody overturned would silence it without
        anything having argued against it, which is the worst outcome this layer
        can produce.
        """
        write_assumption(store, span)
        write_finding(store, span, verdict=Verdict.UPHELD, challenge="held")

        def drop_everything(
            self: object, findings: object, *, withdrawn: object = ()
        ) -> GateResult:
            return GateResult(routings=(Routing("F-0001", Disposition.DROPPED, (), "no reason"),))

        monkeypatch.setattr(AbstentionGate, "route_all", drop_everything)

        assert not grade_governance(store).abstentions_hold

    def test_claims_hold_is_all_three_together(self, store: Repository, span: Span) -> None:
        """One line for a report, and it must not be true when a part is false."""
        write_assumption(store, span)
        write_finding(store, span, verdict=Verdict.UPHELD, challenge="held")

        assert grade_governance(store).claims_hold
        assert not GovernanceScore(abstentions_hold=False).claims_hold


class TestWhatIsRecomputedRatherThanReported:
    """Grading a store rather than a run's opinion of itself."""

    def test_the_grade_reads_the_store_and_takes_no_run(
        self, store: Repository, span: Span
    ) -> None:
        """A grade taken from a pass's return value grades the pipeline's opinion.

        This one is handed nothing but a repository, which is what makes a
        record refused by a foreign key impossible to score as a hit.
        """
        write_assumption(store, span)
        write_finding(store, span, verdict=Verdict.UPHELD, challenge="held")

        assert grade_governance(store) == grade_governance(store)

    def test_a_retracted_assumption_leaves_the_curated_denominator(
        self, store: Repository, span: Span
    ) -> None:
        """`list_all` excludes it, so a governed store does not keep re-curating."""
        write_assumption(store, span, number=1)
        write_assumption(store, span, number=2, predicate="team_size >= 4")
        store.retract(Assumption, "A-0002", actor=ACTOR, reason="curated")

        assert grade_governance(store).assumptions == 1


class TestTheGateContradictingItself:
    """The two dispositions a broken gate would emit, each forced directly.

    Neither is reachable through `AbstentionGate` -- its rules and its verdicts
    are computed together -- which is precisely why the grader checks them. A
    consistency claim that can only be true is not a claim.
    """

    def test_an_emitted_finding_that_failed_a_rule_does_not_hold(
        self, store: Repository, span: Span, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The worst direction: a conclusion reached on evidence the gate rejected."""
        write_assumption(store, span)
        write_finding(store, span, verdict=Verdict.UPHELD, challenge="held")

        def emit_anyway(self: object, findings: object, *, withdrawn: object = ()) -> GateResult:
            return GateResult(
                routings=(
                    Routing(
                        "F-0001",
                        Disposition.EMIT,
                        (Insufficiency.LOW_CONFIDENCE,),
                        "emitted anyway",
                    ),
                )
            )

        monkeypatch.setattr(AbstentionGate, "route_all", emit_anyway)

        assert not grade_governance(store).abstentions_hold

    def test_an_abstention_with_no_rule_behind_it_does_not_hold(
        self, store: Repository, span: Span, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refusal nobody can explain is not a refusal, it is a silence."""
        write_assumption(store, span)
        write_finding(store, span, verdict=Verdict.UPHELD, challenge="held")

        def withhold_silently(
            self: object, findings: object, *, withdrawn: object = ()
        ) -> GateResult:
            return GateResult(
                routings=(Routing("F-0001", Disposition.NEEDS_HUMAN, (), "no reason given"),)
            )

        monkeypatch.setattr(AbstentionGate, "route_all", withhold_silently)

        assert not grade_governance(store).abstentions_hold

    def test_a_drop_naming_a_finding_the_store_does_not_hold_does_not_hold(
        self, store: Repository, span: Span, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Silencing a record nobody can look up is the least recoverable failure."""
        write_assumption(store, span)
        write_finding(store, span, verdict=Verdict.OVERTURNED, challenge="gave way")

        def drop_a_ghost(self: object, findings: object, *, withdrawn: object = ()) -> GateResult:
            return GateResult(
                routings=(Routing("F-9999", Disposition.DROPPED, (), "not in the store"),)
            )

        monkeypatch.setattr(AbstentionGate, "route_all", drop_a_ghost)

        assert not grade_governance(store).abstentions_hold
