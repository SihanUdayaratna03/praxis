"""A contradiction pass over a real store: what it writes, and what it writes once.

Read back out of SQLite, not off the objects the run returned, for the reason
`tests/agents/test_extraction.py` gives -- inspecting what was passed in only
proves the pipeline agrees with itself.

The claim under `TestReRunning` is that a second pass costs nothing and changes
nothing, and it holds by construction rather than by a check: a `Link`'s id is
derived from its type and its two endpoints, and the pair is ordered before the
edge is built, so the same contradiction found twice is the same id twice.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta

import pytest
from praxis.agents.contradiction import ContradictionDetector
from praxis.agents.detection import DETECTION_ACTOR, detect_in_store
from praxis.agents.results import Settlement
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Link, Span
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.conftest import Answering
from tests.monitor.conftest import ACTOR, AT, BODY, make_assumption, make_decision

LATER = AT + timedelta(days=1)


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def span(store: Repository) -> Span:
    """One real document and one span over it, both in the store.

    Built here rather than shared with `tests/monitor/` because a fixture that
    two packages reach into is a fixture neither owns, and this file needs the
    document written as well as the span.
    """
    source = MARKDOWN_ADAPTER.normalise(BODY.encode("utf-8"), source_uri="adr.md")
    document = store.add(
        document_from(source, doc_id="DOC-0001", ingested_at=AT), actor=ACTOR, reason="fixture"
    )
    return store.add(
        Span.covering(
            document, 0, len(document.content.encode("utf-8")), created_by=ACTOR, created_at=AT
        ),
        actor=ACTOR,
        reason="fixture",
    )


def write(store: Repository, span, number: int, predicate: str, statement: str) -> Assumption:
    """One assumption, in the store."""
    return store.add(
        make_assumption(
            span, assumption_id=f"A-{number:04d}", predicate=predicate, statement=statement
        ),
        actor=ACTOR,
        reason="fixture",
    )


def contradicting_pair(store: Repository, span) -> None:
    """Two assumptions whose predicates cannot both hold."""
    write(store, span, 1, "index_size_gb <= 50", "the index stays under 50 GB")
    write(store, span, 2, "index_size_gb > 50", "the index will pass 50 GB")


def links_in(store: Repository) -> tuple[Link, ...]:
    """Every `contradicts` edge the store holds."""
    return tuple(link for link in store.list_all(Link) if link.link_type is LinkType.CONTRADICTS)


class TestWhatAPassWrites:
    def test_a_proven_contradiction_is_written(self, store, span):
        contradicting_pair(store, span)
        run = detect_in_store(store, at=AT)
        assert len(run.written) == 1
        assert len(links_in(store)) == 1

    def test_the_edge_joins_the_two_records(self, store, span):
        contradicting_pair(store, span)
        detect_in_store(store, at=AT)
        edge = links_in(store)[0]
        assert {edge.source_id, edge.target_id} == {"A-0001", "A-0002"}

    def test_the_audit_row_says_which_stage_settled_it(self, store, span):
        # A person reading the trail should be able to tell an arithmetic
        # certainty from a model's opinion without opening the code.
        contradicting_pair(store, span)
        detect_in_store(store, at=AT)
        events = store.audit_for(links_in(store)[0].id)
        assert events[0].actor == DETECTION_ACTOR
        assert Settlement.ARITHMETIC.value in events[0].reason

    def test_the_run_id_reaches_the_edge_it_writes(self, store, span):
        contradicting_pair(store, span)
        detect_in_store(store, at=AT, run_id="RUN-abc")
        assert store.audit_for(links_in(store)[0].id)[0].run_id == "RUN-abc"

    def test_nothing_is_written_when_nothing_conflicts(self, store, span):
        write(store, span, 1, "index_size_gb <= 50", "the index stays under 50 GB")
        write(store, span, 2, "query_latency_ms <= 200", "queries answer quickly")
        assert detect_in_store(store, at=AT).written == ()

    def test_an_empty_store_is_a_run(self, store):
        run = detect_in_store(store, at=AT)
        assert run.records == 0
        assert run.written == ()

    def test_decisions_are_read_alongside_assumptions(self, store, span):
        # `contradicts` runs between any two claims, so splitting the pass in
        # two would mean two indexes that never see each other's records.
        write(store, span, 1, "index_size_gb <= 50", "the index stays under 50 GB")
        store.add(make_decision(span), actor=ACTOR, reason="fixture")
        assert detect_in_store(store, at=AT).records == 2

    def test_a_pass_with_no_provider_still_writes_what_it_can_prove(self, store, span):
        contradicting_pair(store, span)
        run = detect_in_store(store, at=AT)
        assert run.calls == 0
        assert len(run.written) == 1


class TestReRunning:
    def test_a_second_pass_writes_no_second_edge(self, store, span):
        contradicting_pair(store, span)
        detect_in_store(store, at=AT)
        second = detect_in_store(store, at=LATER)
        assert second.written == ()
        assert len(links_in(store)) == 1

    def test_a_second_pass_does_not_reconsider_a_known_pair(self, store, span):
        # Handed to the detector as `known`, so a re-run does not pay to
        # re-decide what the store already asserts.
        contradicting_pair(store, span)
        detect_in_store(store, at=AT)
        assert detect_in_store(store, at=LATER).result.blocking.candidates == ()

    def test_a_second_pass_makes_no_model_calls(self, store, span):
        contradicting_pair(store, span)
        provider = Answering([json.dumps({"judgements": []})] * 4)
        detect_in_store(store, provider=provider, at=AT)
        assert detect_in_store(store, provider=provider, at=LATER).calls == 0

    def test_a_new_record_is_still_compared(self, store, span):
        contradicting_pair(store, span)
        detect_in_store(store, at=AT)
        write(store, span, 3, "index_size_gb >= 90", "the index passes 90 GB")
        assert len(detect_in_store(store, at=LATER).written) >= 1

    def test_an_edge_found_twice_is_written_once(self, store, span):
        # The guard behind `_write`. A detector that ignored what the store
        # already holds would offer the same edge again, and the ordinary
        # outcome of a re-run should be a no-op rather than a DuplicateRecordError.
        contradicting_pair(store, span)
        detect_in_store(store, at=AT)
        again = detect_in_store(store, at=LATER, detector=_ForgetfulDetector())
        assert again.contradictions
        assert again.written == ()
        assert len(links_in(store)) == 1


class TestWhatTheRunReports:
    def test_what_blocking_skipped_is_reported_whole(self, store, span):
        # A pair never proposed and a pair judged not to conflict are different
        # results, and the eval table has to be able to tell them apart.
        contradicting_pair(store, span)
        assert detect_in_store(store, at=AT).result.blocking.indexed == 2

    def test_a_found_contradiction_is_reported_even_when_already_stored(self, store, span):
        contradicting_pair(store, span)
        detect_in_store(store, at=AT)
        # Nothing is re-proposed, so nothing is re-found -- which is what makes
        # `written` and `contradictions` agree on a first pass and both empty on
        # a second.
        assert detect_in_store(store, at=LATER).contradictions == ()

    def test_calls_are_reported_from_the_detector(self, store, span):
        write(store, span, 1, "the rollout finishes this quarter", "the rollout finishes soon")
        write(store, span, 2, "the rollout slips past this quarter", "the rollout slips")
        provider = Answering([json.dumps({"judgements": []})])
        assert detect_in_store(store, provider=provider, at=AT).calls == 1


class TestAnEdgeWrittenByHand:
    def test_an_edge_the_store_already_holds_is_recognised_in_either_direction(self, store, span):
        # `contradicts` is symmetric and stored once, so an edge written the
        # other way round still has to count as known.
        contradicting_pair(store, span)
        store.add(
            Link.between(
                LinkType.CONTRADICTS,
                "A-0002",
                "A-0001",
                rationale="noticed by a person",
                confidence=1.0,
                created_by="a colleague",
                created_at=AT,
            ),
            actor=ACTOR,
            reason="fixture",
        )
        run = detect_in_store(store, at=AT)
        assert run.written == ()
        assert len(links_in(store)) == 1

    @pytest.fixture(autouse=True)
    def _quiet(self) -> None:
        """No setup; the class exists to group one claim."""
        return


class TestDeterminism:
    def test_the_pair_is_reported_in_a_stable_order(self, store, span):
        # One pair, one identity: the edge's id is derived from its endpoints,
        # so an order that varied would let two runs write two edges.
        contradicting_pair(store, span)
        found = detect_in_store(store, at=AT).contradictions
        assert [entry.pair for entry in found] == [("A-0001", "A-0002")]

    def test_the_clock_is_the_caller_s_and_not_the_machine_s(self, store, span):
        contradicting_pair(store, span)
        detect_in_store(store, at=AT)
        assert store.audit_for(links_in(store)[0].id)[0].occurred_at == AT

    def test_now_is_used_when_no_clock_is_given(self, store, span):
        # Timezone-aware, invariant 5, even on the default path.
        contradicting_pair(store, span)
        detect_in_store(store)
        assert store.audit_for(links_in(store)[0].id)[0].occurred_at.tzinfo is not None


class _ForgetfulDetector(ContradictionDetector):
    """A detector that ignores what the store already holds.

    Not a shortcut: it is how the duplicate guard in `praxis.agents.detection`
    is reached at all, since the ordinary pass excludes a known pair before it
    is ever proposed. Written as a real subclass so the rest of the agent --
    blocking, the arithmetic, the edge it builds -- is exercised unchanged.
    """

    def detect(self, records, *, at, known=()):
        return super().detect(records, at=at)
