"""The typed edge vocabulary, and the direction rule that makes it traversable.

Six link types, fixed. The important content of this module is not the list --
it is the invariant below, which is what lets Phase 8 answer its question with
one recursive query instead of a union of forward and backward walks.

**Every dependency edge points from the dependent to the depended-upon.**

    Decision --assumes--> Assumption --estimated_as--> Estimate
    Decision --justified_by--> Estimate | Document | Span

Because all three point the same way, "an estimate missed -- what rests on it?"
is reverse reachability over `DEPENDENCY_LINK_TYPES` from that estimate, and
nothing else. Reversing any one of them would turn that query into a special
case per hop, so the direction is enforced by `ALLOWED_ENDPOINTS` rather than
left to whichever agent writes the edge.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from praxis.domain.enums import RecordKind


class LinkType(StrEnum):
    """The only relationships the graph can express."""

    ASSUMES = "assumes"
    """A decision rests on an assumption. Breaking the assumption is what makes
    the decision worth re-reading."""

    JUSTIFIED_BY = "justified_by"
    """A decision or assumption cites the evidence it was built on.
    `CollateralAgent` walks these in reverse when an outcome misses."""

    CONTRADICTS = "contradicts"
    """Two records cannot both be true. Symmetric in meaning; stored once."""

    SUPERSEDES = "supersedes"
    """A newer record replaces an older one of the same kind. Points from the
    newer to the older, matching the ADR convention already in the corpus."""

    ESTIMATED_AS = "estimated_as"
    """The fusion edge. An assumption whose predicate is a quantified
    forward-looking claim is an estimate wearing an assumption's clothes;
    `FusionBridge` writes this edge, and it is what lets calibration history
    invalidate a decision."""

    COLLATERAL_OF = "collateral_of"
    """A finding exists only because of another record's failure."""


DEPENDENCY_LINK_TYPES: Final[frozenset[LinkType]] = frozenset(
    {LinkType.ASSUMES, LinkType.JUSTIFIED_BY, LinkType.ESTIMATED_AS}
)
"""The edges that form the impact DAG, all pointing dependent -> depended-upon.

`contradicts` and `supersedes` are excluded on purpose. Neither expresses
dependency: superseding a decision does not make the new one rest on the old,
and following `supersedes` during a blast-radius walk would drag every retired
version of a record into the result set.
"""

SYMMETRIC_LINK_TYPES: Final[frozenset[LinkType]] = frozenset({LinkType.CONTRADICTS})
"""Types whose meaning does not depend on which end is the source.

Stored once, in whichever direction the writing agent saw it. Queries that care
must look both ways -- `Repository.links_touching` exists for exactly this, so
the alternative (writing both directions and keeping them in step) never has to
be maintained.
"""

_CLAIMS: Final[frozenset[RecordKind]] = frozenset(
    {
        RecordKind.DECISION,
        RecordKind.ASSUMPTION,
        RecordKind.ESTIMATE,
        RecordKind.OUTCOME,
        RecordKind.FINDING,
    }
)
_EVIDENCE: Final[frozenset[RecordKind]] = frozenset(
    {
        RecordKind.DOCUMENT,
        RecordKind.SPAN,
        RecordKind.ESTIMATE,
        RecordKind.OUTCOME,
        RecordKind.FINDING,
    }
)

ALLOWED_ENDPOINTS: Final[Mapping[LinkType, tuple[frozenset[RecordKind], frozenset[RecordKind]]]] = (
    MappingProxyType(
        {
            LinkType.ASSUMES: (
                frozenset({RecordKind.DECISION}),
                frozenset({RecordKind.ASSUMPTION}),
            ),
            LinkType.JUSTIFIED_BY: (
                frozenset({RecordKind.DECISION, RecordKind.ASSUMPTION, RecordKind.FINDING}),
                _EVIDENCE,
            ),
            LinkType.CONTRADICTS: (_CLAIMS, _CLAIMS),
            LinkType.SUPERSEDES: (_CLAIMS, _CLAIMS),
            LinkType.ESTIMATED_AS: (
                frozenset({RecordKind.ASSUMPTION}),
                frozenset({RecordKind.ESTIMATE}),
            ),
            LinkType.COLLATERAL_OF: (
                frozenset({RecordKind.FINDING}),
                _CLAIMS,
            ),
        }
    )
)
"""Which record kinds may sit at each end of each edge type.

This is the grammar of the graph, and it is checked in the `Link` model rather
than in SQL: expressing it as a table constraint would need one `CHECK` clause
per combination, which is unreadable and would have to be migrated every time a
kind is added. SQLite enforces that both endpoints *exist* and that their
declared kinds are truthful; this enforces that the sentence means something.
"""


def endpoints_are_valid(
    link_type: LinkType, source_kind: RecordKind, target_kind: RecordKind
) -> bool:
    """Report whether an edge of this type may join these two kinds."""
    sources, targets = ALLOWED_ENDPOINTS[link_type]
    if source_kind not in sources or target_kind not in targets:
        return False
    # Superseding is replacement, and a decision cannot replace an estimate.
    return not (link_type is LinkType.SUPERSEDES and source_kind is not target_kind)
