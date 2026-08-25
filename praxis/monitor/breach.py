"""What an `AssumptionBreach` is: a `Finding`, in the Phase 1 store, and nothing new.

`FindingKind.ASSUMPTION_BREACH` has existed since Phase 1 and nothing has ever
written one. This is the module that does, and the point of it being this short
is that there was nothing to invent -- the record, the enum member, the SQL
`CHECK` constraint and the audit trail were all already there. Phase 2 made the
same choice for LLM traces: a new kind of thing goes in the store that exists.

Two decisions.

**One finding per breached assumption, not one per affected decision.**
`ARCHITECTURE.md` says a breach is emitted "against every `Decision` linked by
an `assumes` edge", and the graph already expresses that: the decisions are
reverse-reachable from the assumption over `assumes`, which is what
`Repository.impacted_by` walks. Writing N findings for one fact would make the
count of findings a function of how many decisions happened to cite the
assumption, and a report counting breaches would be counting citations.

**Severity is computed from the decisions, and it is arithmetic.** The worst
`Impact` among the decisions resting on the assumption, promoted to `CRITICAL`
when a high-impact decision also binds the whole organisation. A breach nothing
rests on is `LOW` -- true, and worth recording, and not worth waking anyone for.
No model decides this: a severity that varied between runs would make the
triage queue vary between runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final

from praxis.domain.enums import DecisionScope, FindingKind, Impact, RecordKind, Severity
from praxis.domain.ids import FindingId
from praxis.domain.records import Assumption, Decision, Finding
from praxis.predicates.ast import Evaluation

BREACH_ACTOR: Final = "AssumptionMonitor"
"""Who the audit trail records as having written the finding."""

_SEVERITY_OF: Final[dict[Impact, Severity]] = {
    Impact.LOW: Severity.LOW,
    Impact.MEDIUM: Severity.MEDIUM,
    Impact.HIGH: Severity.HIGH,
}

_IMPACT_ORDER: Final[tuple[Impact, ...]] = (Impact.LOW, Impact.MEDIUM, Impact.HIGH)

_ORGANISATION_WIDE: Final = DecisionScope.ORGANISATION
"""The scope that promotes a high-impact breach to critical."""


def severity_for(decisions: Sequence[Decision]) -> Severity:
    """How urgently a breach of this assumption needs a person.

    Args:
        decisions: The decisions resting on the breached assumption.

    Returns:
        The worst impact among them as a severity, promoted to `CRITICAL` when
        a high-impact decision also binds the whole organisation. `LOW` when
        nothing rests on the assumption at all.
    """
    if not decisions:
        return Severity.LOW
    worst = max(decisions, key=lambda decision: _IMPACT_ORDER.index(decision.impact)).impact
    critical = any(
        decision.impact is Impact.HIGH and decision.scope is _ORGANISATION_WIDE
        for decision in decisions
    )
    return Severity.CRITICAL if critical else _SEVERITY_OF[worst]


def prosecution_for(
    assumption: Assumption, evaluation: Evaluation, decisions: Sequence[Decision]
) -> str:
    """The case against the assumption, stated so it can be argued with.

    Names the predicate, says the verdict was reached by evaluation rather than
    inferred, and lists every decision resting on it -- because the decisions
    are what a person is being asked to re-read, and a finding that made them
    look those up is a finding nobody follows up.

    Args:
        assumption: The record whose predicate evaluated false.
        evaluation: The verdict. Only a violation reaches here.
        decisions: What rests on it.

    Returns:
        One paragraph, in the shape `Finding.prosecution` documents.
    """
    resting = ", ".join(decision.id for decision in decisions) or "no recorded decision"
    return (
        f"The predicate `{assumption.predicate}` evaluated "
        f"{evaluation.truth.value} against the facts this run was given, so the "
        f'assumption "{assumption.statement}" no longer holds. '
        f"Resting on it: {resting}."
    )


def breach_finding(
    assumption: Assumption,
    evaluation: Evaluation,
    decisions: Sequence[Decision],
    *,
    finding_id: str,
    at: datetime,
) -> Finding:
    """Build the finding a violated predicate raises.

    Args:
        assumption: The record whose predicate evaluated false.
        evaluation: The verdict, which must be a violation -- `UNKNOWN` is not
            one, and the caller has already made that distinction.
        decisions: What rests on the assumption, from a reverse walk over
            `assumes`. Decides the severity and is named in the prosecution.
        finding_id: Allocated by the caller, which holds the store.
        at: When the breach was detected. Timezone-aware, invariant 5.

    Returns:
        The finding, at version 1 and undecided -- `ChallengerAgent` is what
        turns a prosecution into a verdict, and it arrives in a later phase. A
        finding written already decided would be claiming a review happened.

    Raises:
        ValueError: if the evaluation is not a violation. An undecided predicate
            raising a breach is the one failure this whole phase is arranged to
            prevent, so it is refused here as well as avoided by the caller.
    """
    if not evaluation.violated:
        message = (
            f"a breach needs a violated predicate, got {evaluation.truth.value} for {assumption.id}"
        )
        raise ValueError(message)
    return Finding(
        id=FindingId(finding_id),
        kind=FindingKind.ASSUMPTION_BREACH,
        subject_kind=RecordKind.ASSUMPTION,
        subject_id=assumption.id,
        prosecution=prosecution_for(assumption, evaluation, decisions),
        severity=severity_for(decisions),
        confidence=assumption.confidence,
        evidence_span_ids=(assumption.span_id,),
        detected_at=at,
        created_by=BREACH_ACTOR,
        created_at=at,
    )
