"""One governance pass over a store: argue, curate, route, write only what changed.

The counterpart to `praxis.agents.fusion_pass` and `praxis.agents.calibration`,
and the same shape for the same reasons. It runs **per store** rather than per
document: governance is a question about what the store already holds, so there
is nothing per-document to iterate.

## Three stages, in an order that cannot move

```
  findings ──▶ ChallengerAgent ──▶ CuratorAgent ──▶ AbstentionGate
               one reason call     no model call    no model call
               per batch           writes edges     writes nothing
                                   and retractions
```

**The challenger runs first** because the gate reads the verdict it writes.
Routing before challenging would send every finding to a person as
`NEVER_CHALLENGED`, which is technically true and useless.

**The gate runs last and writes nothing.** A disposition is recomputed rather
than stored (ADR 0032), so it is a report over the store this pass has just
finished changing rather than a change of its own -- which is also why routing
after curation is correct: an assumption the curator has just retracted makes
the findings against it `SUBJECT_WITHDRAWN`, and a gate that ran first would have
concluded on a claim that no longer stands.

**Curation sits between them** and could sit anywhere, since nothing it writes
is read by the other two. It is placed here so the gate sees the store's final
state, which is the only ordering that makes the abstention count true at the
moment it is printed.

## Writing only on a change

The rule `praxis.monitor.run` established and every store pass since has
repeated. A finding whose recorded challenge already says what this pass would
say gets no new version, so a second pass over an unchanged store writes
nothing at all -- and the model calls are skipped too, because
`ChallengerAgent` refuses to re-argue a finding that already carries a verdict.

Curation is idempotent by the same mechanism from the other side: a merge whose
`supersedes` edge already stands is refused by the curator, and a retirement of
an already-retracted assumption cannot arise because `list_all` excludes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from praxis.agents.abstention import AbstentionGate, GateResult
from praxis.agents.challenger import Challenge, ChallengerAgent, ChallengeResult
from praxis.agents.curator import (
    CURATOR_NAME,
    CurationResult,
    CuratorAgent,
    Merge,
    Retirement,
    supersedes_link,
)
from praxis.domain.records import Assumption, Finding
from praxis.llm.provider import LLMProvider
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository

_log = get_logger(__name__)

GOVERNANCE_ACTOR: Final = "governance"
"""The actor on the audit rows this pass writes.

The pass rather than the agent, as every store pass since Phase 6 has done: the
audit trail records who *wrote* a record, and `created_by` on the record itself
records which component produced it. Both questions have an answer and neither
is inferred from the other.
"""


@dataclass(frozen=True, slots=True)
class GovernanceRun:
    """Everything one governance pass over a store did.

    Attributes:
        challenge: What the challenger concluded, refusals and gaps included.
        curation: What the curator would collapse and withdraw, and what it
            refused to touch. The refusals are most of it and are not filtered.
        gate: Where every surviving finding goes. Computed over the store's
            state *after* the writes below, so it is true at the moment it is
            printed.
        decided: Findings given a verdict this pass, as new versions.
        unchanged: Findings whose recorded challenge already said this. A
            second pass over a settled store is all of these and nothing else.
        merged: `supersedes` edges written, one per merge.
        retired: Assumptions withdrawn.
        calls: Model calls made, all of them the challenger's.
    """

    challenge: ChallengeResult
    curation: CurationResult
    gate: GateResult
    decided: tuple[Finding, ...] = ()
    unchanged: int = 0
    merged: tuple[Merge, ...] = ()
    retired: tuple[Assumption, ...] = ()
    calls: int = 0

    @property
    def written(self) -> int:
        """Records this pass changed: verdicts, edges and retractions together."""
        return len(self.decided) + len(self.merged) + len(self.retired)


def govern_store(
    repository: Repository,
    provider: LLMProvider | None = None,
    *,
    at: datetime | None = None,
    run_id: str | None = None,
    idle_days: int | None = None,
) -> GovernanceRun:
    """Argue against every finding, curate the store, and route what survives.

    Args:
        repository: An open, migrated store.
        provider: The seam, for the challenger only. **Optional**: without one
            nothing is argued, every finding stays undecided, and the gate
            routes them all to a person. Degrade quality, never correctness.
        at: When this ran. Defaults to now, in UTC. Timezone-aware, invariant 5.
        run_id: Recorded on every audit row this writes.
        idle_days: Override the curator's idle window. Left unset in every
            ordinary call; the CLI exposes it so a person can ask what a
            different window would retire without editing a constant.

    Returns:
        What each stage concluded, and what was written.
    """
    moment = at or datetime.now(UTC)
    standing = repository.list_all(Finding)

    challenge = ChallengerAgent(provider).challenge(standing)
    decided, unchanged = _record(repository, standing, challenge, at=moment, run_id=run_id)

    curator = (
        CuratorAgent(repository)
        if idle_days is None
        else CuratorAgent(repository, idle_days=idle_days)
    )
    curation = curator.curate(at=moment)
    merged = _merge(repository, curation, at=moment, run_id=run_id)
    retired = _retire(repository, curation, at=moment, run_id=run_id)

    # Re-read rather than patch what was read at the top. Two of the three
    # stages above wrote, and the gate's whole job is to report on the store as
    # it now stands -- a routing computed against a stale copy would conclude on
    # assumptions this very pass has just withdrawn.
    gate = AbstentionGate().route_all(
        repository.list_all(Finding), withdrawn=[record.id for record in retired]
    )

    _log.info(
        "governance_pass",
        considered=len(standing),
        decided=len(decided),
        unchanged=unchanged,
        conceded=len(challenge.conceded),
        merged=len(merged),
        retired=len(retired),
        abstained=len(gate.abstained),
        calls=challenge.calls,
    )
    return GovernanceRun(
        challenge=challenge,
        curation=curation,
        gate=gate,
        decided=tuple(decided),
        unchanged=unchanged,
        merged=tuple(merged),
        retired=tuple(retired),
        calls=challenge.calls,
    )


def _record(
    repository: Repository,
    standing: tuple[Finding, ...],
    challenge: ChallengeResult,
    *,
    at: datetime,
    run_id: str | None,
) -> tuple[list[Finding], int]:
    """Write each challenge onto the finding it argued, where it says something new.

    The comparison is on the challenge text and the verdict together. Either
    moving is a real change to what the record claims; neither moving means the
    store already says this, and a new version would be a version recording that
    nothing happened.
    """
    by_id: dict[str, Finding] = {finding.id: finding for finding in standing}
    written: list[Finding] = []
    unchanged = 0
    for result in challenge.challenges:
        finding = by_id[result.finding_id]
        if finding.challenge == result.rebuttal and finding.verdict is result.verdict:
            unchanged += 1
            continue
        written.append(
            repository.revise(
                finding.model_copy(
                    update={"challenge": result.rebuttal, "verdict": result.verdict}
                ),
                actor=GOVERNANCE_ACTOR,
                reason=_verdict_reason(result),
                at=at,
                run_id=run_id,
            )
        )
    return written, unchanged


def _verdict_reason(challenge: Challenge) -> str:
    """What the audit row says about a verdict.

    Names the outcome rather than the agent, because the trail already records
    the actor and the interesting question a reader brings to it is which way
    the argument went.
    """
    if not challenge.decided:
        return f"{challenge.finding_id} was argued and left undecided"
    return f"{challenge.finding_id} was {challenge.verdict.value} after a challenge"


def _merge(
    repository: Repository, curation: CurationResult, *, at: datetime, run_id: str | None
) -> list[Merge]:
    """Write the `supersedes` edge for each merge, newer to older.

    The superseded assumption is **not** retracted alongside it, and that is the
    decision rather than an omission. `supersedes` already says the newer record
    replaces the older, which is the whole claim; retracting as well would put
    the record beyond `list_all` and take the losing side of a revision out of
    every later reading of the store. A person tracing why a decision changed
    needs both halves, and the edge is what tells them which is which.
    """
    written: list[Merge] = []
    for merge in curation.merges:
        repository.add(
            supersedes_link(merge, at=at),
            actor=GOVERNANCE_ACTOR,
            reason=f"{merge.survivor.id} supersedes {merge.superseded.id} ({merge.reason.value})",
            at=at,
            run_id=run_id,
        )
        written.append(merge)
    return written


def _retire(
    repository: Repository, curation: CurationResult, *, at: datetime, run_id: str | None
) -> list[Assumption]:
    """Withdraw each idle assumption, through the store's own retraction.

    A new version carrying a flag plus an `AuditAction.RETRACTED` row. The
    withdrawn versions stay readable, so this is a statement about a record
    rather than the disappearance of one -- invariant 7, and the reason no
    migration was needed for any of this.
    """
    return [
        repository.retract(
            Assumption,
            retirement.assumption.id,
            actor=GOVERNANCE_ACTOR,
            reason=_retirement_reason(retirement),
            at=at,
            run_id=run_id,
        )
        for retirement in curation.retirements
    ]


def _retirement_reason(retirement: Retirement) -> str:
    """Why an assumption was withdrawn, with the number that decided it.

    The idle count travels into the audit trail rather than staying in the
    curator's result, so a reader six months later can tell a retirement made
    under a 60-day window from one made under a 30-day window without knowing
    what the constant was on the day.
    """
    return (
        f"retired by {CURATOR_NAME}: nothing settled {retirement.assumption.id} in "
        f"{retirement.idle_days} days and no live decision rests on it"
    )
