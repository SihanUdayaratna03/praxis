"""Search, counting and calibration: the reads that are about more than a record.

Separate from `praxis.store.repository` because they answer questions no single
record can. `praxis store stats` and `praxis doctor` are the callers now;
`ReporterAgent` is the caller this shape is really for.

`calibration_history` is here for a second reason as well as the first: no raw
SQL leaves `praxis.store`, so the query Phase 7 groups estimates by person and
work class with has to live in this package whichever phase writes it. It is
written in Phase 6, against Half B's output while that output can still change.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from praxis.domain.enums import MatchQuality, RecordKind, Unit
from praxis.domain.ids import DocumentId, NodeId
from praxis.domain.links import LinkType
from praxis.store import audit
from praxis.store.errors import translating_sqlite_errors
from praxis.store.migrations import current_version as schema_version

_SEARCH_SQL: Final = """
    SELECT search.record_id AS record_id, search.kind AS kind, search.field AS field,
           snippet(search, 0, '[', ']', '...', 12) AS excerpt
    FROM search
    JOIN record_head AS h ON h.id = search.record_id AND h.version = search.version
    JOIN record_version AS rv ON rv.id = search.record_id AND rv.version = search.version
    WHERE search MATCH ? AND rv.retracted = 0
    ORDER BY rank
    LIMIT ?
"""
"""`search` is written out on both sides of MATCH because FTS5 does not accept
an alias there. The join to `record_head` is what keeps superseded versions out
of the results: nothing is ever removed from the index, because nothing is ever
removed from the store."""


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One full-text match, already narrowed to a current, unretracted record.

    Attributes:
        record_id: The record the text belongs to.
        kind: That record's kind.
        field: Which field matched -- `statement`, `rejected`, `content`.
        excerpt: The matching text, with the terms marked in square brackets.
    """

    record_id: NodeId
    kind: RecordKind
    field: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class StoreStats:
    """What `praxis store stats` reports.

    Attributes:
        schema_version: The migration the store is at.
        records: Current, unretracted record count per kind.
        versions: Rows in `record_version`, superseded versions included. The
            gap between this and the sum of `records` is the store's history.
        links: Current, unretracted edge count per type.
        audit_events: One per write, ever.
    """

    schema_version: int
    records: Mapping[RecordKind, int]
    versions: int
    links: Mapping[LinkType, int]
    audit_events: int


def search(connection: sqlite3.Connection, query: str, *, limit: int = 20) -> tuple[SearchHit, ...]:
    """Full-text search over the current, unretracted records.

    Args:
        connection: An open store.
        query: An FTS5 match expression, passed through unchanged -- the caller
            owns its syntax, and a malformed one arrives back as a
            `StoreQueryError`.
        limit: How many hits to return, best first.
    """
    with translating_sqlite_errors():
        rows = connection.execute(_SEARCH_SQL, (query, limit)).fetchall()
    return tuple(
        SearchHit(
            record_id=row["record_id"],
            kind=RecordKind(row["kind"]),
            field=row["field"],
            excerpt=row["excerpt"],
        )
        for row in rows
    )


_BY_CONTENT_HASH_SQL: Final = """
    SELECT d.id AS id
    FROM document AS d
    JOIN record_head AS h ON h.id = d.id AND h.version = d.version
    JOIN record_version AS rv ON rv.id = d.id AND rv.version = d.version
    WHERE d.content_hash = ? AND rv.retracted = 0
    ORDER BY d.id
    LIMIT 1
"""
"""The re-ingestion lookup the `document_content_hash` index was built for.

Narrowed to current, unretracted versions on purpose: a document whose content
was superseded by a new version should be re-ingested rather than recognised,
and a retracted one should come back as if it had never been seen.
"""


def document_with_content(connection: sqlite3.Connection, content_hash: str) -> DocumentId | None:
    """Return the document already holding these exact bytes, if there is one.

    What makes ingesting a corpus twice cost nothing and, more importantly,
    produce nothing: the second run recognises every document instead of
    writing a second copy under a new id, which would fork every span cut from
    it and double-count the corpus in every metric computed over it.

    Args:
        connection: An open store.
        content_hash: `Document.content_hash` -- of the *normalised* content,
            which is the only thing two ingestions of one file agree on.
    """
    with translating_sqlite_errors():
        row = connection.execute(_BY_CONTENT_HASH_SQL, (content_hash,)).fetchone()
    return None if row is None else DocumentId(str(row["id"]))


_CALIBRATION_SELECT: Final = """
    SELECT e.id               AS estimate_id,
           e.owner            AS owner,
           e.work_class       AS work_class,
           e.unit             AS unit,
           e.subject          AS subject,
           e.active_quantity  AS estimated_active,
           e.blocked_quantity AS estimated_blocked,
           o.id               AS outcome_id,
           o.active_quantity  AS actual_active,
           o.blocked_quantity AS actual_blocked,
           o.match_quality    AS match_quality
    FROM estimate AS e
    JOIN record_head    AS eh  ON eh.id = e.id AND eh.version = e.version
    JOIN record_version AS erv ON erv.id = e.id AND erv.version = e.version
    JOIN outcome        AS o   ON o.estimate_id = e.id
    JOIN record_head    AS oh  ON oh.id = o.id AND oh.version = o.version
    JOIN record_version AS orv ON orv.id = o.id AND orv.version = o.version
    WHERE erv.retracted = 0 AND orv.retracted = 0
"""
"""Phase 7's question, written in Phase 6 so its shape can be checked now.

"For this person and this class of work, what is the distribution of estimated
against actual" is what `CalibratorAgent` and `BiasDetective` compute a factor
from, and a shape that is awkward there is cheap to change while `OutcomeMatcher`
is still being written and expensive afterwards. Four properties of Half B's
output are what keep this to one indexed join:

- the pairing is a **column** (`outcome.estimate_id`) and not an edge, so this is
  a join rather than a graph walk per estimate;
- an unmatched estimate carries an **unresolved** outcome rather than nothing, so
  this is an inner join with no absences to account for, and `n` is a filter on
  `match_quality` rather than a second query against a table of silences;
- units are reconciled **at write time**, so nothing here converts; and
- `work_class` lives on the estimate, so the grouping keys are the two columns
  `estimate_owner_work_class` already indexes.

Both sides are narrowed to the current, unretracted version. That matters more
here than anywhere else in this module: `WorkClassifier` revises an estimate to
put it on this axis, so an unfiltered read would return the row under its old
class as well and count one estimate twice, in two different groups.
"""

_CALIBRATION_BY_OWNER: Final = " AND e.owner = ?"
_CALIBRATION_BY_WORK_CLASS: Final = " AND e.work_class = ?"
_CALIBRATION_ORDER: Final = " ORDER BY e.owner, e.work_class, e.id"
"""The two narrowings and the ordering, as fragments the query is built from.

Fragments rather than one statement with `(? IS NULL OR e.owner = ?)`, and
composed rather than interpolated. The composition is safe by construction --
these three are literals in this module and the *values* are always bound
parameters -- and it is what lets SQLite choose `estimate_owner_work_class`
rather than scan, because a comparison against a placeholder that might be null
is not one the planner can use an index for.

Phase 7 is what made this worth fixing. `BiasDetective` asks this question once
per `(owner, work_class)` group and `FusionBridge` will ask it once per
assumption it prices, so an unnarrowed read here is a full scan of every
estimate and outcome the store holds, per question. The narrowing existed as an
argument from Phase 6 and was applied in Python after `fetchall`, which answered
correctly and read everything to do it.
"""


@dataclass(frozen=True, slots=True)
class CalibrationRow:
    """One estimate beside what actually happened to it.

    Attributes:
        estimate_id: The prediction.
        owner: Whose it was. Half of the grouping key.
        work_class: What kind of work. The other half.
        unit: Shared by both quantities, by construction -- `OutcomeMatcher`
            stores an outcome in its estimate's unit or refuses the pairing.
        subject: What was estimated, for a person reading a row.
        estimated_active: Predicted hands-on effort.
        estimated_blocked: Predicted waiting.
        outcome_id: The row answering it, resolved or not.
        actual_active: Effort really spent, or `None` when unresolved.
        actual_blocked: Waiting really incurred, or `None` when unresolved.
        match_quality: How well the two agree. `unresolved` means nothing ever
            answered this estimate, which is a row rather than an absence.
    """

    estimate_id: str
    owner: str
    work_class: str
    unit: Unit
    subject: str
    estimated_active: Decimal
    estimated_blocked: Decimal
    outcome_id: str
    actual_active: Decimal | None
    actual_blocked: Decimal | None
    match_quality: MatchQuality

    @property
    def resolved(self) -> bool:
        """Whether this row carries an actual to compare against."""
        return self.match_quality is not MatchQuality.UNRESOLVED

    @property
    def group(self) -> tuple[str, str]:
        """The key calibration is computed within: one person, one kind of work."""
        return self.owner, self.work_class


def calibration_history(
    connection: sqlite3.Connection,
    *,
    owner: str | None = None,
    work_class: str | None = None,
) -> tuple[CalibrationRow, ...]:
    """Every estimate the store holds beside the outcome standing against it.

    The read Phase 7 computes calibration factors from. It is here rather than
    in an agent because no raw SQL leaves `praxis.store`, and it is written now
    rather than then because the point of writing it is to find out whether
    `OutcomeMatcher`'s output shape answers it cleanly.

    Unresolved rows are returned rather than filtered out. Whether to exclude
    them is the caller's decision and `CalibrationRow.resolved` is how, because
    "eleven estimates, four of them never answered" and "seven estimates" are
    different facts and only one of them is honest.

    **Both narrowings are applied by the database**, not by this function. That
    is the difference between one indexed lookup and a full read of every
    estimate and outcome the store holds, and it is the shape `BiasDetective`
    and `FusionBridge` ask this question in: once per group, once per assumption
    being priced.

    Args:
        connection: An open store.
        owner: Restrict to one estimator. `None` for every estimator.
        work_class: Restrict to one class of work. `None` for every class.

    Returns:
        Rows ordered by owner, then work class, then estimate id -- so the
        grouping a caller does is a walk rather than a sort.
    """
    sql, parameters = calibration_query(owner=owner, work_class=work_class)
    with translating_sqlite_errors():
        rows = connection.execute(sql, parameters).fetchall()
    return tuple(_calibration_row(row) for row in rows)


def calibration_query(
    *, owner: str | None = None, work_class: str | None = None
) -> tuple[str, tuple[str, ...]]:
    """The statement and bound values `calibration_history` would run.

    Public so the query *plan* can be asserted on rather than described. "This
    narrowing uses `estimate_owner_work_class`" is a claim about SQLite's
    planner, and the only honest way to hold it is to hand the planner the real
    statement and read back what it decided -- which a test cannot do if the
    statement is assembled inside a function that also executes it.

    Args:
        owner: Restrict to one estimator, or `None`.
        work_class: Restrict to one class of work, or `None`.

    Returns:
        The SQL, and the values to bind to it in order.
    """
    sql = _CALIBRATION_SELECT
    values: list[str] = []
    if owner is not None:
        sql += _CALIBRATION_BY_OWNER
        values.append(owner)
    if work_class is not None:
        sql += _CALIBRATION_BY_WORK_CLASS
        values.append(work_class)
    return sql + _CALIBRATION_ORDER, tuple(values)


def _calibration_row(row: sqlite3.Row) -> CalibrationRow:
    """One row, with the quantities back in `Decimal`.

    `Decimal` and never `float`, invariant 4: these are summed and divided
    across a whole calibration history, and binary floating point would make
    those sums depend on the order the rows came back in.
    """
    return CalibrationRow(
        estimate_id=str(row["estimate_id"]),
        owner=str(row["owner"]),
        work_class=str(row["work_class"]),
        unit=Unit(row["unit"]),
        subject=str(row["subject"]),
        estimated_active=Decimal(str(row["estimated_active"])),
        estimated_blocked=Decimal(str(row["estimated_blocked"])),
        outcome_id=str(row["outcome_id"]),
        actual_active=_quantity(row["actual_active"]),
        actual_blocked=_quantity(row["actual_blocked"]),
        match_quality=MatchQuality(row["match_quality"]),
    )


def _quantity(value: object) -> Decimal | None:
    """A nullable stored quantity, as a `Decimal` or as nothing."""
    return None if value is None else Decimal(str(value))


def stats(connection: sqlite3.Connection) -> StoreStats:
    """Row and edge counts for a store."""
    with translating_sqlite_errors():
        records = {
            RecordKind(row["kind"]): int(row["n"])
            for row in connection.execute(
                "SELECT kind, count(*) AS n FROM current_record WHERE retracted = 0 GROUP BY kind"
            )
        }
        links = {
            LinkType(row["link_type"]): int(row["n"])
            for row in connection.execute(
                "SELECT link_type, count(*) AS n FROM current_link "
                "WHERE retracted = 0 GROUP BY link_type"
            )
        }
        versions = int(connection.execute("SELECT count(*) FROM record_version").fetchone()[0])
    return StoreStats(
        schema_version=schema_version(connection),
        records=records,
        versions=versions,
        links=links,
        audit_events=audit.event_count(connection),
    )
