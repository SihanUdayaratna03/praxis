"""The ablation ladder: one graded run per rung, each adding one component.

Every rung runs the same corpus in a store of its own, so a difference between
two rows is the component between them and nothing else. See ADR 0035 for why
the ladder is cumulative and what each rung is allowed to claim.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from praxis.eval.harness import EvalResult, evaluate
from praxis.eval.metrics import EMPTY, MaeImprovement, PairScore, mae_improvement, overall
from praxis.eval.stages import Stages
from praxis.llm.provider import LLMProvider
from praxis.obs.logging import get_logger
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Rung:
    """One row of the table: a name, what it adds, and the stages it runs.

    Attributes:
        name: What the row is called.
        adds: The component this rung adds to the one below it.
        stages: The configuration `evaluate` runs.
    """

    name: str
    adds: str
    stages: Stages


LADDER: tuple[Rung, ...] = (
    Rung("floor", "paragraph floor, extraction only", Stages.floor()),
    Rung("segmenter", "learned segmentation", Stages.floor().with_(floor_only=False)),
    Rung(
        "+ estimation",
        "half B: outcomes and classification",
        Stages.floor().with_(floor_only=False, estimation=True),
    ),
    Rung(
        "+ memory",
        "formalization, monitoring, detection",
        Stages.floor().with_(floor_only=False, estimation=True, memory=True),
    ),
    Rung(
        "+ calibration",
        "per-group bias correction",
        Stages.floor().with_(floor_only=False, estimation=True, memory=True, calibration=True),
    ),
    Rung(
        "+ fusion",
        "estimated_as edges and the flips they cause",
        Stages.floor().with_(
            floor_only=False, estimation=True, memory=True, calibration=True, fusion=True
        ),
    ),
    Rung("+ governance", "challenger, curator, abstention gate", Stages()),
)
"""The rungs, in the order the pipeline runs them.

Cumulative, so each row is the one above it plus one component and a difference
between two adjacent rows is attributable. The floor is ADR 0011's paragraph
segmenter, which makes no model call.
"""


@dataclass(frozen=True, slots=True)
class AblationRow:
    """One rung's numbers, in the shape the brief named.

    Attributes:
        rung: Which rung produced them.
        extraction: Micro-averaged precision and recall over the graded kinds.
        citation_integrity: Share of claims that cited something real.
        breach_detection: Precision and recall over the assumption breaches.
        mae: Calibration error before and after correction.
        abstention_precision: Share of abstentions that really fail a rule.
            Trivially 1 below the governance rung, where every finding abstains
            for want of a challenge -- `emitted` is what separates the two.
        emitted: Findings the gate concluded on.
        abstained: Findings it routed to a person instead.
        findings: Findings standing in the store. The column the calibration
            and fusion rungs move -- neither writes a claim, so extraction
            precision cannot see them at all.
        flips: Edges where calibration turned a held predicate into a violated
            one. What the fusion rung exists to produce.
        fusion_recall: Of the labelled `estimated_as` edges, how many were found.
        cost_per_document: Total spent per document, across every agent.
        cost_by_agent: The same, split. Which agents a rung paid for at all is
            how the floor rung is told from the one above it offline, where
            every amount is zero.
        calls: Model calls every stage of the rung made.
    """

    rung: Rung
    extraction: PairScore
    citation_integrity: Decimal = EMPTY
    breach_detection: PairScore = field(default_factory=lambda: PairScore(label="breach"))
    mae: MaeImprovement = field(default_factory=MaeImprovement)
    abstention_precision: Decimal = EMPTY
    emitted: int = 0
    abstained: int = 0
    findings: int = 0
    flips: int = 0
    fusion_recall: Decimal = EMPTY
    cost_per_document: Decimal = EMPTY
    cost_by_agent: Mapping[str, Decimal] = field(default_factory=dict)
    calls: int = 0


def row_for(rung: Rung, result: EvalResult) -> AblationRow:
    """Reduce a graded run to the row the table prints."""
    return AblationRow(
        rung=rung,
        extraction=overall(kind.score for kind in result.kinds),
        citation_integrity=result.citations.integrity,
        breach_detection=result.memory.monitoring.breaches,
        mae=mae_improvement(result.calibration.backtest),
        abstention_precision=result.governance.abstention_precision,
        emitted=result.governance.emitted,
        abstained=result.governance.abstained,
        findings=result.governance.findings,
        flips=result.fusion.flips,
        fusion_recall=result.fusion_recall,
        cost_per_document=sum(result.cost.values(), EMPTY),
        cost_by_agent=dict(result.cost),
        calls=result.calls,
    )


@dataclass(frozen=True, slots=True)
class AblationTable:
    """Every rung's row, plus what produced them.

    Attributes:
        rows: One per rung, in ladder order.
        documents: Documents each rung was run over.
    """

    rows: tuple[AblationRow, ...] = ()
    documents: int = 0

    def row(self, name: str) -> AblationRow | None:
        """One rung's row by name, or `None` if it was not run."""
        return next((row for row in self.rows if row.rung.name == name), None)


@contextmanager
def in_memory_store() -> Iterator[Repository]:
    """A migrated store of one rung's own. Closed when the rung is done."""
    connection = connect(MEMORY)
    try:
        migrate(connection)
        repository = Repository(connection)
    except BaseException:
        connection.close()
        raise
    try:
        yield repository
    finally:
        repository.close()


def ablate(
    corpus: Path,
    make_provider: Callable[[Repository, str], LLMProvider],
    *,
    at: datetime | None = None,
    ladder: Sequence[Rung] = LADDER,
    store: Callable[[], AbstractContextManager[Repository]] = in_memory_store,
) -> AblationTable:
    """Run every rung over one corpus and reduce each to a row.

    Args:
        corpus: The corpus root, holding `documents/` and its key.
        make_provider: Builds the seam for one rung, given that rung's store and
            run id. A rung needs its own so the cost column is per rung.
        at: When this ran. Defaults to now, in UTC, per stage.
        ladder: The rungs to run. Defaults to `LADDER`.
        store: Opens a store for one rung. Defaults to an in-memory one.

    Returns:
        The table, in ladder order.
    """
    rows: list[AblationRow] = []
    documents = 0
    for rung in ladder:
        with store() as repository:
            run_id = f"ablation-{rung.name}"
            result = evaluate(
                repository,
                make_provider(repository, run_id),
                corpus,
                at=at,
                run_id=run_id,
                stages=rung.stages,
            )
        rows.append(row_for(rung, result))
        documents = result.documents
        _log.info("ablation_rung", rung=rung.name, calls=result.calls)
    return AblationTable(rows=tuple(rows), documents=documents)
