"""Edges, walks, and the one query the schema documents.

Two things are being defended here. The first is that a traversal terminates: the
recursive CTE de-duplicates on `(id, kind, depth)`, so a cycle produces the same
node at increasing depths rather than being folded away, and only the depth bound
stops it. The vocabulary's direction rule makes a cycle unconstructible over the
dependency types -- which is exactly why the cycle test uses `contradicts`, a
type with no direction rule to protect it.

The second is `test_the_walk_agrees_with_the_query_the_schema_documents`. The
comment above the `dependency_edge` view in `001_core.sql` claims Phase 8's
question is eight lines of SQL against that view. That test reads the claim out
of the migration file and runs it, so the comment is executable documentation
rather than a promise nobody checks.
"""

from __future__ import annotations

import pytest
from praxis.domain.enums import RecordKind
from praxis.domain.ids import AssumptionId
from praxis.domain.links import DEPENDENCY_LINK_TYPES, LinkType
from praxis.domain.records import Assumption, Decision, Link
from praxis.store import graph
from praxis.store.migrations import discover_migrations
from praxis.store.repository import Repository

from tests.store.conftest import ACTOR, WRITTEN_AT, World, link
from tests.store.test_repository import retract

DOCUMENTED_QUERY_START = "WITH RECURSIVE impacted("
DOCUMENTED_QUERY_END = "GROUP BY id, kind;"


def an_assumption(store: Repository, span_id: str, statement: str) -> Assumption:
    """A second assumption, for the tests that need two of something."""
    record = Assumption(
        id=AssumptionId(store.next_id(RecordKind.ASSUMPTION)),
        statement=statement,
        predicate="migration_weeks > 6",
        expiry_condition="when(phases_completed >= 6)",
        span_id=span_id,
        confidence=0.4,
        created_at=WRITTEN_AT,
        created_by=ACTOR,
    )
    return store.add(record, actor=ACTOR, reason="written by a test", at=WRITTEN_AT)


def documented_query() -> str:
    """Lift the impact query out of the comment that documents it.

    Read from the migration this build ships rather than from a copy pasted
    here, so the test fails if the comment and the code drift apart -- which is
    the only way a documented query stays true.
    """
    lines = discover_migrations()[0].sql.splitlines()
    start = next(i for i, line in enumerate(lines) if DOCUMENTED_QUERY_START in line)
    end = next(i for i, line in enumerate(lines) if DOCUMENTED_QUERY_END in line)
    return "\n".join(line.removeprefix("--") for line in lines[start : end + 1])


def reached(nodes: tuple[graph.ReachedNode, ...]) -> dict[str, int]:
    """A walk's result as id -> depth, which is what the assertions are about."""
    return {node.id: node.depth for node in nodes}


# --- reading edges --------------------------------------------------------


def test_edges_are_read_in_the_direction_they_point(store, world: World):
    assert [edge.target_id for edge in graph.links_from(store.connection, world.decision.id)] == [
        world.assumption.id
    ]
    assert graph.links_to(store.connection, world.decision.id) == ()


def test_an_edge_comes_back_whole(store, world: World):
    edge = graph.links_from(store.connection, world.assumption.id)[0]

    assert isinstance(edge, Link)
    assert edge.link_type is LinkType.ESTIMATED_AS
    assert edge.source_id == world.assumption.id
    assert edge.target_id == world.estimate.id
    assert edge.rationale == "asserted by the fixture"


def test_the_edges_into_a_node_are_ordered_by_id(store, world: World):
    edges = graph.links_to(store.connection, world.estimate.id)

    # Ordered so that two runs over one corpus produce the same answer in the
    # same order -- the determinism the eval harness rests on starts here.
    assert [edge.id for edge in edges] == sorted(edge.id for edge in edges)


def test_a_symmetric_edge_is_found_from_either_end(store, world: World):
    # `contradicts` is stored once, in whichever direction the writing agent saw
    # it. A query that looked only one way would miss half of them, which is the
    # whole reason `links_touching` exists.
    other = an_assumption(store, world.span.id, "the migration is long")
    link(store, LinkType.CONTRADICTS, world.assumption.id, other.id)

    assert len(graph.links_touching(store.connection, other.id)) == 1
    assert graph.links_from(store.connection, other.id) == ()


def test_touching_finds_both_ends_at_once(store, world: World):
    assert len(graph.links_touching(store.connection, world.assumption.id)) == 2


def test_a_type_filter_narrows_the_answer(store, world: World):
    assert graph.links_from(store.connection, world.decision.id, [LinkType.ASSUMES]) != ()
    assert graph.links_from(store.connection, world.decision.id, [LinkType.SUPERSEDES]) == ()


def test_asking_for_no_types_asks_for_nothing(store, world: World):
    # Distinct from passing None, which means "every type". An empty filter that
    # silently meant "everything" would turn a caller's empty configuration into
    # the widest possible query.
    assert graph.links_from(store.connection, world.decision.id, []) == ()
    assert graph.links_to(store.connection, world.estimate.id, []) == ()
    assert graph.links_touching(store.connection, world.assumption.id, []) == ()


def test_a_node_with_no_edges_has_no_edges(store, world: World):
    assert graph.links_touching(store.connection, world.outcome.id) == ()


def test_a_retracted_edge_is_gone_from_every_read(store, world: World):
    edge = graph.links_from(store.connection, world.decision.id)[0]

    retract(store, Link, edge.id)

    assert graph.links_from(store.connection, world.decision.id) == ()
    assert graph.links_to(store.connection, world.assumption.id) == ()
    assert graph.links_touching(store.connection, world.assumption.id) == (
        graph.links_from(store.connection, world.assumption.id)[0],
    )


def test_only_the_current_version_of_an_edge_is_read(store, world: World):
    edge = graph.links_from(store.connection, world.decision.id)[0]

    store.revise(
        edge.model_copy(update={"rationale": "restated on a second pass"}),
        actor=ACTOR,
        reason="the extractor ran twice",
        at=WRITTEN_AT,
    )

    current = graph.links_from(store.connection, world.decision.id)
    assert len(current) == 1
    assert current[0].version == 2


# --- walking --------------------------------------------------------------


def test_a_walk_finds_what_rests_on_a_node(store, world: World):
    # The fusion query: the estimate missed, so what has to be re-read?
    assert reached(graph.impacted_by(store.connection, world.estimate.id)) == {
        world.assumption.id: 1,
        world.other_decision.id: 1,
        world.decision.id: 2,
    }


def test_a_walk_finds_what_a_node_rests_on(store, world: World):
    assert reached(graph.depends_on(store.connection, world.decision.id)) == {
        world.assumption.id: 1,
        world.estimate.id: 2,
    }


def test_a_node_is_not_part_of_its_own_blast_radius(store, world: World):
    assert world.estimate.id not in reached(graph.impacted_by(store.connection, world.estimate.id))
    assert world.decision.id not in reached(graph.depends_on(store.connection, world.decision.id))


def test_a_walk_reports_the_shortest_route_it_found(store, world: World):
    # The decision reaches the estimate through its assumption, two hops away.
    assert reached(graph.impacted_by(store.connection, world.estimate.id))[world.decision.id] == 2

    # Given a second, direct route, the distance is the shorter one. Depth ranks
    # a blast radius, so a node reported at its longest distance would sort
    # below things that depend on it far less directly.
    link(store, LinkType.JUSTIFIED_BY, world.decision.id, world.estimate.id)

    assert reached(graph.impacted_by(store.connection, world.estimate.id))[world.decision.id] == 1


def test_a_walk_stops_at_the_depth_it_was_given(store, world: World):
    at_one = reached(graph.impacted_by(store.connection, world.estimate.id, max_depth=1))

    assert at_one == {world.assumption.id: 1, world.other_decision.id: 1}


def test_a_walk_of_no_depth_goes_nowhere(store, world: World):
    assert graph.impacted_by(store.connection, world.estimate.id, max_depth=0) == ()
    assert graph.depends_on(store.connection, world.decision.id, max_depth=-1) == ()


def test_a_walk_over_no_types_goes_nowhere(store, world: World):
    assert graph.impacted_by(store.connection, world.estimate.id, types=()) == ()
    assert graph.depends_on(store.connection, world.decision.id, types=()) == ()


def test_a_walk_follows_only_the_types_it_was_given(store, world: World):
    # `contradicts` and `supersedes` are not dependency: superseding a decision
    # does not make the new one rest on the old.
    other = an_assumption(store, world.span.id, "the migration is long")
    # Pointing at the fixture's assumption, so that a walk towards dependents
    # would step onto it if the type were followed.
    link(store, LinkType.CONTRADICTS, other.id, world.assumption.id)

    assert other.id not in reached(graph.impacted_by(store.connection, world.estimate.id))
    assert other.id in reached(
        graph.impacted_by(
            store.connection,
            world.estimate.id,
            types=[*DEPENDENCY_LINK_TYPES, LinkType.CONTRADICTS],
        )
    )


def test_a_cycle_terminates_and_stays_bounded(store, world: World):
    # A dependency cycle cannot be built: every dependency edge points from the
    # dependent to the depended-upon, and the grammar enforces it. `contradicts`
    # has no such rule, so it is what a cycle can be made of -- and the walk has
    # to survive one that a future edge type might allow by accident.
    other = an_assumption(store, world.span.id, "the migration is long")
    link(store, LinkType.CONTRADICTS, world.assumption.id, other.id)
    link(store, LinkType.CONTRADICTS, other.id, world.assumption.id)

    walked = graph.impacted_by(
        store.connection, world.assumption.id, types=[LinkType.CONTRADICTS], max_depth=4
    )

    assert reached(walked) == {other.id: 1, world.assumption.id: 2}
    assert all(node.depth <= 4 for node in walked)


def test_a_walk_does_not_follow_a_retracted_edge(store, world: World):
    edge = graph.links_to(store.connection, world.estimate.id, [LinkType.ESTIMATED_AS])[0]

    retract(store, Link, edge.id)

    assert reached(graph.impacted_by(store.connection, world.estimate.id)) == {
        world.other_decision.id: 1
    }


def test_a_retracted_node_is_nobodys_blast_radius(store, world: World):
    retract(store, Decision, world.other_decision.id)

    assert world.other_decision.id not in reached(
        graph.impacted_by(store.connection, world.estimate.id)
    )


def test_a_walk_still_passes_through_a_retracted_node(store, world: World):
    # The honest behaviour: the withdrawn assumption is not in the answer, but
    # the decision that rests on it still is. An edge that outlives its endpoint
    # is a caller's bug, and hiding what is behind it here would hide the bug.
    retract(store, Assumption, world.assumption.id)

    walked = reached(graph.impacted_by(store.connection, world.estimate.id))

    assert world.assumption.id not in walked
    assert walked[world.decision.id] == 2


# --- the query the schema documents ---------------------------------------


def test_the_documented_query_is_still_in_the_schema(store):
    query = documented_query()

    assert "dependency_edge" in query
    assert ":estimate_id" in query


def test_the_walk_agrees_with_the_query_the_schema_documents(store, world: World):
    link(store, LinkType.JUSTIFIED_BY, world.decision.id, world.estimate.id)

    rows = store.connection.execute(
        documented_query(),
        {"estimate_id": world.estimate.id, "max_depth": graph.DEFAULT_MAX_DEPTH},
    ).fetchall()

    by_hand = {row[0]: row[2] for row in rows}
    assert by_hand == reached(graph.impacted_by(store.connection, world.estimate.id))


@pytest.mark.parametrize("max_depth", [1, 2, 16])
def test_the_two_agree_at_every_depth(store, world: World, max_depth):
    rows = store.connection.execute(
        documented_query(), {"estimate_id": world.estimate.id, "max_depth": max_depth}
    ).fetchall()

    by_hand = {row[0]: row[2] for row in rows}
    assert by_hand == reached(
        graph.impacted_by(store.connection, world.estimate.id, max_depth=max_depth)
    )
