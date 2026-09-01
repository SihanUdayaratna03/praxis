"""The dashboard's reads, against the fixture graph the other store tests use.

These exist mostly to hold one line: every read narrows to the current,
unretracted version. A dashboard that counts superseded rows shows a number
nobody can reconcile with the CLI, and the append-only schema makes that the
easy mistake rather than the unlikely one.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from praxis.domain.enums import AssumptionStatus, RecordKind
from praxis.domain.records import Assumption, Decision
from praxis.store import dashboard
from praxis.store.repository import Repository

from tests.store.conftest import ACTOR, WRITTEN_AT, World, build_world


def test_the_timeline_returns_the_newest_write_first(store: Repository, world: World):
    page = dashboard.audit_timeline(store)
    assert page.events
    ordinals = [event.id for event in page.events]
    assert ordinals == sorted(ordinals, reverse=True)
    assert page.total == len(page.events)
    assert page.next_before is None


def test_the_timeline_pages_by_ordinal_without_repeating_a_row(store: Repository, world: World):
    seen: list[str] = []
    cursor: int | None = None
    while True:
        page = dashboard.audit_timeline(store, limit=2, before=cursor)
        seen.extend(event.id for event in page.events)
        if page.next_before is None:
            break
        cursor = page.next_before
    assert len(seen) == len(set(seen)), "a page boundary repeated a row"
    assert len(seen) == page.total


@pytest.mark.parametrize(("asked", "expected"), [(None, 50), (0, 1), (-5, 1), (10_000, 500)])
def test_a_page_size_is_clamped(asked: int | None, expected: int):
    assert dashboard._page_size(asked) == expected


def test_the_decision_index_counts_the_assumptions_a_decision_rests_on(
    store: Repository, world: World
):
    summaries = {summary.decision.id: summary for summary in dashboard.decision_index(store)}
    assert summaries[world.decision.id].assumptions == 1
    # The second decision is joined to the estimate by justified_by, not assumes.
    assert summaries[world.other_decision.id].assumptions == 0


def test_the_decision_index_reports_a_breached_assumption_as_risk(store: Repository, world: World):
    before = {s.decision.id: s for s in dashboard.decision_index(store)}
    assert before[world.decision.id].breached == 0
    assert before[world.decision.id].at_risk is False

    store.revise(
        world.assumption.model_copy(
            update={
                "status": AssumptionStatus.BREACHED,
                "last_evaluated_at": WRITTEN_AT,
            }
        ),
        actor=ACTOR,
        reason="the monitor found the predicate false",
        at=WRITTEN_AT,
    )

    after = {s.decision.id: s for s in dashboard.decision_index(store)}
    assert after[world.decision.id].breached == 1
    assert after[world.decision.id].at_risk is True


def test_a_revised_assumption_is_counted_once(store: Repository, world: World):
    """The failure this whole module is written against."""
    latest = world.assumption
    for version in range(3):
        latest = store.revise(
            latest.model_copy(
                update={
                    "status": AssumptionStatus.BREACHED,
                    "last_evaluated_at": WRITTEN_AT + timedelta(minutes=version),
                }
            ),
            actor=ACTOR,
            reason="re-evaluated",
            at=WRITTEN_AT + timedelta(minutes=version),
        )
    assert latest.version == 4
    summaries = {s.decision.id: s for s in dashboard.decision_index(store)}
    assert summaries[world.decision.id].breached == 1
    assert dashboard.assumption_health(store)[AssumptionStatus.BREACHED] == 1


def test_the_decision_index_counts_findings_about_the_decision(store: Repository, world: World):
    summaries = {s.decision.id: s for s in dashboard.decision_index(store)}
    # The fixture's only finding is about the assumption, not either decision.
    assert summaries[world.decision.id].findings == 0


def test_the_decision_index_is_newest_first(store: Repository):
    world = build_world(store)
    later = store.add(
        world.decision.model_copy(
            update={
                "id": store.next_id(RecordKind.DECISION),
                "title": "A later decision",
                "decided_at": WRITTEN_AT + timedelta(days=1),
            }
        ),
        actor=ACTOR,
        reason="written by the test",
        at=WRITTEN_AT,
    )
    assert dashboard.decision_index(store)[0].decision.id == later.id


def test_assumption_health_reports_every_status_including_the_empty_ones(
    store: Repository, world: World
):
    health = dashboard.assumption_health(store)
    assert set(health) == set(AssumptionStatus)
    assert health[AssumptionStatus.UNVERIFIED] == 1
    assert health[AssumptionStatus.BREACHED] == 0


def test_the_reads_are_empty_rather_than_broken_on_a_fresh_store(store: Repository):
    assert dashboard.decision_index(store) == ()
    assert dashboard.audit_timeline(store).events == ()
    assert dashboard.audit_timeline(store).total == 0
    assert sum(dashboard.assumption_health(store).values()) == 0


def test_a_retracted_record_leaves_the_index(store: Repository, world: World):
    store.retract(
        Decision,
        world.other_decision.id,
        actor=ACTOR,
        reason="superseded by a later plan",
        at=WRITTEN_AT,
    )
    listed = {s.decision.id for s in dashboard.decision_index(store)}
    assert world.other_decision.id not in listed
    assert world.decision.id in listed


def test_a_retracted_assumption_leaves_the_health_counts(store: Repository, world: World):
    store.retract(
        Assumption,
        world.assumption.id,
        actor=ACTOR,
        reason="withdrawn by the curator",
        at=WRITTEN_AT,
    )
    assert sum(dashboard.assumption_health(store).values()) == 0
