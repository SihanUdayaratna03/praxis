"""The closed vocabularies every record draws on.

Everything here is a `StrEnum`, so a value serialises to the string a human
would have written anyway and the SQL `CHECK` constraints in the schema can
mirror these members literally. A field that could be a free string and is an
enum instead is a field where drift between two agents' spellings would have
been silent.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class RecordKind(StrEnum):
    """The nine record types Praxis stores.

    The member value is the entity table name and the `kind` written to the
    node registry, so adding a member without a migration is a loud failure
    rather than a quiet one.
    """

    DOCUMENT = "document"
    SPAN = "span"
    DECISION = "decision"
    ASSUMPTION = "assumption"
    ESTIMATE = "estimate"
    OUTCOME = "outcome"
    LINK = "link"
    FINDING = "finding"
    AUDIT_EVENT = "audit_event"


GRAPH_KINDS: Final[frozenset[RecordKind]] = frozenset(RecordKind) - {RecordKind.AUDIT_EVENT}
"""Kinds that may appear in the node registry and at the end of an edge.

`AuditEvent` is excluded deliberately. It is the log describing changes to the
graph, not a member of it -- nothing ever links to an audit row, and letting
one become an edge endpoint would make the trail part of the thing it audits.
"""


class SourceKind(StrEnum):
    """How a document's bytes were encoded before normalisation."""

    MARKDOWN = "markdown"
    TEXT = "text"
    JSON = "json"


class DecisionScope(StrEnum):
    """How far a decision's authority reaches.

    `ReviewTriageAgent` uses this with `Impact` to decide who has to re-read a
    decision when one of its assumptions breaks.
    """

    PERSONAL = "personal"
    TEAM = "team"
    PROJECT = "project"
    ORGANISATION = "organisation"


class Impact(StrEnum):
    """How much rests on a decision being right."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DecisionStatus(StrEnum):
    """Where a decision sits in its own lifecycle."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"


class AssumptionStatus(StrEnum):
    """The last thing `AssumptionMonitor` concluded about a predicate."""

    UNVERIFIED = "unverified"
    """Never evaluated. The state every assumption starts in."""

    HOLDING = "holding"
    """Evaluated, and the predicate was true."""

    BREACHED = "breached"
    """Evaluated, and the predicate was false. This is what raises a Finding."""

    EXPIRED = "expired"
    """The expiry condition fired, so the last verdict is no longer evidence."""


class Unit(StrEnum):
    """The unit an estimated or observed quantity is measured in.

    An `Outcome` is only comparable to its `Estimate` when the units match, and
    that comparison is what the whole calibration half rests on.
    """

    HOURS = "hours"
    DAYS = "days"
    WEEKS = "weeks"
    POINTS = "points"
    COUNT = "count"
    USD = "usd"


class MatchQuality(StrEnum):
    """How well an outcome answers the estimate it resolves."""

    EXACT = "exact"
    CLOSE = "close"
    PARTIAL = "partial"
    MISS = "miss"
    UNRESOLVED = "unresolved"
    """The work never finished, or the outcome was never observed. Explicitly
    recorded rather than absent, because silently dropping unresolved estimates
    is how a calibration curve flatters its estimator."""


class FindingKind(StrEnum):
    """What a finding is alleging."""

    ASSUMPTION_BREACH = "assumption_breach"
    CONTRADICTION = "contradiction"
    CALIBRATION_BIAS = "calibration_bias"
    COLLATERAL_IMPACT = "collateral_impact"
    STALE_DECISION = "stale_decision"


class Verdict(StrEnum):
    """What survived `ChallengerAgent`."""

    UNDECIDED = "undecided"
    """Prosecuted but not yet challenged."""

    UPHELD = "upheld"
    OVERTURNED = "overturned"


class Severity(StrEnum):
    """How urgently a finding needs a human."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AuditAction(StrEnum):
    """What a write did to a record.

    There is no `deleted`. Retraction is a new version carrying a flag, so the
    thing that was retracted stays readable and the audit trail describing it
    stays true.
    """

    CREATED = "created"
    REVISED = "revised"
    RETRACTED = "retracted"
