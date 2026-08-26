"""One formalization pass over a store: compile the assumptions that are not yet.

The store-facing half of `AssumptionFormalizer`, kept apart from it for the
reason `praxis.agents.extraction` is kept apart from the three agents it runs.

**Re-running costs nothing, and the two reasons an assumption is skipped are
different.** One already parses, so there is nothing to compile. The other was
attempted before and could not be compiled -- and that is read off the *audit
trail*, which already records that this agent wrote a version of the record.
Without the second check a re-run would pay the reason tier again for every
assumption that is not compilable, every time, forever. Neither check needs a
column: both are questions the store can already answer, which is the same
argument `praxis.monitor.run` makes about writing only on a change.

`--force` exists because the first check is a property of the *text* and the
second is a property of the history. A prompt that got better should be able to
re-attempt what an older one failed at, and the only honest way to offer that is
to say so rather than to invent a heuristic about when to retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from praxis.agents.formalizer import (
    AssumptionFormalizer,
    Formalization,
    FormalizationRefusal,
    is_checkable,
)
from praxis.domain.records import Assumption, Span
from praxis.llm.provider import LLMProvider
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository

_log = get_logger(__name__)

FORMALIZATION_ACTOR: Final = "AssumptionFormalizer"
"""Who the audit trail records, and what a re-run looks for to know it has
already tried."""


@dataclass(frozen=True, slots=True)
class FormalizationRun:
    """What one pass compiled, kept, and did not pay for twice.

    Attributes:
        formalized: Every assumption a new version was written for, checkable
            or not -- an uncompilable one is kept and marked rather than
            dropped, and it is still a write.
        refusals: Assumptions the model said nothing usable about. Distinct
            from an uncheckable formalization: there was no attempt to keep.
        already_checkable: Skipped because their predicate and expiry already
            parse. Nothing to compile.
        already_attempted: Skipped because this agent has written a version of
            them before and they still do not parse. Re-attempting would spend
            the reason tier on the same failure every run.
        calls: Model calls made, retries included.
    """

    formalized: tuple[Formalization, ...] = ()
    refusals: tuple[FormalizationRefusal, ...] = ()
    already_checkable: int = 0
    already_attempted: int = 0
    calls: int = 0

    @property
    def checkable(self) -> tuple[Formalization, ...]:
        """The ones the monitor will be able to reach a verdict about."""
        return tuple(found for found in self.formalized if found.checkable)

    @property
    def marked(self) -> tuple[Formalization, ...]:
        """The ones kept as a best attempt, with a stated reason.

        Reported apart from `checkable` because the difference is what a person
        would act on: these are the assumptions somebody has to look at.
        """
        return tuple(found for found in self.formalized if not found.checkable)

    @property
    def skipped(self) -> int:
        """Assumptions no call was made about."""
        return self.already_checkable + self.already_attempted


def formalize_store(
    repository: Repository,
    provider: LLMProvider,
    *,
    at: datetime | None = None,
    run_id: str | None = None,
    force: bool = False,
) -> FormalizationRun:
    """Compile every assumption in a store that is not already compiled.

    Args:
        repository: The store to read and write.
        provider: The seam, from `provider_for`.
        at: When this ran. Defaults to now, in UTC. Timezone-aware, invariant 5.
        run_id: Recorded on every audit row this writes.
        force: Re-attempt assumptions this agent has already failed at. For a
            new prompt version, and named rather than guessed at.

    Returns:
        What was compiled, kept, refused and skipped.

    Raises:
        ProviderError: for failures about the run rather than one record.
        StoreError: if a write is refused.
    """
    occurred_at = at if at is not None else datetime.now(UTC)
    agent = AssumptionFormalizer(provider)
    formalized: list[Formalization] = []
    refusals: list[FormalizationRefusal] = []
    checkable = attempted = calls = 0

    for assumption in repository.list_all(Assumption):
        if is_checkable(assumption):
            checkable += 1
            continue
        if not force and _already_attempted(repository, assumption):
            attempted += 1
            continue
        evidence = repository.get(Span, assumption.span_id)
        if evidence is None:  # pragma: no cover -- a foreign key makes this unreachable
            continue
        result = agent.formalize(assumption, evidence, at=occurred_at)
        calls += result.calls
        if isinstance(result, FormalizationRefusal):
            refusals.append(result)
            continue
        formalized.append(result)
        _record(repository, result, at=occurred_at, run_id=run_id)

    _log.info(
        "formalization_run",
        compiled=len(formalized),
        refused=len(refusals),
        already_checkable=checkable,
        already_attempted=attempted,
        calls=calls,
    )
    return FormalizationRun(
        formalized=tuple(formalized),
        refusals=tuple(refusals),
        already_checkable=checkable,
        already_attempted=attempted,
        calls=calls,
    )


def _already_attempted(repository: Repository, assumption: Assumption) -> bool:
    """Whether this agent has written a version of this assumption before.

    Read off the audit trail rather than off a flag, because the trail already
    records exactly this and a flag would be a second copy of it that could
    disagree. The store is append-only, so the record of the attempt cannot go
    missing.
    """
    return any(event.actor == FORMALIZATION_ACTOR for event in repository.audit_for(assumption.id))


def _record(
    repository: Repository, result: Formalization, *, at: datetime, run_id: str | None
) -> None:
    """Write the compiled assumption as a new version.

    Append-only, invariant 7: the words the extractor read stay readable beside
    the expression they were compiled into, so "what did this predicate used to
    say" is a query rather than a memory.
    """
    repository.revise(
        result.assumption,
        actor=FORMALIZATION_ACTOR,
        reason=result.reason,
        at=at,
        run_id=run_id,
    )
