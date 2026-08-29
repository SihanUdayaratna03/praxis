"""What an agent run produced, in the shape the run reports it.

Separate from the pipeline that fills them because they are what everything
*downstream* reads: `praxis.eval` grades against them, the CLI prints them, and
Phase 10 charts them. A caller that only wants to read a run's numbers should
not have to import the object that makes model calls.

The one type worth arguing about is `Refused`. Each agent has its own rejection
shape, fitted to its own caller, and each is right for that caller -- the scout
reports an ordinal it was never shown, the extractor reports which of two
records it lost. This is the shape the *run* speaks in, so the eval harness
groups one sequence rather than three, and `stage` is what keeps the merge from
losing what the three types knew apart.

Phase 5's detection types live here for the same reason and not because they
are related to extraction: `praxis.eval` reads them, and a module that only
wants a run's numbers should not have to import the agent that makes the
calls -- which for `ContradictionDetector` would drag in the whole predicate
language as well.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from praxis.agents.blocking import Blocking
from praxis.agents.errors import Refusal
from praxis.agents.extractor import ExtractedAssumption
from praxis.agents.structurer import StructuredDecision
from praxis.domain.enums import RecordKind
from praxis.domain.ids import DocumentId
from praxis.domain.records import Document, Link


class Stage(StrEnum):
    """Which agent lost a record.

    Carried on every refusal, because "the scout cited a passage it was not
    shown" and "the extractor did" are the same `Refusal` about two different
    prompts, and a table that cannot tell them apart cannot say which prompt to
    change.

    Half A's three and Half B's three share this enum rather than having one
    each. The eval harness groups a single sequence of refusals, and two
    vocabularies for "the model cited a passage it was not shown" would split
    one row of that table down a line nobody could act on.
    """

    SCAN = "scan"
    STRUCTURE = "structure"
    EXTRACT = "extract"

    ESTIMATE = "estimate"
    """`EstimateExtractor` reading a document for the quantities it predicts."""

    CLASSIFY = "classify"
    """`WorkClassifier` putting an estimate on the axis calibration groups by."""

    MATCH = "match"
    """`OutcomeMatcher` looking for what actually happened to an estimate."""


@dataclass(frozen=True, slots=True)
class Refused:
    """One record that did not enter the store, and everything about why.

    The three agents each have their own rejection type, shaped for their own
    caller. This is the shape the *run* reports in, so the eval harness groups
    one sequence rather than three.
    """

    stage: Stage
    refusal: Refusal
    doc_id: DocumentId
    detail: str
    lost: RecordKind
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class DocumentExtraction:
    """What one document produced, and what it cost.

    Attributes:
        document: The document read.
        decisions: Decisions written, with the edge justifying each.
        assumptions: Assumptions written, with their edges and any estimate.
        refused: Everything lost, at whichever stage lost it.
        candidates: Passages the scout marked, before any were structured.
        calls: Model calls made across all three agents, repairs included.
        blind_windows: Windows the scout never got an answer about. Decisions
            in those passages were not found rather than absent.
        already_extracted: Whether the store already held decisions here.
    """

    document: Document
    decisions: tuple[StructuredDecision, ...] = ()
    assumptions: tuple[ExtractedAssumption, ...] = ()
    refused: tuple[Refused, ...] = ()
    candidates: int = 0
    calls: int = 0
    blind_windows: int = 0
    already_extracted: bool = False

    @property
    def estimates(self) -> int:
        """Assumptions that turned out to be estimates -- the fusion count."""
        return sum(1 for found in self.assumptions if found.estimate is not None)


@dataclass(frozen=True, slots=True)
class ExtractionRun:
    """Everything one run over a store did.

    Reported rather than logged, for the reason `IngestionRun` is: these are the
    numbers Phase 10 charts and the ones a person needs after a corpus run.
    """

    documents: tuple[DocumentExtraction, ...] = ()

    @property
    def decisions(self) -> int:
        """Decisions written across the run."""
        return sum(len(result.decisions) for result in self.documents)

    @property
    def assumptions(self) -> int:
        """Assumptions written across the run."""
        return sum(len(result.assumptions) for result in self.documents)

    @property
    def estimates(self) -> int:
        """Estimates written, each one an assumption that was a bet."""
        return sum(result.estimates for result in self.documents)

    @property
    def refused(self) -> tuple[Refused, ...]:
        """Every record lost, in document order."""
        return tuple(refusal for result in self.documents for refusal in result.refused)

    @property
    def calls(self) -> int:
        """Model calls made across the run, repairs included."""
        return sum(result.calls for result in self.documents)

    @property
    def blind_windows(self) -> int:
        """Windows nothing was ever learned about."""
        return sum(result.blind_windows for result in self.documents)

    def refused_by(self, refusal: Refusal) -> int:
        """How many records one defect cost."""
        return sum(1 for entry in self.refused if entry.refusal is refusal)


class Settlement(StrEnum):
    """Which stage decided a pair, so the two recalls can be reported apart."""

    ARITHMETIC = "arithmetic"
    """Two predicates whose satisfying ranges do not intersect. Free, certain,
    and reproducible."""

    MODEL = "model"
    """A judgement about two claims in prose, carrying the model's confidence."""


@dataclass(frozen=True, slots=True)
class Contradiction:
    """Two records that cannot both hold, and the edge asserting it.

    Attributes:
        link: The `contradicts` edge, ready to store. Its id is derived from the
            ordered pair, so re-running writes the same edge rather than a
            second one.
        settled_by: Which stage decided.
        rationale: Why they conflict, in one sentence. Also the edge's own
            rationale -- carried here too so a caller reporting a run does not
            have to reach into the record.
    """

    link: Link
    settled_by: Settlement
    rationale: str

    @property
    def pair(self) -> tuple[str, str]:
        """The two records, in the order the edge stores them."""
        return self.link.source_id, self.link.target_id


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """What one detection proposed, settled, asked about and found.

    Attributes:
        contradictions: The edges to write, arithmetic ones first.
        blocking: What candidate generation proposed and what it skipped.
        judged: Pairs sent to a model.
        calls: Model calls made, repairs included.
    """

    contradictions: tuple[Contradiction, ...] = ()
    blocking: Blocking = field(default_factory=Blocking)
    judged: int = 0
    calls: int = 0

    @property
    def by_arithmetic(self) -> tuple[Contradiction, ...]:
        """Those a model was never asked about."""
        return tuple(
            found for found in self.contradictions if found.settled_by is Settlement.ARITHMETIC
        )

    @property
    def by_model(self) -> tuple[Contradiction, ...]:
        """Those that were a judgement."""
        return tuple(found for found in self.contradictions if found.settled_by is Settlement.MODEL)
