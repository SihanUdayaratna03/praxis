"""One governance pass: what it writes, what it leaves alone, and in what order.

Three claims run through this file.

The first is that **a second pass over a settled store writes nothing**, which
is the rule `praxis.monitor.run` established and every store pass since has
repeated. It is asserted by counting versions and audit rows rather than by
trusting the run's own report, because a pass that reported `unchanged` while
writing anyway would pass the weaker test.

The second is that **the order of the three stages is load-bearing**. The gate
reads the verdict the challenger writes, and it reads the store *after* the
curator has retracted things — so a pass that routed first would conclude on
claims that no longer stand. Both orderings are asserted against the store
rather than against the code.

The third is that **a merge does not retract the loser**. That is the one
decision in this pass that is not forced by an existing mechanism, so it gets
its own test naming the reason: a person tracing why a decision changed needs
both halves of a revision, and the `supersedes` edge is what says which is
which.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from praxis.agents.abstention import Disposition, Insufficiency
from praxis.agents.curator import IDLE_DAYS, MergeReason
from praxis.agents.governance import GOVERNANCE_ACTOR, govern_store
from praxis.domain.enums import AuditAction, FindingKind, RecordKind, Severity, Verdict
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Finding, Span
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.conftest import Answering

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=IDLE_DAYS + 30)
RECENTLY = NOW - timedelta(days=IDLE_DAYS - 30)
ACTOR = "test"

BODY = "# ADR\n\nThe index stays under 50 GB for the next year.\n"


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


def write_span(store: Repository, doc_id: str = "DOC-0001", uri: str = "adr.md") -> Span:
    source = MARKDOWN_ADAPTER.normalise(BODY.encode("utf-8"), source_uri=uri)
    document = document_from(source, doc_id=doc_id, ingested_at=LONG_AGO)
    store.add(document, actor=ACTOR, reason="fixture")
    return store.add(
        Span.covering(
            document,
            0,
            len(document.content.encode("utf-8")),
            created_by=ACTOR,
            created_at=LONG_AGO,
        ),
        actor=ACTOR,
        reason="fixture",
    )


@pytest.fixture
def span(store: Repository) -> Span:
    return write_span(store)


def write_assumption(
    store: Repository,
    span: Span,
    *,
    number: int = 1,
    predicate: str = "index_size_gb <= 50",
    at: datetime = LONG_AGO,
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
            created_at=at,
        ),
        actor=ACTOR,
        reason="fixture",
        at=at,
    )


def write_finding(
    store: Repository,
    span: Span,
    *,
    number: int = 1,
    subject: str = "A-0001",
    confidence: float = 0.8,
) -> Finding:
    """A finding in the state every producer in this system writes one."""
    return store.add(
        Finding(
            id=f"F-{number:04d}",
            kind=FindingKind.ASSUMPTION_BREACH,
            subject_kind=RecordKind.ASSUMPTION,
            subject_id=subject,
            prosecution=f"{subject} assumed under 50 GB; it is measured at 61 GB.",
            severity=Severity.MEDIUM,
            confidence=confidence,
            evidence_span_ids=(span.id,),
            detected_at=LONG_AGO,
            created_by="AssumptionMonitor",
            created_at=LONG_AGO,
        ),
        actor=ACTOR,
        reason="fixture",
    )


def answers(*entries: tuple[int, bool], confidence: float = 0.9) -> Answering:
    """A provider that upholds or overturns by ordinal."""
    return Answering(
        [
            json.dumps(
                {
                    "judgements": [
                        {
                            "finding_ordinal": ordinal,
                            "rebuttal": "the quoted figure is current",
                            "upheld": upheld,
                            "confidence": confidence,
                        }
                        for ordinal, upheld in entries
                    ]
                }
            )
        ]
    )


class TestWhatThePassWrites:
    """The three kinds of write, each against the store rather than the report."""

    def test_a_verdict_lands_as_a_new_version_of_the_finding(
        self, store: Repository, span: Span
    ) -> None:
        write_assumption(store, span)
        write_finding(store, span)

        run = govern_store(store, answers((0, True)), at=NOW)

        standing = store.require(Finding, "F-0001")
        assert standing.version == 2
        assert standing.verdict is Verdict.UPHELD
        assert standing.challenge == "the quoted figure is current"
        assert len(run.decided) == 1

    def test_the_audit_row_says_which_way_the_argument_went(
        self, store: Repository, span: Span
    ) -> None:
        """The trail records the actor; the reason is what a reader wants."""
        write_assumption(store, span)
        write_finding(store, span)
        govern_store(store, answers((0, False)), at=NOW)

        events = store.audit_for("F-0001")

        assert events[-1].actor == GOVERNANCE_ACTOR
        assert events[-1].action is AuditAction.REVISED
        assert "overturned" in events[-1].reason

    def test_a_merge_writes_a_supersedes_edge(self, store: Repository, span: Span) -> None:
        elsewhere = write_span(store, doc_id="DOC-0002", uri="revision.md")
        write_assumption(store, span, number=1)
        write_assumption(store, elsewhere, number=2, at=RECENTLY)

        run = govern_store(store, None, at=NOW)

        edges = store.links_from("A-0002", types=(LinkType.SUPERSEDES,))
        assert [edge.target_id for edge in edges] == ["A-0001"]
        assert [merge.reason for merge in run.merged] == [MergeReason.DUPLICATE]

    def test_a_merge_does_not_retract_the_superseded_assumption(
        self, store: Repository, span: Span
    ) -> None:
        """The edge is the whole claim; withdrawing as well loses half a revision.

        A person tracing why a decision changed needs both sides of it, and
        retracting the loser would put it beyond `list_all` for every later
        reading of the store.
        """
        elsewhere = write_span(store, doc_id="DOC-0002", uri="revision.md")
        write_assumption(store, span, number=1)
        write_assumption(store, elsewhere, number=2, at=RECENTLY)

        govern_store(store, None, at=NOW)

        assert not store.require(Assumption, "A-0001").retracted
        assert len(store.list_all(Assumption)) == 2

    def test_a_retirement_is_a_retraction_and_the_record_stays_readable(
        self, store: Repository, span: Span
    ) -> None:
        """Invariant 7. The withdrawn version is a version, not a deletion."""
        write_assumption(store, span)

        run = govern_store(store, None, at=NOW)

        assert [record.id for record in run.retired] == ["A-0001"]
        assert store.require(Assumption, "A-0001").retracted
        assert store.require(Assumption, "A-0001", version=1).retracted is False
        assert not store.list_all(Assumption)

    def test_the_retirement_reason_carries_the_number_that_decided_it(
        self, store: Repository, span: Span
    ) -> None:
        """So a retirement made under a 60-day window is legible later."""
        write_assumption(store, span)
        govern_store(store, None, at=NOW)

        events = store.audit_for("A-0001")

        assert events[-1].action is AuditAction.RETRACTED
        assert f"{IDLE_DAYS + 30} days" in events[-1].reason


class TestWritingOnlyOnAChange:
    """A second pass over a settled store, asserted against the store."""

    def test_a_second_pass_writes_no_new_version(self, store: Repository, span: Span) -> None:
        write_assumption(store, span, at=RECENTLY)
        write_finding(store, span)
        govern_store(store, answers((0, True)), at=NOW)
        before = store.versions("F-0001")

        run = govern_store(store, answers((0, False)), at=NOW)

        assert store.versions("F-0001") == before
        assert not run.decided

    def test_a_second_pass_makes_no_model_call(self, store: Repository, span: Span) -> None:
        """The challenger refuses to re-argue a finding that carries a verdict.

        So re-running governance over a settled store is free, which is what
        makes it safe to put in a pipeline.
        """
        write_assumption(store, span, at=RECENTLY)
        write_finding(store, span)
        govern_store(store, answers((0, True)), at=NOW)
        provider = answers((0, False))

        run = govern_store(store, provider, at=NOW)

        assert run.calls == 0
        assert not provider.requests

    def test_a_second_pass_proposes_no_merge_it_already_made(
        self, store: Repository, span: Span
    ) -> None:
        elsewhere = write_span(store, doc_id="DOC-0002", uri="revision.md")
        write_assumption(store, span, number=1)
        write_assumption(store, elsewhere, number=2, at=RECENTLY)
        govern_store(store, None, at=NOW)

        run = govern_store(store, None, at=NOW)

        assert not run.merged
        assert len(store.links_from("A-0002", types=(LinkType.SUPERSEDES,))) == 1

    def test_a_second_pass_cannot_retire_what_it_already_retired(
        self, store: Repository, span: Span
    ) -> None:
        """`list_all` excludes retracted records, so the case cannot arise."""
        write_assumption(store, span)
        govern_store(store, None, at=NOW)

        run = govern_store(store, None, at=NOW)

        assert not run.retired
        assert store.versions("A-0001") == (1, 2)


class TestTheOrderOfTheStages:
    """Two orderings that are load-bearing, asserted through their effects."""

    def test_the_gate_sees_the_verdict_the_challenger_wrote(
        self, store: Repository, span: Span
    ) -> None:
        """Routing before challenging would call every finding never-challenged."""
        write_assumption(store, span, at=RECENTLY)
        write_finding(store, span)

        run = govern_store(store, answers((0, True)), at=NOW)

        assert [routing.disposition for routing in run.gate.routings] == [Disposition.EMIT]

    def test_the_gate_sees_the_assumption_the_curator_retracted(
        self, store: Repository, span: Span
    ) -> None:
        """A finding about a record this very pass withdrew is not concluded.

        This is why the gate runs last and re-reads rather than reusing the
        list the pass opened with.
        """
        write_assumption(store, span)
        write_finding(store, span, confidence=0.9)

        run = govern_store(store, answers((0, True)), at=NOW)

        assert [record.id for record in run.retired] == ["A-0001"]
        routing = run.gate.routings[0]
        assert routing.disposition is Disposition.NEEDS_HUMAN
        assert Insufficiency.SUBJECT_WITHDRAWN in routing.insufficiencies


class TestWithoutAProvider:
    """Degrade quality, never correctness."""

    def test_nothing_is_argued_and_everything_reaches_a_person(
        self, store: Repository, span: Span
    ) -> None:
        write_assumption(store, span, at=RECENTLY)
        write_finding(store, span)

        run = govern_store(store, None, at=NOW)

        assert run.calls == 0
        assert not run.decided
        assert [routing.disposition for routing in run.gate.routings] == [Disposition.NEEDS_HUMAN]
        assert Insufficiency.NEVER_CHALLENGED in run.gate.routings[0].insufficiencies

    def test_curation_still_runs(self, store: Repository, span: Span) -> None:
        """The curator makes no model call, so a missing provider costs it nothing."""
        write_assumption(store, span)

        assert govern_store(store, None, at=NOW).retired


class TestTheRunsOwnReport:
    """What the CLI and the eval harness read off a run."""

    def test_an_empty_store_reports_zeros_and_writes_nothing(self, store: Repository) -> None:
        run = govern_store(store, None, at=NOW)

        assert run.written == 0
        assert run.calls == 0
        assert run.curation.considered == 0
        assert run.gate.considered == 0

    def test_written_counts_all_three_kinds_of_change(self, store: Repository, span: Span) -> None:
        elsewhere = write_span(store, doc_id="DOC-0002", uri="revision.md")
        write_assumption(store, span, number=1)
        write_assumption(store, elsewhere, number=2, at=RECENTLY)
        write_assumption(store, span, number=3, predicate="query_latency_ms <= 200")
        write_finding(store, span, subject="A-0003")

        run = govern_store(store, answers((0, True)), at=NOW)

        assert len(run.decided) == 1
        assert len(run.merged) == 1
        assert len(run.retired) == 1
        assert run.written == 3

    def test_an_idle_window_can_be_widened_without_editing_a_constant(
        self, store: Repository, span: Span
    ) -> None:
        """The CLI exposes this so a person can ask what a different window does."""
        write_assumption(store, span)

        run = govern_store(store, None, at=NOW, idle_days=IDLE_DAYS + 365)

        assert not run.retired

    def test_the_refusals_are_reported_rather_than_filtered(
        self, store: Repository, span: Span
    ) -> None:
        """Which beliefs nobody can check is a fact somebody can act on."""
        write_assumption(store, span, at=RECENTLY)

        run = govern_store(store, None, at=NOW)

        assert [entry.refusal.value for entry in run.curation.declined] == ["not_yet_idle"]
        assert not run.retired


class TestAnArgumentThatReachedNoVerdict:
    """The one finding state a re-run genuinely revisits, and what it costs."""

    def test_an_undecided_challenge_is_recorded_with_its_prose(
        self, store: Repository, span: Span
    ) -> None:
        """Below the floor, so no verdict -- but the objection is worth keeping.

        The finding stays `UNDECIDED`, which the gate then routes to a person as
        `NEVER_CHALLENGED`. That is the correct outcome: nobody was willing to
        decide it, so nobody is told it was decided.
        """
        write_assumption(store, span, at=RECENTLY)
        write_finding(store, span)

        run = govern_store(store, answers((0, True), confidence=0.1), at=NOW)

        standing = store.require(Finding, "F-0001")
        assert standing.verdict is Verdict.UNDECIDED
        assert standing.challenge == "the quoted figure is current"
        assert "left undecided" in store.audit_for("F-0001")[-1].reason
        assert Insufficiency.NEVER_CHALLENGED in run.gate.routings[0].insufficiencies

    def test_re_arguing_it_to_the_same_place_writes_no_new_version(
        self, store: Repository, span: Span
    ) -> None:
        """An undecided finding is the one thing the challenger will re-argue.

        So the write-on-change comparison has to cover the prose as well as the
        verdict -- keyed on the verdict alone, this would write a new version
        every pass forever and the store would grow without anything changing.
        """
        write_assumption(store, span, at=RECENTLY)
        write_finding(store, span)
        govern_store(store, answers((0, True), confidence=0.1), at=NOW)
        before = store.versions("F-0001")

        run = govern_store(store, answers((0, True), confidence=0.1), at=NOW)

        assert store.versions("F-0001") == before
        assert run.unchanged == 1
        assert not run.decided

    def test_a_different_objection_does_write_a_new_version(
        self, store: Repository, span: Span
    ) -> None:
        """The control. Without it the test above passes against a pass that
        never writes at all."""
        write_assumption(store, span, at=RECENTLY)
        write_finding(store, span)
        govern_store(store, answers((0, True), confidence=0.1), at=NOW)
        provider = Answering(
            [
                json.dumps(
                    {
                        "judgements": [
                            {
                                "finding_ordinal": 0,
                                "rebuttal": "a second look at the same figure",
                                "upheld": True,
                                "confidence": 0.1,
                            }
                        ]
                    }
                )
            ]
        )

        run = govern_store(store, provider, at=NOW)

        assert store.versions("F-0001") == (1, 2, 3)
        assert len(run.decided) == 1
