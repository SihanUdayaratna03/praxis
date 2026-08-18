"""What one extraction run produced, in the shape the run reports it.

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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from praxis.agents.errors import Refusal
from praxis.agents.extractor import ExtractedAssumption
from praxis.agents.structurer import StructuredDecision
from praxis.domain.enums import RecordKind
from praxis.domain.ids import DocumentId
from praxis.domain.records import Document


class Stage(StrEnum):
    """Which of the three agents lost a record.

    Carried on every refusal, because "the scout cited a passage it was not
    shown" and "the extractor did" are the same `Refusal` about two different
    prompts, and a table that cannot tell them apart cannot say which prompt to
    change.
    """

    SCAN = "scan"
    STRUCTURE = "structure"
    EXTRACT = "extract"


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
