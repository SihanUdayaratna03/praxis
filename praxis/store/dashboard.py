"""The reads Phase 11's dashboard needs, kept inside the store package.

Here rather than in `reports.py` only because that file is already near the
400-line limit. Same rule either way: no SQL leaves `praxis.store`, so a route
handler calls one of these instead of writing a query. See ADR 0036.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

from praxis.domain.enums import AssumptionStatus, Severity, Verdict
from praxis.domain.ids import NodeId
from praxis.domain.records import (
    AuditEvent,
    Decision,
    Finding,
)
from praxis.store.errors import translating_sqlite_errors
from praxis.store.mapping import from_row
from praxis.store.repository import Repository

DEFAULT_PAGE: Final = 50
"""Rows a paged read returns when the caller does not say."""

MAX_PAGE: Final = 500
"""Ceiling on any page size, so a URL cannot ask for the whole store."""


def _page_size(limit: int | None) -> int:
    """Clamp a caller's page size into `[1, MAX_PAGE]`."""
    if limit is None:
        return DEFAULT_PAGE
    return max(1, min(limit, MAX_PAGE))


# --- the audit timeline ----------------------------------------------------

_TIMELINE_SQL: Final = """
    SELECT * FROM audit_event
    WHERE (:before IS NULL OR ordinal < :before)
    ORDER BY ordinal DESC
    LIMIT :limit
"""
"""Paged by ordinal, not by offset.

`occurred_at` is not unique -- writes in one run share a timestamp -- and an
OFFSET page shifts under any concurrent write. The ordinal is the store's only
total order, so a cursor on it returns each row exactly once.
"""


@dataclass(frozen=True, slots=True)
class Timeline:
    """One page of the audit trail, newest first.

    Attributes:
        events: The page.
        next_before: Cursor for the following page, or `None` at the end.
        total: Every event the store holds, so a page can say what it is part of.
    """

    events: tuple[AuditEvent, ...]
    next_before: int | None
    total: int


def audit_timeline(
    repository: Repository, *, limit: int | None = None, before: int | None = None
) -> Timeline:
    """One page of audit events, newest first.

    Args:
        repository: An open store.
        before: Return events allocated before this ordinal. `None` starts at
            the newest.
        limit: Page size, clamped to `MAX_PAGE`.

    Returns:
        The page and the cursor for the next one.
    """
    size = _page_size(limit)
    connection = repository.connection
    with translating_sqlite_errors():
        rows = connection.execute(_TIMELINE_SQL, {"before": before, "limit": size + 1}).fetchall()
        total = int(connection.execute("SELECT count(*) FROM audit_event").fetchone()[0])
    # One row over the page size answers "is there more" without a second count.
    has_more = len(rows) > size
    page = tuple(from_row(AuditEvent, row) for row in rows[:size])
    next_before = int(rows[size - 1]["ordinal"]) if has_more and page else None
    return Timeline(events=page, next_before=next_before, total=total)


# --- the decision index ----------------------------------------------------

_DECISION_COUNTS_SQL: Final = """
    SELECT source_id AS decision_id, count(*) AS n
    FROM current_link
    WHERE retracted = 0 AND link_type = 'assumes' AND source_kind = 'decision'
    GROUP BY source_id
"""

_FINDING_COUNTS_SQL: Final = """
    SELECT f.subject_id AS subject_id, count(*) AS n
    FROM finding AS f
    JOIN record_head    AS h  ON h.id = f.id AND h.version = f.version
    JOIN record_version AS rv ON rv.id = f.id AND rv.version = f.version
    WHERE rv.retracted = 0
    GROUP BY f.subject_id
"""

_BREACHED_COUNTS_SQL: Final = """
    SELECT l.source_id AS decision_id, count(*) AS n
    FROM current_link AS l
    JOIN assumption     AS a  ON a.id = l.target_id
    JOIN record_head    AS h  ON h.id = a.id AND h.version = a.version
    JOIN record_version AS rv ON rv.id = a.id AND rv.version = a.version
    WHERE l.retracted = 0 AND l.link_type = 'assumes' AND rv.retracted = 0
      AND a.status = 'breached'
    GROUP BY l.source_id
"""
"""Counts per decision and per finding subject.

Three aggregates rather than one join per decision: the index renders every
decision at once, and a per-row query is what makes a list page slow.
"""


@dataclass(frozen=True, slots=True)
class DecisionSummary:
    """A decision as the index lists it.

    Attributes:
        decision: The record itself.
        assumptions: How many assumptions it rests on.
        breached: How many of those are breached.
        findings: Findings naming this decision as their subject.
    """

    decision: Decision
    assumptions: int
    breached: int
    findings: int

    @property
    def at_risk(self) -> bool:
        """Whether anything it rests on has already failed."""
        return self.breached > 0


def decision_index(repository: Repository) -> tuple[DecisionSummary, ...]:
    """Every current decision with the counts the list view shows.

    Newest first, which is the order a timeline-shaped index reads in.
    """
    decisions = repository.list_all(Decision)
    connection = repository.connection
    with translating_sqlite_errors():
        assumptions = _counts(connection, _DECISION_COUNTS_SQL, "decision_id")
        breached = _counts(connection, _BREACHED_COUNTS_SQL, "decision_id")
        findings = _counts(connection, _FINDING_COUNTS_SQL, "subject_id")
    summaries = tuple(
        DecisionSummary(
            decision=decision,
            assumptions=assumptions.get(decision.id, 0),
            breached=breached.get(decision.id, 0),
            findings=findings.get(decision.id, 0),
        )
        for decision in decisions
    )
    return tuple(sorted(summaries, key=lambda s: s.decision.decided_at, reverse=True))


def _counts(connection: sqlite3.Connection, sql: str, key: str) -> dict[str, int]:
    """Run one grouped count and return it keyed by id."""
    return {str(row[key]): int(row["n"]) for row in connection.execute(sql)}


# --- assumption health -----------------------------------------------------

_ASSUMPTION_HEALTH_SQL: Final = """
    SELECT a.status AS status, count(*) AS n
    FROM assumption AS a
    JOIN record_head    AS h  ON h.id = a.id AND h.version = a.version
    JOIN record_version AS rv ON rv.id = a.id AND rv.version = a.version
    WHERE rv.retracted = 0
    GROUP BY a.status
"""


def assumption_health(repository: Repository) -> dict[AssumptionStatus, int]:
    """Current assumptions counted by status.

    Every status is present, zeros included -- a panel that hides "breached: 0"
    and one that was never told about breaches look identical.
    """
    with translating_sqlite_errors():
        found = {
            AssumptionStatus(row["status"]): int(row["n"])
            for row in repository.connection.execute(_ASSUMPTION_HEALTH_SQL)
        }
    return {status: found.get(status, 0) for status in AssumptionStatus}


# --- the review queue ------------------------------------------------------

_SEVERITY_RANK: Final = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}
"""Most urgent first. A dict rather than the enum's declaration order, so
reordering the enum cannot silently reorder the queue."""


@dataclass(frozen=True, slots=True)
class QueueItem:
    """A finding as the review queue lists it.

    Attributes:
        finding: The finding itself.
        subject_label: A human-readable name for what it is about.
    """

    finding: Finding
    subject_label: str


def finding_queue(
    repository: Repository, *, limit: int | None = None, undecided_only: bool = False
) -> tuple[QueueItem, ...]:
    """Findings needing a person, most severe first, then newest.

    Args:
        repository: An open store.
        limit: Page size, clamped to `MAX_PAGE`.
        undecided_only: Keep only what the challenger has not ruled on.
    """
    findings = repository.list_all(Finding)
    if undecided_only:
        findings = tuple(f for f in findings if f.verdict is Verdict.UNDECIDED)
    ordered = sorted(
        findings, key=lambda f: (_SEVERITY_RANK[f.severity], -f.detected_at.timestamp())
    )
    labels = _subject_labels(repository)
    return tuple(
        QueueItem(finding=f, subject_label=labels.get(f.subject_id, f.subject_id))
        for f in ordered[: _page_size(limit)]
    )


_SUBJECT_LABEL_SQL: Final = """
    SELECT d.id AS id, d.title AS label FROM decision AS d
    JOIN record_head AS h ON h.id = d.id AND h.version = d.version
    UNION ALL
    SELECT a.id AS id, a.predicate AS label FROM assumption AS a
    JOIN record_head AS h ON h.id = a.id AND h.version = a.version
    UNION ALL
    SELECT e.id AS id, e.subject AS label FROM estimate AS e
    JOIN record_head AS h ON h.id = e.id AND h.version = e.version
"""
"""One title per record a finding can be about.

Read in one statement rather than one per finding: the queue renders every row
at once and this is the only column of any of them it needs.
"""


def _subject_labels(repository: Repository) -> dict[str, str]:
    """Titles for everything a finding can name as its subject."""
    with translating_sqlite_errors():
        return {
            str(row["id"]): str(row["label"])
            for row in repository.connection.execute(_SUBJECT_LABEL_SQL)
        }


def findings_for(repository: Repository, node_id: NodeId) -> tuple[Finding, ...]:
    """Every current finding naming this record as its subject, most severe first."""
    found = [f for f in repository.list_all(Finding) if f.subject_id == node_id]
    return tuple(
        sorted(found, key=lambda f: (_SEVERITY_RANK[f.severity], -f.detected_at.timestamp()))
    )
