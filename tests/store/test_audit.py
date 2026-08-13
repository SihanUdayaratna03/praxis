"""One event per write, written with the write or not at all.

The trail's whole value is that it has no holes. A missing row would not look
like a missing row -- it would look like a change that never happened, in a log
that appears complete -- so the tests here are about the two ways a hole could
appear: a mutation that records nothing, and a mutation whose event commits
separately from the change it describes and can therefore be lost on its own.

The strongest assertion in the file is
`test_every_version_ever_written_has_an_event_of_its_own`, which compares the
audit table against `record_version` directly rather than counting writes the
test itself made. That is the invariant; the rest is how it can break.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from praxis.domain.enums import AuditAction, RecordKind
from praxis.domain.ids import AssumptionId, AuditEventId
from praxis.domain.links import LinkType
from praxis.domain.records import Decision, Link
from praxis.store import audit
from praxis.store.connection import transaction
from praxis.store.errors import DanglingEdgeError
from praxis.store.repository import Repository
from pydantic import ValidationError

from tests.store.conftest import ACTOR, WRITTEN_AT, World
from tests.store.test_repository import LATER, a_decision, add, retract, revise

RUN = "run-2026-08-14-01"
OTHER_RUN = "run-2026-08-14-02"

FIXTURE_WRITES = 11
"""Records in the `world` fixture: eight records and three edges."""


def events(store: Repository) -> list[tuple[str, int]]:
    """Every audited (entity, version) pair in the store."""
    rows = store.connection.execute("SELECT entity_id, entity_version FROM audit_event").fetchall()
    return [(row["entity_id"], row["entity_version"]) for row in rows]


def written_versions(store: Repository) -> list[tuple[str, int]]:
    """Every (entity, version) pair the store actually holds."""
    rows = store.connection.execute("SELECT id, version FROM record_version").fetchall()
    return [(row["id"], row["version"]) for row in rows]


# --- one event per mutation -----------------------------------------------


def test_a_write_records_exactly_one_event(store, world: World):
    record = add(store, a_decision(store.next_id(RecordKind.DECISION), world.span.id))

    assert len(store.audit_for(record.id)) == 1


def test_the_event_says_what_the_write_was(store, world: World):
    record = add(store, a_decision(store.next_id(RecordKind.DECISION), world.span.id))

    event = store.audit_for(record.id)[0]

    assert event.action is AuditAction.CREATED
    assert event.entity_kind is RecordKind.DECISION
    assert event.entity_id == record.id
    assert event.entity_version == 1
    assert event.actor == ACTOR
    assert event.reason == "written by a test"


def test_a_revision_is_recorded_as_a_revision(store, world: World):
    revise(store, world.decision, title="Ledger backend, revisited")

    second = store.audit_for(world.decision.id)[1]

    assert second.action is AuditAction.REVISED
    assert second.entity_version == 2


def test_a_retraction_is_recorded_as_a_retraction(store, world: World):
    retract(store, Decision, world.decision.id)

    second = store.audit_for(world.decision.id)[1]

    # Not `deleted`, because nothing was deleted. The retracted version is a
    # version like any other and this row is what says why it exists.
    assert second.action is AuditAction.RETRACTED
    assert second.entity_version == 2


def test_each_mutation_adds_one_event_and_no_more(store, world: World):
    before = audit.event_count(store.connection)

    record = add(store, a_decision(store.next_id(RecordKind.DECISION), world.span.id))
    revise(store, record, title="Ledger backend, revisited")
    retract(store, Decision, record.id)

    assert audit.event_count(store.connection) == before + 3


def test_the_fixture_recorded_one_event_per_write(store, world: World):
    assert audit.event_count(store.connection) == FIXTURE_WRITES


def test_every_version_ever_written_has_an_event_of_its_own(store, world: World):
    revise(store, world.decision, title="Ledger backend, revisited")
    retract(store, Decision, world.other_decision.id)

    # Not "the same number of rows" -- the same rows. A trail that matched only
    # in count could still be describing the wrong versions.
    assert sorted(events(store)) == sorted(written_versions(store))


# --- the event and its change are one transaction -------------------------


def test_a_write_that_fails_leaves_the_trail_untouched(store, world: World):
    # A dangling edge fails after its node and version rows are in, so this is
    # the case where a separately committed audit row would survive a change
    # that did not happen.
    dangling = Link.between(
        LinkType.ASSUMES,
        world.decision.id,
        AssumptionId("A-9999"),
        rationale="points at an assumption nobody wrote",
        confidence=0.5,
        created_by=ACTOR,
        created_at=WRITTEN_AT,
    )
    before = audit.event_count(store.connection)

    with pytest.raises(DanglingEdgeError):
        add(store, dangling)

    assert audit.event_count(store.connection) == before
    assert store.audit_for(dangling.id) == ()


def test_an_event_cannot_survive_a_rolled_back_caller(store, world: World):
    # The repository's own transaction joins the caller's rather than nesting,
    # so a caller that fails after a successful write takes the audit row with
    # it. A savepoint the inner write could release on its own would be a way to
    # commit half of this.
    record = a_decision(store.next_id(RecordKind.DECISION), world.span.id)
    before = audit.event_count(store.connection)

    with pytest.raises(RuntimeError), transaction(store.connection):
        add(store, record)
        message = "the agent fell over after the write"
        raise RuntimeError(message)

    assert store.get(Decision, record.id) is None
    assert audit.event_count(store.connection) == before


def test_a_successful_write_commits_both(store, world: World):
    record = a_decision(store.next_id(RecordKind.DECISION), world.span.id)

    with transaction(store.connection):
        add(store, record)

    assert store.get(Decision, record.id) == record
    assert len(store.audit_for(record.id)) == 1


# --- ordering -------------------------------------------------------------


def test_events_are_ordered_by_version_even_when_the_clock_agrees(store, world: World):
    # Every write in the fixture shares one timestamp, which is the point:
    # ordering by `occurred_at` would be arbitrary here, and the trail's order
    # is the order things happened in.
    store.revise(
        world.decision.model_copy(update={"title": "Ledger backend, revisited"}),
        actor=ACTOR,
        reason="revised in the same instant",
        at=WRITTEN_AT,
    )

    trail = store.audit_for(world.decision.id)

    assert [event.entity_version for event in trail] == [1, 2]
    assert [event.occurred_at for event in trail] == [WRITTEN_AT, WRITTEN_AT]


def test_the_trail_of_a_record_nobody_wrote_is_empty(store, world: World):
    assert store.audit_for("D-9999") == ()


# --- allocation -----------------------------------------------------------


def test_event_ids_are_allocated_in_write_order(store, world: World):
    # The document is the fixture's first write, so it holds the first event.
    assert store.audit_for(world.document.id)[0].id == "AUD-0001"
    assert store.next_id(RecordKind.AUDIT_EVENT) == "AUD-0012"


def test_the_event_counter_is_a_number_and_not_a_string(store, world: World):
    # Past four digits the ids stop sorting the way the numbers do: 'AUD-9999'
    # is lexically greater than 'AUD-10000'. Allocating from MAX(id) would hand
    # AUD-10000 out a second time here.
    store.connection.execute(
        "INSERT INTO audit_event "
        "(id, ordinal, occurred_at, actor, action, entity_id, entity_kind, "
        "entity_version, reason, run_id) "
        "VALUES ('AUD-10000', 10000, ?, 'importer', 'created', ?, 'decision', 1, 'imported', NULL)",
        (WRITTEN_AT.isoformat(), world.decision.id),
    )

    assert store.next_id(RecordKind.AUDIT_EVENT) == "AUD-10001"


# --- runs -----------------------------------------------------------------


def test_a_run_can_be_asked_what_it_changed(store, world: World):
    first = a_decision(store.next_id(RecordKind.DECISION), world.span.id, title="From the run")
    store.add(first, actor=ACTOR, reason="written by a run", at=WRITTEN_AT, run_id=RUN)
    store.revise(first, actor=ACTOR, reason="revised by the same run", at=LATER, run_id=RUN)

    in_run = audit.events_in_run(store.connection, RUN)

    assert [event.entity_version for event in in_run] == [1, 2]
    assert {event.entity_id for event in in_run} == {first.id}


def test_a_run_does_not_see_another_runs_writes(store, world: World):
    mine = a_decision(store.next_id(RecordKind.DECISION), world.span.id, title="From my run")
    store.add(mine, actor=ACTOR, reason="written by a run", at=WRITTEN_AT, run_id=RUN)
    theirs = a_decision(store.next_id(RecordKind.DECISION), world.span.id, title="From their run")
    store.add(theirs, actor=ACTOR, reason="written by another run", at=WRITTEN_AT, run_id=OTHER_RUN)

    assert [event.entity_id for event in audit.events_in_run(store.connection, RUN)] == [mine.id]
    assert [event.entity_id for event in audit.events_in_run(store.connection, OTHER_RUN)] == [
        theirs.id
    ]


def test_a_write_outside_a_run_belongs_to_no_run(store, world: World):
    # The fixture writes without a run id, and Phase 4 is where runs get one.
    assert audit.events_in_run(store.connection, RUN) == ()
    assert store.audit_for(world.decision.id)[0].run_id is None


# --- timestamps -----------------------------------------------------------


def test_an_event_defaults_to_the_time_of_the_write(store, world: World):
    record = a_decision(store.next_id(RecordKind.DECISION), world.span.id)

    before = datetime.now(UTC)
    store.add(record, actor=ACTOR, reason="written without a stated time")
    after = datetime.now(UTC)

    occurred_at = store.audit_for(record.id)[0].occurred_at
    assert before <= occurred_at <= after
    assert occurred_at.tzinfo is not None


def test_when_a_record_was_written_and_when_it_says_it_was_are_two_things(store, world: World):
    # `add` keeps the record's own `created_at` -- the caller knows when the
    # thing was written better than the store does -- while the event records
    # when the store was told about it.
    record = a_decision(store.next_id(RecordKind.DECISION), world.span.id)

    store.add(record, actor=ACTOR, reason="written later than it claims", at=LATER)

    assert store.require(Decision, record.id).created_at == WRITTEN_AT
    assert store.audit_for(record.id)[0].occurred_at == LATER


# --- building an event without writing one --------------------------------


def test_building_an_event_writes_nothing(store, world: World):
    # Separate steps so the repository can validate the whole row before any of
    # it reaches the database, and so a caller can see what would be recorded.
    before = audit.event_count(store.connection)

    audit.build_event(
        event_id=AuditEventId("AUD-9999"),
        action=AuditAction.CREATED,
        entity_kind=RecordKind.DECISION,
        entity_id=world.decision.id,
        entity_version=1,
        actor=ACTOR,
        reason="considered and not written",
        occurred_at=WRITTEN_AT,
    )

    assert audit.event_count(store.connection) == before


def test_an_event_with_no_reason_is_not_an_event(store, world: World):
    # The reason is the one field on this row a human will actually read.
    with pytest.raises(ValidationError):
        audit.build_event(
            event_id=AuditEventId("AUD-9999"),
            action=AuditAction.CREATED,
            entity_kind=RecordKind.DECISION,
            entity_id=world.decision.id,
            entity_version=1,
            actor=ACTOR,
            reason="   ",
            occurred_at=WRITTEN_AT,
        )
