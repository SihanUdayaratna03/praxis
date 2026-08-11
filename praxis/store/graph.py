"""Edges, and walking them.

The whole of Phase 8's question is one recursive CTE in this module, and it is
one rather than two because `praxis.domain.links` enforces that every dependency
edge points from the dependent to the depended-upon:

    Decision --assumes--> Assumption --estimated_as--> Estimate
    Decision --justified_by--> Estimate | Document | Span

So "this estimate missed -- what rests on it?" is reverse reachability, "what
does this decision rest on?" is the same walk forward, and neither needs a
special case per hop. `impacted_by` and `depends_on` differ by two column names.

**The depth bound is load-bearing, not a safety net.** The CTE de-duplicates on
`(id, kind, depth)`, so a cycle produces the same node at increasing depths
forever rather than being folded away. Dropping depth would make termination
structural but would also throw away the distance, which is what ranks a blast
radius. The bound is the price of keeping it, and every walk is bounded.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from praxis.domain.enums import RecordKind
from praxis.domain.ids import NodeId, kind_of
from praxis.domain.links import DEPENDENCY_LINK_TYPES, LinkType
from praxis.domain.records import Link
from praxis.store.errors import translating_sqlite_errors
from praxis.store.mapping import from_row

DEFAULT_MAX_DEPTH: Final = 16
"""How far a walk goes before it stops.

Generously past anything the vocabulary produces naturally -- decision to
assumption to estimate is two hops -- and small enough that a cyclic graph costs
a bounded query rather than a hung agent.
"""

_LINK_COLUMNS: Final = (
    "id, version, link_type, source_id, source_kind, target_id, target_kind, "
    "confidence, rationale, span_id, retracted, created_at, created_by"
)


@dataclass(frozen=True, slots=True)
class ReachedNode:
    """A node found by a walk, and how far away it was.

    Attributes:
        id: The node's id.
        kind: Its record kind.
        depth: Hops from the root along the shortest path found. Never zero --
            the root is not part of its own result.
    """

    id: NodeId
    kind: RecordKind
    depth: int


# -- edges -----------------------------------------------------------------


def links_from(
    connection: sqlite3.Connection, node_id: NodeId, types: Iterable[LinkType] | None = None
) -> tuple[Link, ...]:
    """Current, unretracted edges whose source is this node."""
    return _links(connection, "source_id = ?", (node_id,), types)


def links_to(
    connection: sqlite3.Connection, node_id: NodeId, types: Iterable[LinkType] | None = None
) -> tuple[Link, ...]:
    """Current, unretracted edges whose target is this node."""
    return _links(connection, "target_id = ?", (node_id,), types)


def links_touching(
    connection: sqlite3.Connection, node_id: NodeId, types: Iterable[LinkType] | None = None
) -> tuple[Link, ...]:
    """Current, unretracted edges at either end of this node.

    What `praxis.domain.links` promises for symmetric types: `contradicts` is
    stored once, in whichever direction the writing agent saw it, so a query
    that looked only one way would miss half of them.
    """
    return _links(connection, "(source_id = ? OR target_id = ?)", (node_id, node_id), types)


def _links(
    connection: sqlite3.Connection,
    predicate: str,
    parameters: tuple[object, ...],
    types: Iterable[LinkType] | None,
) -> tuple[Link, ...]:
    bindings = list(parameters)
    type_filter = ""
    if types is not None:
        wanted = tuple(types)
        if not wanted:
            return ()
        type_filter = f" AND link_type IN ({', '.join('?' * len(wanted))})"
        bindings.extend(link_type.value for link_type in wanted)
    sql = (
        f"SELECT {_LINK_COLUMNS} FROM current_link "  # noqa: S608 -- the only interpolation is a run of '?' placeholders
        f"WHERE {predicate} AND retracted = 0{type_filter} ORDER BY id"
    )
    with translating_sqlite_errors():
        rows = connection.execute(sql, bindings).fetchall()
    return tuple(from_row(Link, row) for row in rows)


# -- traversal -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Direction:
    """Which end of an edge a walk steps to, and which end it matches on.

    The two walks are the same query with these three column names swapped,
    which is only true because every dependency edge points the same way.
    """

    step_id: str
    step_kind: str
    match_id: str


_TOWARDS_DEPENDENTS: Final = _Direction("source_id", "source_kind", "target_id")
_TOWARDS_DEPENDENCIES: Final = _Direction("target_id", "target_kind", "source_id")


def impacted_by(
    connection: sqlite3.Connection,
    node_id: NodeId,
    *,
    types: Iterable[LinkType] = DEPENDENCY_LINK_TYPES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> tuple[ReachedNode, ...]:
    """Everything that depends on this node, transitively.

    The fusion query. Given the estimate that missed, this returns the
    assumptions that were compiled from it and the decisions that rest on those
    assumptions, each with its distance.

    Args:
        connection: An open store.
        node_id: The root. Not included in the result.
        types: Which edges count as dependency. The default is the impact DAG;
            `contradicts` and `supersedes` are excluded because neither
            expresses dependency.
        max_depth: Hops to follow. See the module docstring on why this is
            required rather than advisory.
    """
    return _walk(connection, node_id, _TOWARDS_DEPENDENTS, types=types, max_depth=max_depth)


def depends_on(
    connection: sqlite3.Connection,
    node_id: NodeId,
    *,
    types: Iterable[LinkType] = DEPENDENCY_LINK_TYPES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> tuple[ReachedNode, ...]:
    """Everything this node rests on, transitively.

    The same walk in the other direction: from a decision, the assumptions it
    makes and the estimates those assumptions are really about.
    """
    return _walk(connection, node_id, _TOWARDS_DEPENDENCIES, types=types, max_depth=max_depth)


def _walk(
    connection: sqlite3.Connection,
    node_id: NodeId,
    direction: _Direction,
    *,
    types: Iterable[LinkType],
    max_depth: int,
) -> tuple[ReachedNode, ...]:
    wanted = tuple(types)
    if not wanted or max_depth < 1:
        return ()
    placeholders = ", ".join("?" * len(wanted))
    # Retracted *edges* are excluded by the join, and retracted *nodes* by the
    # final one: a withdrawn decision is not part of anyone's blast radius. A
    # walk may still pass through a retracted node, which is the honest
    # behaviour -- an edge that outlives its endpoint is a caller's bug, not
    # something to paper over here.
    sql = f"""
        WITH RECURSIVE reachable(id, kind, depth) AS (
            SELECT ?, ?, 0
            UNION
            SELECT l.{direction.step_id}, l.{direction.step_kind}, r.depth + 1
            FROM current_link AS l
            JOIN reachable AS r ON l.{direction.match_id} = r.id
            WHERE l.retracted = 0 AND l.link_type IN ({placeholders}) AND r.depth < ?
        )
        SELECT r.id AS id, r.kind AS kind, MIN(r.depth) AS depth
        FROM reachable AS r
        JOIN current_record AS cr ON cr.id = r.id
        WHERE r.depth > 0 AND cr.retracted = 0
        GROUP BY r.id, r.kind
        ORDER BY depth, id
    """  # noqa: S608 -- every interpolated value is a column name fixed by this module
    bindings: list[object] = [node_id, kind_of(node_id).value]
    bindings.extend(link_type.value for link_type in wanted)
    bindings.append(max_depth)
    with translating_sqlite_errors():
        rows = connection.execute(sql, bindings).fetchall()
    return tuple(
        ReachedNode(id=row["id"], kind=RecordKind(row["kind"]), depth=int(row["depth"]))
        for row in rows
    )
