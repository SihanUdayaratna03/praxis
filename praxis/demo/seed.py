"""Writing Praxis's own history into a Praxis store.

Every record is derived from a file already committed to this repository: the
ADRs become decisions and assumptions, `docs/dogfood/` becomes estimates and
outcomes. Nothing is invented, and every span cites bytes that really exist in
the file it came from.

Two fields have no source and are set to a constant rather than guessed. See
`SEEDED_CONFIDENCE`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from praxis.demo.sources import (
    Adr,
    AdrAssumption,
    DogfoodRow,
    Quoted,
    quantity,
    read_adrs,
    read_jsonl,
)
from praxis.domain.base import VersionedRecord
from praxis.domain.enums import (
    DecisionScope,
    DecisionStatus,
    Impact,
    MatchQuality,
    RecordKind,
    SourceKind,
    Unit,
)
from praxis.domain.ids import (
    AssumptionId,
    DecisionId,
    DocumentId,
    EstimateId,
    NodeId,
    OutcomeId,
    SpanId,
)
from praxis.domain.links import LinkType
from praxis.domain.records import (
    Assumption,
    Decision,
    Document,
    Estimate,
    Link,
    Outcome,
    RejectedOption,
    Span,
)
from praxis.store.connection import transaction
from praxis.store.repository import Repository

ACTOR: Final = "praxis demo seed"
"""Who the audit trail names for every write this module makes."""

SEEDED_CONFIDENCE: Final = 0.75
"""Confidence for records whose source file does not carry one.

`Decision` and `Assumption` require it and an ADR has no such field. One
constant, stated here, rather than a different invented number per record --
a spread would look like measurement and would be nothing of the kind.
"""

MAX_CONDITIONS: Final = 6
"""How many `conditions` a seeded estimate keeps.

`EST-0011` alone carries sixteen numbered refusal paths in one string. The
whole text stays readable in the document the span points at; the record keeps
the first few so a drill-down is legible.
"""


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What one seeding run wrote.

    Attributes:
        documents: Source files ingested.
        spans: Byte ranges cited.
        decisions: ADRs written as decisions.
        assumptions: Assumption-table rows written.
        estimates: Dogfood predictions written.
        outcomes: Dogfood results written.
        links: Edges asserted.
        unresolved: Estimates with no outcome answering them.
    """

    documents: int = 0
    spans: int = 0
    decisions: int = 0
    assumptions: int = 0
    estimates: int = 0
    outcomes: int = 0
    links: int = 0
    unresolved: int = 0

    @property
    def records(self) -> int:
        """Everything written, edges included."""
        return (
            self.documents
            + self.spans
            + self.decisions
            + self.assumptions
            + self.estimates
            + self.outcomes
            + self.links
        )


class _Writer:
    """One seeding run. Holds the counters so `seed` stays readable."""

    def __init__(self, repository: Repository, at: datetime) -> None:
        self.repo = repository
        self.at = at
        self.counts = dict.fromkeys(
            ("documents", "spans", "decisions", "assumptions", "estimates", "outcomes", "links"), 0
        )

    def add[R: VersionedRecord](self, record: R, kind: str, reason: str) -> R:
        """Write one record and count it."""
        written = self.repo.add(record, actor=ACTOR, reason=reason, at=self.at)
        self.counts[kind] += 1
        return written

    def document(self, path: Path, content: str, title: str, kind: SourceKind) -> Document:
        """Ingest one source file whole, so every span has something to cite."""
        return self.add(
            Document(
                id=DocumentId(self.repo.next_id(RecordKind.DOCUMENT)),
                # resolve() first: a relative path has no file URI, and the
                # seeder is normally handed docs/adr from the repo root.
                source_uri=path.resolve().as_uri(),
                source_kind=kind,
                title=title,
                content=content,
                ingested_at=self.at,
                created_at=self.at,
                created_by=ACTOR,
            ),
            "documents",
            f"seeded from {path.name}",
        )

    def span(self, document: Document, quoted: Quoted, reason: str) -> Span:
        """Cite a byte range of a document. Invariant 6 lives here."""
        return self.add(
            Span.covering(document, quoted.start, quoted.end, created_by=ACTOR, created_at=self.at),
            "spans",
            reason,
        )

    def link(self, link_type: LinkType, source: NodeId, target: NodeId, why: str) -> Link:
        """Assert one edge."""
        return self.add(
            Link.between(
                link_type,
                source,
                target,
                rationale=why,
                confidence=SEEDED_CONFIDENCE,
                created_by=ACTOR,
                created_at=self.at,
            ),
            "links",
            why,
        )


def seed(
    repository: Repository,
    *,
    adr_dir: Path,
    dogfood_dir: Path,
    at: datetime | None = None,
) -> SeedReport:
    """Load this project's own history into a store.

    Args:
        repository: An open, migrated store. Written to.
        adr_dir: `docs/adr/`.
        dogfood_dir: `docs/dogfood/`.
        at: Write time. Defaults to now, in UTC.

    Returns:
        Counts of what was written.
    """
    writer = _Writer(repository, at or datetime.now(UTC))
    # One transaction for the whole seed. `transaction` joins rather than
    # nests, so every `add` below lands in this one and commits once instead of
    # six hundred times. A half-seeded store also stops being reachable.
    with transaction(repository.connection):
        for adr in read_adrs(adr_dir):
            _seed_adr(writer, adr)
        unresolved = _seed_dogfood(writer, dogfood_dir)
    return SeedReport(unresolved=unresolved, **writer.counts)


def _seed_adr(writer: _Writer, adr: Adr) -> None:
    """One ADR: a document, a decision, and one assumption per table row."""
    document = writer.document(
        adr.path, adr.content, f"ADR {adr.number} — {adr.title}", SourceKind.MARKDOWN
    )
    chosen = writer.span(document, adr.chosen, f"the chosen paragraph of ADR {adr.number}")
    decision = writer.add(
        Decision(
            id=DecisionId(writer.repo.next_id(RecordKind.DECISION)),
            title=adr.title,
            chosen=adr.chosen.text,
            rejected=tuple(
                RejectedOption(option=option, reason=reason) for option, reason in adr.rejected
            ),
            decision_maker=adr.decision_maker,
            decided_at=_day(adr.date, writer.at),
            scope=DecisionScope.PROJECT,
            impact=Impact(adr.impact),
            status=_status(adr.status),
            span_id=chosen.id,
            confidence=SEEDED_CONFIDENCE,
            created_at=writer.at,
            created_by=ACTOR,
        ),
        "decisions",
        f"ADR {adr.number}",
    )
    for row in adr.assumptions:
        _seed_assumption(writer, document, decision, adr, row)


def _seed_assumption(
    writer: _Writer, document: Document, decision: Decision, adr: Adr, row: AdrAssumption
) -> None:
    """One assumption-table row, cited at the row itself."""
    span = writer.span(document, row.quoted, f"an assumption of ADR {adr.number}")
    assumption = writer.add(
        Assumption(
            id=AssumptionId(writer.repo.next_id(RecordKind.ASSUMPTION)),
            statement=row.claim,
            predicate=row.predicate,
            expiry_condition=row.expiry,
            span_id=span.id,
            confidence=SEEDED_CONFIDENCE,
            created_at=writer.at,
            created_by=ACTOR,
        ),
        "assumptions",
        f"ADR {adr.number} assumption",
    )
    writer.link(
        LinkType.ASSUMES,
        decision.id,
        assumption.id,
        f"ADR {adr.number} rests on this assumption",
    )


def _seed_dogfood(writer: _Writer, dogfood_dir: Path) -> int:
    """The estimate and outcome corpus. Returns how many estimates went unanswered."""
    estimates_path = dogfood_dir / "estimates.jsonl"
    outcomes_path = dogfood_dir / "outcomes.jsonl"
    estimate_text, estimate_rows = read_jsonl(estimates_path)
    outcome_text, outcome_rows = read_jsonl(outcomes_path)

    estimate_doc = writer.document(
        estimates_path, estimate_text, "Praxis dogfood estimates", SourceKind.JSON
    )
    outcome_doc = writer.document(
        outcomes_path, outcome_text, "Praxis dogfood outcomes", SourceKind.JSON
    )

    # Dogfood ids and store ids are allocated independently, so nothing is
    # matched by name -- an outcome finds its estimate through this map.
    written: dict[str, EstimateId] = {}
    cited: dict[str, SpanId] = {}
    for row in estimate_rows:
        span = writer.span(estimate_doc, row.quoted, f"{row.id} as it was logged")
        estimate = writer.add(_estimate(writer, row, span.id), "estimates", f"{row.id}")
        written[row.id] = estimate.id
        cited[row.id] = span.id

    answered = set()
    for row in outcome_rows:
        target = written.get(str(row.data["estimate_id"]))
        if target is None:  # pragma: no cover - both files are committed together
            continue
        span = writer.span(outcome_doc, row.quoted, f"{row.id} as it was recorded")
        writer.add(_outcome(writer, row, target, span.id), "outcomes", f"{row.id}")
        answered.add(target)

    # ADR 0022: an unmatched estimate carries an `unresolved` outcome rather
    # than nothing, so a phase still in flight stays visible in the calibration
    # history instead of quietly leaving it.
    open_estimates = [(name, eid) for name, eid in written.items() if eid not in answered]
    for name, estimate_id in open_estimates:
        row = next(r for r in estimate_rows if r.id == name)
        # The estimate's own span, reused: span ids are content-addressed
        # (ADR 0008), so citing the same line again is the same span.
        writer.add(
            _unresolved(writer, estimate_id, row, cited[name]), "outcomes", f"{name} unresolved"
        )
    return len(open_estimates)


def _estimate(writer: _Writer, row: DogfoodRow, span_id: SpanId) -> Estimate:
    """One dogfood prediction as an `Estimate`."""
    data = row.data
    # The first three rows predate the active/blocked split and carry `quantity`.
    active = quantity(data.get("active_quantity", data.get("quantity")))
    return Estimate(
        id=EstimateId(writer.repo.next_id(RecordKind.ESTIMATE)),
        subject=str(data["subject"]),
        owner=str(data["owner"]),
        work_class=str(data["work_class"]),
        active_quantity=active if active is not None else Decimal(0),
        blocked_quantity=quantity(data.get("blocked_quantity")) or Decimal(0),
        unit=Unit(data["unit"]),
        confidence=float(data["confidence"]),
        conditions=tuple(str(c) for c in data.get("conditions", ())[:MAX_CONDITIONS]),
        estimated_at=_moment(str(data["logged_at"])),
        span_id=span_id,
        created_at=writer.at,
        created_by=ACTOR,
    )


def _outcome(writer: _Writer, row: DogfoodRow, estimate_id: EstimateId, span_id: SpanId) -> Outcome:
    """One dogfood result as an `Outcome`."""
    data = row.data
    return Outcome(
        id=OutcomeId(writer.repo.next_id(RecordKind.OUTCOME)),
        estimate_id=estimate_id,
        active_quantity=quantity(data.get("active_quantity")),
        blocked_quantity=quantity(data.get("blocked_quantity")),
        unit=Unit(data["unit"]),
        match_quality=MatchQuality(data["match_quality"]),
        resolved_at=_moment(str(data["resolved_at"])),
        notes=str(data.get("notes", "")),
        span_id=span_id,
        created_at=writer.at,
        created_by=ACTOR,
    )


def _unresolved(
    writer: _Writer, estimate_id: EstimateId, row: DogfoodRow, span_id: SpanId
) -> Outcome:
    """The outcome of an estimate nothing has answered yet.

    Carries no quantities and no resolution time -- the schema refuses an
    `unresolved` row that also holds numbers, which is what keeps the
    distinction worth having.
    """
    return Outcome(
        id=OutcomeId(writer.repo.next_id(RecordKind.OUTCOME)),
        estimate_id=estimate_id,
        unit=Unit(row.data["unit"]),
        match_quality=MatchQuality.UNRESOLVED,
        notes="no outcome recorded yet",
        span_id=span_id,
        created_at=writer.at,
        created_by=ACTOR,
    )


def _moment(text: str) -> datetime:
    """An ISO-8601 timestamp, always tz-aware. Invariant 5."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _day(text: str, fallback: datetime) -> datetime:
    """An ADR's `date:` field at midnight UTC, or the run's own time."""
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    except ValueError:
        return fallback


def _status(text: str) -> DecisionStatus:
    """An ADR's `status:` field, defaulting to accepted."""
    try:
        return DecisionStatus(text)
    except ValueError:  # pragma: no cover - every ADR is accepted today
        return DecisionStatus.ACCEPTED
