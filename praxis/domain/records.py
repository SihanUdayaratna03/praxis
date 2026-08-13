"""The nine record types, as Pydantic v2 models.

Each one validates the things that would otherwise become someone else's
debugging session: a span whose text does not fill its byte range, an outcome
that claims to be unresolved while carrying a number, a finding with a verdict
but no challenge behind it. None of these needs a database to catch, so none of
them waits for one.

The relationships that belong to the six-word edge vocabulary live in `Link`,
never as a foreign key on a record -- see `praxis.domain.links`. The single
exception is `Outcome.estimate_id`, and it is an exception because no edge type
expresses "resolves": that is 1:1 ownership, not a graph relationship.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Annotated, ClassVar, Self

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from praxis.domain import ids
from praxis.domain.base import (
    Confidence,
    NonEmptyStr,
    Quantity,
    Record,
    VersionedRecord,
    WorkClass,
)
from praxis.domain.enums import (
    GRAPH_KINDS,
    AssumptionStatus,
    AuditAction,
    DecisionScope,
    DecisionStatus,
    FindingKind,
    Impact,
    MatchQuality,
    RecordKind,
    Severity,
    SourceKind,
    Unit,
    Verdict,
)
from praxis.domain.ids import (
    AssumptionId,
    AuditEventId,
    DecisionId,
    DocumentId,
    EstimateId,
    FindingId,
    LinkId,
    NodeId,
    OutcomeId,
    SpanId,
)
from praxis.domain.links import LinkType, endpoints_are_valid

DocumentRef = Annotated[DocumentId, AfterValidator(ids.check_document_id)]
SpanRef = Annotated[SpanId, AfterValidator(ids.check_span_id)]
EstimateRef = Annotated[EstimateId, AfterValidator(ids.check_estimate_id)]
NodeRef = Annotated[NodeId, AfterValidator(ids.check_node_id)]


class Document(VersionedRecord):
    """A normalised source, held whole so every span into it can be re-read."""

    record_kind: ClassVar[RecordKind] = RecordKind.DOCUMENT

    id: DocumentId
    source_uri: NonEmptyStr
    source_kind: SourceKind
    title: NonEmptyStr | None = None
    content: str
    """The exact normalised text. Never stripped or re-wrapped: every span's
    byte offsets are offsets into precisely these bytes."""
    ingested_at: AwareDatetime

    @property
    def content_bytes(self) -> bytes:
        """The UTF-8 encoding that span offsets address."""
        return self.content.encode("utf-8")

    @property
    def content_hash(self) -> str:
        """SHA-256 of the content, used to recognise a re-ingested source.

        Derived rather than stored on the model so it cannot drift from the
        content it describes. The store keeps it in an indexed column.
        """
        return hashlib.sha256(self.content_bytes).hexdigest()

    @property
    def byte_length(self) -> int:
        """Length in bytes -- the upper bound on any span offset."""
        return len(self.content_bytes)


class Span(VersionedRecord):
    """An addressable byte range, carrying the text that range contains.

    This is the record the hallucinated-citation gate rests on. Holding the text
    alongside the offsets is redundant on purpose: the redundancy is what makes
    `praxis.domain.spans.verify_span` able to detect a citation that points
    somewhere its quoted text is not.
    """

    record_kind: ClassVar[RecordKind] = RecordKind.SPAN

    id: SpanId
    doc_id: DocumentRef
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=1)
    text: str

    @model_validator(mode="before")
    @classmethod
    def _derive_id(cls, data: object) -> object:
        """Fill in the content-addressed id when the caller omits it."""
        if not isinstance(data, dict) or "id" in data:
            return data
        try:
            derived = ids.span_id_for(data["doc_id"], data["start_byte"], data["end_byte"])
        except (KeyError, TypeError):
            return data  # let field validation report the real problem
        return {**data, "id": derived}

    @model_validator(mode="after")
    def _coordinates_agree(self) -> Self:
        if self.end_byte <= self.start_byte:
            message = f"empty or reversed span: [{self.start_byte}, {self.end_byte})"
            raise ValueError(message)
        width = self.end_byte - self.start_byte
        actual = len(self.text.encode("utf-8"))
        if actual != width:
            message = f"span text is {actual} bytes but its range is {width} bytes wide"
            raise ValueError(message)
        expected = ids.span_id_for(self.doc_id, self.start_byte, self.end_byte)
        if self.id != expected:
            message = (
                f"span id {self.id} does not address "
                f"{self.doc_id}[{self.start_byte}:{self.end_byte}]"
            )
            raise ValueError(message)
        return self

    @classmethod
    def covering(
        cls,
        document: Document,
        start_byte: int,
        end_byte: int,
        *,
        created_by: str,
        created_at: datetime,
    ) -> Self:
        """Build the span for a byte range by slicing the document itself.

        The preferred constructor, because a span built this way cannot quote
        text the document does not contain.

        Raises:
            ValueError: if the range is empty, runs past the end of the
                document, or splits a multi-byte character.
        """
        raw = document.content_bytes
        if not 0 <= start_byte < end_byte <= len(raw):
            message = (
                f"[{start_byte}, {end_byte}) is not a range within "
                f"{document.id}, which is {len(raw)} bytes long"
            )
            raise ValueError(message)
        try:
            text = raw[start_byte:end_byte].decode("utf-8")
        except UnicodeDecodeError as exc:
            message = f"[{start_byte}, {end_byte}) splits a character in {document.id}"
            raise ValueError(message) from exc
        return cls(
            id=ids.span_id_for(document.id, start_byte, end_byte),
            doc_id=document.id,
            start_byte=start_byte,
            end_byte=end_byte,
            text=text,
            created_by=created_by,
            created_at=created_at,
        )


class RejectedOption(BaseModel):
    """An alternative that was considered and turned down, with the reason.

    A decision with no rejected options is a note. Recording the reason is what
    lets `ArchaeologistAgent` answer "why not X" years later without anyone
    having to remember.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    option: NonEmptyStr
    reason: NonEmptyStr


class Decision(VersionedRecord):
    """A choice, its rejected alternatives, and who made it when."""

    record_kind: ClassVar[RecordKind] = RecordKind.DECISION

    id: DecisionId
    title: NonEmptyStr
    chosen: NonEmptyStr
    rejected: tuple[RejectedOption, ...] = Field(min_length=1)
    """At least one. The project's own ADR convention, enforced rather than
    trusted -- if the option space really was that narrow, say so in a rejected
    option and explain why."""
    decision_maker: NonEmptyStr
    decided_at: AwareDatetime
    scope: DecisionScope
    impact: Impact
    status: DecisionStatus = DecisionStatus.PROPOSED
    span_id: SpanRef
    confidence: Confidence


class Assumption(VersionedRecord):
    """A belief a decision rests on, compiled into something checkable."""

    record_kind: ClassVar[RecordKind] = RecordKind.ASSUMPTION

    id: AssumptionId
    statement: NonEmptyStr
    """The assumption in the words it was written in."""
    predicate: NonEmptyStr
    """The same claim as an expression `AssumptionMonitor` can evaluate, e.g.
    `migration_weeks <= 6`. Held as text until the Phase 5 DSL parses it."""
    expiry_condition: NonEmptyStr
    """When to re-check, e.g. `when(phases_completed >= 6)`."""
    status: AssumptionStatus = AssumptionStatus.UNVERIFIED
    last_evaluated_at: AwareDatetime | None = None
    span_id: SpanRef
    confidence: Confidence

    @model_validator(mode="after")
    def _status_matches_evaluation(self) -> Self:
        """A verdict must be accompanied by the time it was reached.

        Without this, a breached assumption with no evaluation time is
        indistinguishable from one whose monitor never ran, and the difference
        is the entire value of the status field.
        """
        evaluated = self.status is not AssumptionStatus.UNVERIFIED
        if evaluated and self.last_evaluated_at is None:
            message = f"status is {self.status.value} but last_evaluated_at is unset"
            raise ValueError(message)
        if not evaluated and self.last_evaluated_at is not None:
            message = "last_evaluated_at is set but the status is still unverified"
            raise ValueError(message)
        return self


class Estimate(VersionedRecord):
    """A prediction of quantity, split into working time and blocked time.

    The split is not decoration. `OUT-0001` recorded a Phase 0 estimate that
    looked accurate against wall clock and was 2.3x wrong about engineering
    time, because an external block absorbed the difference. Storing only a
    total makes that error invisible; storing the two parts makes it impossible
    to log a total that hides one.
    """

    record_kind: ClassVar[RecordKind] = RecordKind.ESTIMATE

    id: EstimateId
    subject: NonEmptyStr
    owner: NonEmptyStr
    """Whose estimate it is. Calibration is per estimator, so this is a key."""
    work_class: WorkClass
    active_quantity: Quantity
    """Predicted hands-on effort."""
    blocked_quantity: Quantity = Decimal(0)
    """Predicted time waiting on something outside the estimator's control.
    Usually zero, and worth predicting explicitly when it is not."""
    unit: Unit
    confidence: Confidence
    conditions: tuple[NonEmptyStr, ...] = ()
    """What the estimate assumes. These are the raw material `FusionBridge`
    reads when deciding an assumption is an estimate in disguise."""
    estimated_at: AwareDatetime
    span_id: SpanRef

    @property
    def total_quantity(self) -> Quantity:
        """Active plus blocked -- derived, so the parts can never disagree."""
        return self.active_quantity + self.blocked_quantity

    @model_validator(mode="after")
    def _predicts_something(self) -> Self:
        if self.total_quantity <= 0:
            message = "an estimate of zero predicts nothing"
            raise ValueError(message)
        return self


class Outcome(VersionedRecord):
    """What actually happened, in the same shape as the estimate it resolves.

    Field-for-field comparable with `Estimate` on purpose: `ScoringAgent`
    compares active against active, so a phase that ran long only because it was
    blocked does not read as an estimation error.
    """

    record_kind: ClassVar[RecordKind] = RecordKind.OUTCOME

    id: OutcomeId
    estimate_id: EstimateRef
    active_quantity: Quantity | None = None
    blocked_quantity: Quantity | None = None
    unit: Unit
    match_quality: MatchQuality
    resolved_at: AwareDatetime | None = None
    notes: str = ""
    span_id: SpanRef | None = None
    """Optional, unlike on the records above: an outcome can be a measurement
    rather than a claim extracted from a document."""

    @model_validator(mode="after")
    def _unresolved_carries_no_numbers(self) -> Self:
        """Keep `unresolved` honest in both directions.

        An unresolved outcome exists so that estimates which never resolved stay
        visible in the calibration data instead of being dropped -- which is how
        a curve ends up flattering its estimator. That only works if
        `unresolved` cannot also carry a quantity.
        """
        unresolved = self.match_quality is MatchQuality.UNRESOLVED
        has_numbers = self.active_quantity is not None or self.blocked_quantity is not None
        if unresolved and (has_numbers or self.resolved_at is not None):
            message = "an unresolved outcome cannot carry a quantity or a resolution time"
            raise ValueError(message)
        if not unresolved and (not has_numbers or self.resolved_at is None):
            message = f"a {self.match_quality.value} outcome needs a quantity and a resolution time"
            raise ValueError(message)
        return self


class Link(VersionedRecord):
    """A typed edge. The graph is nothing but these.

    Validation here enforces the grammar of the edge vocabulary -- which kinds
    may sit at each end of which type, and that a declared endpoint kind agrees
    with the prefix of the id it points at. SQLite enforces that both endpoints
    exist; this enforces that the sentence means something.
    """

    record_kind: ClassVar[RecordKind] = RecordKind.LINK

    id: LinkId
    link_type: LinkType
    source_id: NodeRef
    source_kind: RecordKind
    target_id: NodeRef
    target_kind: RecordKind
    confidence: Confidence
    rationale: NonEmptyStr
    span_id: SpanRef | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_id(cls, data: object) -> object:
        """Fill in the content-addressed id when the caller omits it."""
        if not isinstance(data, dict) or "id" in data:
            return data
        try:
            derived = ids.link_id_for(data["link_type"], data["source_id"], data["target_id"])
        except (KeyError, TypeError, AttributeError):
            return data
        return {**data, "id": derived}

    @model_validator(mode="after")
    def _is_a_well_formed_edge(self) -> Self:
        if self.source_id == self.target_id:
            message = f"{self.source_id} cannot {self.link_type.value} itself"
            raise ValueError(message)
        for role, node_id, kind in (
            ("source", self.source_id, self.source_kind),
            ("target", self.target_id, self.target_kind),
        ):
            if kind not in GRAPH_KINDS:
                message = f"{kind.value} cannot be a graph endpoint"
                raise ValueError(message)
            if ids.kind_of(node_id) is not kind:
                message = f"{role} {node_id} is not a {kind.value}"
                raise ValueError(message)
        if not endpoints_are_valid(self.link_type, self.source_kind, self.target_kind):
            message = (
                f"{self.source_kind.value} --{self.link_type.value}--> "
                f"{self.target_kind.value} is not a relationship this vocabulary expresses"
            )
            raise ValueError(message)
        expected = ids.link_id_for(self.link_type, self.source_id, self.target_id)
        if self.id != expected:
            message = f"link id {self.id} does not address this edge"
            raise ValueError(message)
        return self

    @classmethod
    def between(  # noqa: PLR0913 -- an edge has this many irreducible parts
        cls,
        link_type: LinkType,
        source_id: NodeId,
        target_id: NodeId,
        *,
        rationale: str,
        confidence: float,
        created_by: str,
        created_at: datetime,
        span_id: SpanId | None = None,
    ) -> Self:
        """Build an edge, deriving both its id and its endpoint kinds.

        The endpoint kinds come from the id prefixes rather than from the
        caller, which removes the only way a `Link` could be constructed with a
        kind that contradicts the node it points at.
        """
        return cls(
            id=ids.link_id_for(link_type, source_id, target_id),
            link_type=link_type,
            source_id=source_id,
            source_kind=ids.kind_of(source_id),
            target_id=target_id,
            target_kind=ids.kind_of(target_id),
            rationale=rationale,
            confidence=confidence,
            span_id=span_id,
            created_by=created_by,
            created_at=created_at,
        )


class Finding(VersionedRecord):
    """An allegation about a record, and what survived being challenged."""

    record_kind: ClassVar[RecordKind] = RecordKind.FINDING

    id: FindingId
    kind: FindingKind
    subject_kind: RecordKind
    subject_id: NodeRef
    prosecution: NonEmptyStr
    """The case against the subject, stated so it can be argued with."""
    challenge: NonEmptyStr | None = None
    """`ChallengerAgent`'s rebuttal. Absent until the finding has been tested."""
    verdict: Verdict = Verdict.UNDECIDED
    severity: Severity
    confidence: Confidence
    evidence_span_ids: tuple[SpanRef, ...] = ()
    """May be empty: a calibration finding is computed from the store rather
    than quoted from a document."""
    detected_at: AwareDatetime

    @model_validator(mode="after")
    def _verdict_rests_on_a_challenge(self) -> Self:
        """No finding is decided without the argument that decided it.

        Nothing high-severity reaches a human without surviving the challenger,
        so a verdict with no recorded challenge is a claim that a review
        happened when there is no evidence it did.
        """
        if self.verdict is not Verdict.UNDECIDED and self.challenge is None:
            message = f"verdict is {self.verdict.value} but no challenge was recorded"
            raise ValueError(message)
        if self.subject_kind not in GRAPH_KINDS:
            message = f"a finding cannot be about a {self.subject_kind.value}"
            raise ValueError(message)
        if ids.kind_of(self.subject_id) is not self.subject_kind:
            message = f"subject {self.subject_id} is not a {self.subject_kind.value}"
            raise ValueError(message)
        return self


class AuditEvent(Record):
    """Who or what wrote which version of what, when, and why.

    Not versioned and never retracted -- this is the log, and a log that can be
    revised is a log nobody can rely on. It is also not a graph node: nothing
    links to an audit row, because an audit trail that participates in the graph
    it audits stops being independent evidence.
    """

    record_kind: ClassVar[RecordKind] = RecordKind.AUDIT_EVENT

    id: AuditEventId
    occurred_at: AwareDatetime
    actor: NonEmptyStr
    action: AuditAction
    entity_kind: RecordKind
    entity_id: NodeRef
    entity_version: int = Field(ge=1)
    reason: NonEmptyStr
    """Why the write happened. The one field here a human will actually read."""
    run_id: NonEmptyStr | None = None
    """Ties every write in one orchestrator run together, once Phase 4 has runs."""


AnyRecord = (
    Document | Span | Decision | Assumption | Estimate | Outcome | Link | Finding | AuditEvent
)

RECORD_TYPES: dict[RecordKind, type[Record]] = {
    cls.record_kind: cls
    for cls in (
        Document,
        Span,
        Decision,
        Assumption,
        Estimate,
        Outcome,
        Link,
        Finding,
        AuditEvent,
    )
}
"""Every record class by kind, so the store can dispatch generically instead of
carrying a nine-branch conditional in each of its read and write paths."""
