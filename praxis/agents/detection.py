"""One contradiction pass over a store: read the graph, write the edges it lacks.

The store-facing half, kept apart from `ContradictionDetector` for the reason
`praxis.agents.extraction` is kept apart from the three agents it runs -- the
agent decides, the pipeline holds the store. It is also what makes the detector
testable without SQLite.

**Re-running is free of duplicates by construction rather than by a check.** A
`Link`'s id is derived from its type and its two endpoints (ADR 0008), and this
orders every pair by id before building one, so the same contradiction found
twice is the same id twice. The edges the store already holds are handed to the
detector as `known` so they are not paid for again, and the write itself skips
an id already present rather than raising -- which is the difference between a
second pass costing nothing and a second pass failing.

Assumptions and decisions are read together and compared in one index, because a
decision contradicting an assumption is a real relationship the edge grammar
already permits -- `contradicts` runs between any two claims -- and splitting the
pass in two would mean two indexes that never see each other's records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from praxis.agents.contradiction import ContradictionDetector
from praxis.agents.results import Contradiction, DetectionResult
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Link
from praxis.llm.provider import LLMProvider
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository

_log = get_logger(__name__)

DETECTION_ACTOR: Final = "ContradictionDetector"
"""Who the audit trail records for a `contradicts` edge."""


@dataclass(frozen=True, slots=True)
class DetectionRun:
    """What one pass over a store found and wrote.

    Attributes:
        result: Everything the detector concluded, including what blocking
            skipped -- reported whole, because a pair never proposed and a pair
            judged not to conflict are different results.
        written: Edges that were new to the store.
        records: Assumptions and decisions read.
    """

    result: DetectionResult
    written: tuple[Link, ...] = ()
    records: int = 0

    @property
    def contradictions(self) -> tuple[Contradiction, ...]:
        """Everything found, whether or not the store already held it."""
        return self.result.contradictions

    @property
    def calls(self) -> int:
        """Model calls made."""
        return self.result.calls


def detect_in_store(
    repository: Repository,
    *,
    provider: LLMProvider | None = None,
    at: datetime | None = None,
    run_id: str | None = None,
    detector: ContradictionDetector | None = None,
) -> DetectionRun:
    """Find contradictions among everything a store holds, and write the new ones.

    Args:
        repository: The store to read and write.
        provider: The seam. Omitted, the arithmetic stage still runs and still
            writes every contradiction it can prove.
        at: When this ran. Defaults to now, in UTC. Timezone-aware, invariant 5.
        run_id: Recorded on every audit row this writes.
        detector: Supplied by a test that wants different bounds. There is one
            behaviour otherwise.

    Returns:
        What was found and what was new.

    Raises:
        ProviderError: for failures about the run rather than one batch.
        StoreError: if a write is refused.
    """
    occurred_at = at if at is not None else datetime.now(UTC)
    records: list[Assumption | Decision] = [
        *repository.list_all(Assumption),
        *repository.list_all(Decision),
    ]
    agent = detector if detector is not None else ContradictionDetector(provider)
    result = agent.detect(records, at=occurred_at, known=_existing(repository, records))
    written = tuple(
        link
        for found in result.contradictions
        if (link := _write(repository, found, at=occurred_at, run_id=run_id)) is not None
    )
    _log.info(
        "detection_run",
        records=len(records),
        proposed=len(result.blocking.candidates),
        judged=result.judged,
        found=len(result.contradictions),
        written=len(written),
        calls=result.calls,
    )
    return DetectionRun(result=result, written=written, records=len(records))


def _existing(
    repository: Repository, records: list[Assumption | Decision]
) -> tuple[tuple[str, str], ...]:
    """Every `contradicts` edge the store already holds, as pairs.

    Read through `links_touching`, which is what `praxis.domain.links` exists
    for: the type is symmetric and stored once, so an edge written in the other
    direction still has to count as known.
    """
    return tuple(
        (link.source_id, link.target_id)
        for record in records
        for link in repository.links_touching(record.id, types=(LinkType.CONTRADICTS,))
    )


def _write(
    repository: Repository, found: Contradiction, *, at: datetime, run_id: str | None
) -> Link | None:
    """Write one edge, or `None` if the store already had it.

    Checked rather than caught. A duplicate here is the ordinary outcome of a
    second pass over an unchanged corpus, and an exception is for something
    going wrong.
    """
    if repository.exists(found.link.id):
        return None
    return repository.add(
        found.link,
        actor=DETECTION_ACTOR,
        reason=f"settled by {found.settled_by.value}: {found.rationale}",
        at=at,
        run_id=run_id,
    )
