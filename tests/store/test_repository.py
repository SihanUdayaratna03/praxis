"""What the repository promises: ids, exact round-trips, versions, no half-writes.

The repository is the only module that writes SQL against a record, so it is the
only place the append-only invariant can be broken by ordinary code rather than
by someone reaching past the API. These tests are written against the promises
rather than the statements: that an id is never reissued, that a record comes
back as the value that went in, that a revision adds a version instead of
replacing one, and that a write which fails leaves the store exactly as it was --
including its audit trail, which is the part that would otherwise be a hole
nobody can see.

`tests/store/test_schema.py` covers what the database refuses on its own. What is
here is what the repository refuses on top of it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from praxis.config.settings import Settings
from praxis.domain.enums import (
    DecisionScope,
    FindingKind,
    Impact,
    MatchQuality,
    RecordKind,
    Severity,
    Unit,
)
from praxis.domain.ids import (
    CONTENT_ADDRESSED_KINDS,
    SEQUENTIAL_KINDS,
    AssumptionId,
    DecisionId,
    EstimateId,
    FindingId,
    OutcomeId,
    format_sequential_id,
)
from praxis.domain.links import LinkType
from praxis.domain.records import (
    Assumption,
    Decision,
    Estimate,
    Finding,
    Link,
    Outcome,
    RejectedOption,
    Span,
)
from praxis.store.errors import (
    DanglingEdgeError,
    DuplicateRecordError,
    RecordNotFoundError,
    StoreError,
    StoreNotInitialisedError,
    VersionConflictError,
)
from praxis.store.migrations import latest_version
from praxis.store.repository import Repository, open_repository

from tests.store.conftest import ACTOR, WRITTEN_AT, World

LATER = WRITTEN_AT + timedelta(hours=1)
"""A second instant, so a revision's timestamp is distinguishable from a write's."""

WORLD_RECORDS = (
    "document",
    "span",
    "decision",
    "other_decision",
    "assumption",
    "estimate",
    "outcome",
    "finding",
)


def add(store: Repository, record, *, reason: str = "written by a test"):
    """Write a record with the suite's actor and fixed timestamp."""
    return store.add(record, actor=ACTOR, reason=reason, at=WRITTEN_AT)


def revise(store: Repository, record, *, reason: str = "revised by a test", **updates):
    """Revise a record, changing the named fields."""
    return store.revise(record.model_copy(update=updates), actor=ACTOR, reason=reason, at=LATER)


def retract(store: Repository, record_type, record_id: str):
    """Retract a record with the suite's actor."""
    return store.retract(
        record_type, record_id, actor=ACTOR, reason="withdrawn by a test", at=LATER
    )


def a_decision(record_id: str, span_id: str, *, title: str = "Ledger backend") -> Decision:
    """A valid decision with a caller-chosen id, for the allocation tests."""
    return Decision(
        id=DecisionId(record_id),
        title=title,
        chosen="SQLite",
        rejected=(RejectedOption(option="Postgres", reason="needs a server nobody will run"),),
        decision_maker="Sihan Udayaratna",
        decided_at=WRITTEN_AT,
        scope=DecisionScope.PROJECT,
        impact=Impact.HIGH,
        span_id=span_id,
        confidence=0.8,
        created_at=WRITTEN_AT,
        created_by=ACTOR,
    )


# --- allocation -----------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(SEQUENTIAL_KINDS))
def test_the_first_id_of_every_kind_is_its_first_ordinal(store, kind):
    assert store.next_id(kind) == format_sequential_id(kind, 1)


def test_ids_are_allocated_in_order(store, world: World):
    first = store.next_id(RecordKind.DECISION)
    add(store, a_decision(first, world.span.id, title="A third decision"))

    assert first == "D-0003"
    assert store.next_id(RecordKind.DECISION) == "D-0004"


def test_each_kind_has_its_own_counter(store, world: World):
    # Two decisions and one assumption are in the fixture. A shared counter
    # would make the next assumption A-0004, which is not wrong so much as
    # unreadable -- the ordinal is the one part of an id a human uses.
    assert store.next_id(RecordKind.DECISION) == "D-0003"
    assert store.next_id(RecordKind.ASSUMPTION) == "A-0002"


def test_an_id_comes_from_the_highest_ordinal_not_from_the_row_count(store, world: World):
    # A history with a gap in it -- a rolled-back write, an imported record --
    # must not hand back an ordinal that is already taken. Counting rows would
    # do exactly that, and the collision would surface as a duplicate id much
    # later, in whichever agent happened to allocate next.
    add(store, a_decision("D-0009", world.span.id, title="An imported decision"))

    assert store.next_id(RecordKind.DECISION) == "D-0010"


@pytest.mark.parametrize("kind", sorted(CONTENT_ADDRESSED_KINDS))
def test_a_content_addressed_id_is_not_the_stores_to_allocate(store, kind):
    # A span's identity is its byte range and a link's is its endpoints. If the
    # store handed out counters for these, two agents citing the same range
    # would produce two spans, and edge idempotence would be a clean-up job.
    with pytest.raises(StoreError, match="derived from their content"):
        store.next_id(kind)


def test_a_revision_does_not_consume_an_id(store, world: World):
    revise(store, world.decision, title="Ledger backend, revisited")

    assert store.next_id(RecordKind.DECISION) == "D-0003"


def test_a_retraction_does_not_consume_an_id(store, world: World):
    retract(store, Decision, world.decision.id)

    assert store.next_id(RecordKind.DECISION) == "D-0003"


def test_every_write_consumes_an_audit_event_id(store, world: World):
    before = store.next_id(RecordKind.AUDIT_EVENT)

    revise(store, world.decision, title="Ledger backend, revisited")

    assert store.next_id(RecordKind.AUDIT_EVENT) != before


# --- writing --------------------------------------------------------------


def test_add_returns_the_record_it_was_given(store, world: World):
    record = a_decision(store.next_id(RecordKind.DECISION), world.span.id)

    assert add(store, record) is record


def test_add_refuses_a_record_that_is_not_at_version_one(store, world: World):
    # The store assigns versions. A caller that nominates one has decided what
    # the current version is without reading it.
    record = a_decision(store.next_id(RecordKind.DECISION), world.span.id).model_copy(
        update={"version": 2}
    )

    with pytest.raises(StoreError, match="version 2"):
        add(store, record)


def test_add_refuses_an_id_that_is_already_in_the_store(store, world: World):
    with pytest.raises(DuplicateRecordError):
        add(store, world.decision)


def test_a_failed_write_leaves_no_row_and_no_audit_event(store, world: World):
    # The edge is valid as a sentence and dangling as a fact, so it fails after
    # its node and version rows are already in. An audit row that survived this
    # would describe a change that did not happen, which is worse than no audit
    # row at all: a trail with a hole in it reads like a complete one.
    dangling = Link.between(
        LinkType.ASSUMES,
        world.decision.id,
        AssumptionId("A-9999"),
        rationale="points at an assumption nobody wrote",
        confidence=0.5,
        created_by=ACTOR,
        created_at=WRITTEN_AT,
    )
    events_before = store.stats().audit_events

    with pytest.raises(DanglingEdgeError):
        add(store, dangling)

    assert not store.exists(dangling.id)
    assert store.current_version(dangling.id) is None
    assert store.stats().audit_events == events_before


# --- round-tripping -------------------------------------------------------


@pytest.mark.parametrize("attribute", WORLD_RECORDS)
def test_every_record_comes_back_as_the_value_that_went_in(store, world: World, attribute):
    original = getattr(world, attribute)

    assert store.get(type(original), original.id) == original


def test_an_edge_comes_back_as_the_value_that_went_in(store, world: World):
    written = store.links_from(world.decision.id)

    assert store.get(Link, written[0].id) == written[0]


def test_a_quantity_comes_back_as_the_exact_decimal_it_went_in_as(store, world: World):
    # Invariant 4. The scale matters as much as the value: an estimate of 1.10
    # weeks that returns as 1.1 has lost the precision it was stated to, and a
    # float round-trip would lose considerably more than that.
    estimate = Estimate(
        id=EstimateId(store.next_id(RecordKind.ESTIMATE)),
        subject="ledger cutover",
        owner="Sihan Udayaratna",
        work_class="data-modelling",
        active_quantity=Decimal("0.1234567890123456789012345"),
        blocked_quantity=Decimal("1.10"),
        unit=Unit.HOURS,
        confidence=0.4,
        estimated_at=WRITTEN_AT,
        span_id=world.span.id,
        created_at=WRITTEN_AT,
        created_by=ACTOR,
    )
    add(store, estimate)

    reloaded = store.require(Estimate, estimate.id)

    assert isinstance(reloaded.active_quantity, Decimal)
    assert str(reloaded.active_quantity) == "0.1234567890123456789012345"
    assert str(reloaded.blocked_quantity) == "1.10"


def test_a_findings_evidence_spans_keep_their_order(store, world: World):
    second = add(
        store, Span.covering(world.document, 15, 21, created_by=ACTOR, created_at=WRITTEN_AT)
    )
    finding = Finding(
        id=FindingId(store.next_id(RecordKind.FINDING)),
        kind=FindingKind.CONTRADICTION,
        subject_kind=RecordKind.DECISION,
        subject_id=world.decision.id,
        prosecution="the plan cites two different windows",
        severity=Severity.MEDIUM,
        confidence=0.7,
        evidence_span_ids=(second.id, world.span.id),
        detected_at=WRITTEN_AT,
        created_at=WRITTEN_AT,
        created_by=ACTOR,
    )
    add(store, finding)

    # Evidence lives in a child table, so its order is a column rather than a
    # property of the row. Reversing it here is what would catch that column
    # being dropped from the read.
    assert store.require(Finding, finding.id).evidence_span_ids == (second.id, world.span.id)


def test_a_finding_with_no_evidence_round_trips(store, world: World):
    # A calibration finding is computed from the store rather than quoted from a
    # document, so an empty tuple has to survive as an empty tuple.
    finding = Finding(
        id=FindingId(store.next_id(RecordKind.FINDING)),
        kind=FindingKind.CALIBRATION_BIAS,
        subject_kind=RecordKind.ESTIMATE,
        subject_id=world.estimate.id,
        prosecution="this estimator runs 1.8x optimistic on migrations",
        severity=Severity.LOW,
        confidence=0.55,
        detected_at=WRITTEN_AT,
        created_at=WRITTEN_AT,
        created_by=ACTOR,
    )
    add(store, finding)

    assert store.require(Finding, finding.id).evidence_span_ids == ()


def test_the_absent_fields_of_an_unresolved_outcome_stay_absent(store, world: World):
    # An unresolved outcome exists so that estimates which never resolved stay
    # in the calibration data. A NULL that came back as a zero would flatter the
    # curve with a perfect score for work that never finished.
    outcome = Outcome(
        id=OutcomeId(store.next_id(RecordKind.OUTCOME)),
        estimate_id=world.estimate.id,
        unit=Unit.WEEKS,
        match_quality=MatchQuality.UNRESOLVED,
        created_at=WRITTEN_AT,
        created_by=ACTOR,
    )
    add(store, outcome)

    reloaded = store.require(Outcome, outcome.id)

    assert reloaded.active_quantity is None
    assert reloaded.blocked_quantity is None
    assert reloaded.resolved_at is None
    assert reloaded.span_id is None


# --- reading --------------------------------------------------------------


def test_get_returns_none_for_a_record_that_is_not_there(store):
    assert store.get(Decision, "D-9999") is None


def test_get_returns_none_for_a_version_that_is_not_there(store, world: World):
    assert store.get(Decision, world.decision.id, version=7) is None


def test_require_names_what_was_missing(store):
    with pytest.raises(RecordNotFoundError) as caught:
        store.require(Decision, "D-9999")

    assert caught.value.kind is RecordKind.DECISION
    assert caught.value.record_id == "D-9999"


def test_require_names_the_version_that_was_missing(store, world: World):
    with pytest.raises(RecordNotFoundError, match="at version 7"):
        store.require(Decision, world.decision.id, version=7)


def test_current_version_is_none_before_the_first_write(store):
    assert store.current_version("D-9999") is None


def test_versions_are_returned_ascending(store, world: World):
    revise(store, world.decision, title="Ledger backend, revisited")
    retract(store, Decision, world.decision.id)

    assert store.versions(world.decision.id) == (1, 2, 3)


def test_versions_is_empty_for_a_record_that_is_not_there(store):
    assert store.versions("D-9999") == ()


def test_exists_answers_for_a_node_of_any_kind(store, world: World):
    assert store.exists(world.span.id)
    assert not store.exists("SPAN-ffffffffffffffff")


def test_list_all_returns_the_current_version_of_each_record(store, world: World):
    revised = revise(store, world.decision, title="Ledger backend, revisited")

    assert store.list_all(Decision) == (world.other_decision, revised)


def test_list_all_leaves_out_retracted_records(store, world: World):
    retract(store, Decision, world.decision.id)

    assert store.list_all(Decision) == (world.other_decision,)


def test_list_all_includes_retracted_records_when_asked(store, world: World):
    withdrawn = retract(store, Decision, world.decision.id)

    assert set(store.list_all(Decision, include_retracted=True)) == {
        world.other_decision,
        withdrawn,
    }


def test_list_all_is_empty_for_a_kind_nobody_has_written(store):
    assert store.list_all(Assumption) == ()


# --- revising -------------------------------------------------------------


def test_a_revision_writes_the_next_version(store, world: World):
    revised = revise(store, world.decision, title="Ledger backend, revisited")

    assert revised.version == 2
    assert store.current_version(world.decision.id) == 2
    assert store.get(Decision, world.decision.id) == revised


def test_the_version_a_revision_replaced_is_still_readable(store, world: World):
    revise(store, world.decision, title="Ledger backend, revisited")

    assert store.require(Decision, world.decision.id, version=1) == world.decision


def test_a_revision_is_stamped_with_the_time_it_was_written(store, world: World):
    # Unlike `add`, which keeps the record's own timestamp. A new version
    # carrying the previous version's `created_at` would be claiming to have
    # been written at a time it was not.
    revised = revise(store, world.decision, title="Ledger backend, revisited")

    assert revised.created_at == LATER
    assert store.require(Decision, world.decision.id, version=1).created_at == WRITTEN_AT


def test_a_revision_against_a_stale_version_is_refused(store, world: World):
    revise(store, world.decision, title="Ledger backend, revisited")

    # `world.decision` is still the version 1 the caller read.
    with pytest.raises(VersionConflictError) as caught:
        revise(store, world.decision, title="Ledger backend, again")

    assert caught.value.expected == 1
    assert caught.value.found == 2


def test_a_refused_revision_changes_nothing(store, world: World):
    revised = revise(store, world.decision, title="Ledger backend, revisited")
    events_before = store.stats().audit_events

    with pytest.raises(VersionConflictError):
        revise(store, world.decision, title="Ledger backend, again")

    assert store.get(Decision, world.decision.id) == revised
    assert store.versions(world.decision.id) == (1, 2)
    assert store.stats().audit_events == events_before


def test_a_record_that_was_never_written_cannot_be_revised(store, world: World):
    unwritten = a_decision("D-9999", world.span.id)

    with pytest.raises(RecordNotFoundError):
        revise(store, unwritten, title="Ledger backend, revisited")


# --- retracting -----------------------------------------------------------


def test_a_retraction_is_a_new_version_and_not_a_delete(store, world: World):
    withdrawn = retract(store, Decision, world.decision.id)

    assert withdrawn.version == 2
    assert withdrawn.retracted
    assert store.versions(world.decision.id) == (1, 2)


def test_a_retracted_record_is_still_readable_by_id(store, world: World):
    # A retraction is a statement about a record, not the disappearance of one.
    # A caller that asks for it by id gets it back, marked.
    retract(store, Decision, world.decision.id)

    assert store.require(Decision, world.decision.id).retracted
    assert not store.require(Decision, world.decision.id, version=1).retracted


def test_a_retraction_keeps_everything_else_the_record_said(store, world: World):
    withdrawn = retract(store, Decision, world.decision.id)

    assert withdrawn.title == world.decision.title
    assert withdrawn.rejected == world.decision.rejected


def test_a_record_that_was_never_written_cannot_be_retracted(store):
    with pytest.raises(RecordNotFoundError):
        retract(store, Decision, "D-9999")


# --- edges ----------------------------------------------------------------


def test_the_edges_of_a_node_are_readable_in_both_directions(store, world: World):
    assert [edge.target_id for edge in store.links_from(world.decision.id)] == [world.assumption.id]
    assert [edge.source_id for edge in store.links_to(world.estimate.id)] == [
        world.other_decision.id,
        world.assumption.id,
    ]
    assert len(store.links_touching(world.assumption.id)) == 2


def test_the_same_edge_asserted_twice_is_one_edge(store, world: World):
    # Content-addressed link ids are what make re-running an agent idempotent:
    # the second assertion arrives at the same id, so it is a revision of one
    # edge rather than a second edge saying the same thing.
    again = Link.between(
        LinkType.ASSUMES,
        world.decision.id,
        world.assumption.id,
        rationale="seen again on a second pass",
        confidence=0.95,
        created_by=ACTOR,
        created_at=LATER,
    )

    store.revise(again, actor=ACTOR, reason="the extractor ran twice", at=LATER)

    edges = store.links_from(world.decision.id, [LinkType.ASSUMES])
    assert len(edges) == 1
    assert edges[0].rationale == "seen again on a second pass"
    assert edges[0].version == 2


def test_re_adding_an_edge_instead_of_revising_it_is_refused(store, world: World):
    with pytest.raises(DuplicateRecordError):
        add(store, store.links_from(world.decision.id)[0])


def test_a_retracted_edge_is_no_longer_part_of_the_graph(store, world: World):
    edge = store.links_from(world.decision.id)[0]

    retract(store, Link, edge.id)

    assert store.links_from(world.decision.id) == ()
    # The decision only ever reached the estimate through that edge, so
    # withdrawing it takes the decision out of the estimate's blast radius while
    # leaving the two nodes still connected by other edges where they are.
    assert {reached.id for reached in store.impacted_by(world.estimate.id)} == {
        world.assumption.id,
        world.other_decision.id,
    }


def test_the_walks_are_reachable_from_the_repository(store, world: World):
    # The traversal itself is `tests/store/test_graph.py`. What is asserted here
    # is only that a caller holding a repository does not need the connection.
    impacted = {reached.id for reached in store.impacted_by(world.estimate.id)}

    assert impacted == {world.assumption.id, world.decision.id, world.other_decision.id}
    assert {reached.id for reached in store.depends_on(world.decision.id)} == {
        world.assumption.id,
        world.estimate.id,
    }


def test_the_audit_trail_is_reachable_from_the_repository(store, world: World):
    revise(store, world.decision, title="Ledger backend, revisited")

    events = store.audit_for(world.decision.id)

    assert [event.entity_version for event in events] == [1, 2]


# --- search and stats -----------------------------------------------------


def test_search_finds_a_current_record(store, world: World):
    revise(store, world.decision, title="kingfisher")

    hits = store.search("kingfisher")

    assert [hit.record_id for hit in hits] == [world.decision.id]
    assert hits[0].field == "title"


def test_search_does_not_return_a_superseded_version(store, world: World):
    revised = revise(store, world.decision, title="kingfisher")
    revise(store, revised, title="albatross")

    # Nothing is removed from the index, because nothing is removed from the
    # store. The join to `record_head` is the only thing keeping the old title
    # out of the answer.
    assert store.search("kingfisher") == ()
    assert [hit.record_id for hit in store.search("albatross")] == [world.decision.id]


def test_search_does_not_return_a_retracted_record(store, world: World):
    revise(store, world.other_decision, title="petrel")
    assert store.search("petrel") != ()

    retract(store, Decision, world.other_decision.id)

    assert store.search("petrel") == ()


def test_search_returns_at_most_the_limit(store, world: World):
    assert len(store.search("ledger OR migration OR weeks", limit=1)) == 1


def test_stats_counts_the_current_unretracted_records(store, world: World):
    counts = store.stats().records

    assert counts == {
        RecordKind.DOCUMENT: 1,
        RecordKind.SPAN: 1,
        RecordKind.DECISION: 2,
        RecordKind.ASSUMPTION: 1,
        RecordKind.ESTIMATE: 1,
        RecordKind.OUTCOME: 1,
        RecordKind.FINDING: 1,
        RecordKind.LINK: 3,
    }


def test_stats_counts_the_edges_by_type(store, world: World):
    assert store.stats().links == {
        LinkType.ASSUMES: 1,
        LinkType.ESTIMATED_AS: 1,
        LinkType.JUSTIFIED_BY: 1,
    }


def test_stats_counts_every_version_ever_written(store, world: World):
    before = store.stats()

    revise(store, world.decision, title="Ledger backend, revisited")

    # The gap between the version count and the record count is the store's
    # history, which is the number `praxis store stats` exists to show.
    assert store.stats().versions == before.versions + 1
    assert store.stats().records[RecordKind.DECISION] == 2


def test_stats_stops_counting_a_record_once_it_is_retracted(store, world: World):
    retract(store, Decision, world.decision.id)

    stats = store.stats()

    assert stats.records[RecordKind.DECISION] == 1
    assert stats.versions == 12


def test_stats_counts_one_audit_event_per_write(store, world: World):
    # Eleven records in the fixture, eleven writes, eleven events.
    assert store.stats().audit_events == 11


def test_stats_reports_the_schema_the_store_is_at(store):
    assert store.stats().schema_version == latest_version()


# --- opening a store ------------------------------------------------------


def test_open_repository_creates_and_migrates_the_store(tmp_path):
    settings = Settings(data_dir=tmp_path / "praxis")

    repository = open_repository(settings)

    try:
        assert settings.db_path.exists()
        assert repository.stats().schema_version == latest_version()
    finally:
        repository.close()


def test_open_repository_finds_what_a_previous_one_wrote(tmp_path):
    settings = Settings(data_dir=tmp_path / "praxis")
    first = open_repository(settings)
    try:
        document_id = first.next_id(RecordKind.DOCUMENT)
    finally:
        first.close()

    second = open_repository(settings)

    try:
        assert second.next_id(RecordKind.DOCUMENT) == document_id
        assert second.stats().schema_version == latest_version()
    finally:
        second.close()


def test_open_repository_reports_a_missing_store_rather_than_creating_one(tmp_path):
    # `praxis store stats` passes create=False so that a mistyped
    # PRAXIS_DATA_DIR is an error the owner can read, not an empty database
    # that answers every question with zero.
    settings = Settings(data_dir=tmp_path / "nowhere")

    with pytest.raises(StoreNotInitialisedError):
        open_repository(settings, create=False)

    assert not settings.db_path.exists()
