"""AbstentionGate: refusing to conclude, and saying which evidence was missing.

The last thing between a finding and the person it will interrupt, sitting
immediately downstream of `ChallengerAgent`. The challenger asks whether the
*argument* holds. This asks whether the *evidence* does, and those are different
questions with different failure modes: a finding can survive a rigorous
challenge and still rest on a confidence of 0.3, a span nobody can read, or a
subject that has since been withdrawn.

**Refusing is the output, not a guard clause.** Every other component in this
system reports its refusals beside its results; this one is made entirely of
them. A `NEEDS_HUMAN` disposition is the product working, and the number is
reported plainly in `docs/reports/phase-9.md` rather than treated as a shortfall
to drive toward zero — the stance this project has taken on every refusal since
`BiasDetective` declined to speak below `n = 5`.

## It makes no model call, and this is the easiest of the nine to argue

Its entire input is a confidence between zero and one, a count of evidence
spans, a `Verdict` enum, a `FindingKind` enum and a boolean saying whether the
subject was retracted. Asking a model whether 0.4 is below 0.5 is precisely what
invariant 3 forbids, and there is no prose anywhere in the decision. Ninth
consecutive component on the arithmetic side. See ADR 0032.

## Nothing is stored, and that is what keeps this phase migration-free

A routing decision is **recomputed every time it is asked for**, exactly as
`praxis.agents.calibration` argues a calibration factor must be. The reasoning
transfers without modification: a stored disposition is stale the moment the
evidence under it moves — a challenge lands, a subject is retracted, a
confidence is revised — and a stale refusal is worse than no refusal, because it
reads as a decision somebody made. Storing it would also cost a migration, since
a `needs_human` column and a new `FindingKind` member are both schema changes
mirrored by SQL `CHECK` constraints.

## Where the three rules come from

Each one is a specific way a finding can be well-argued and still not worth
acting on, and each is checkable without reading a word of prose.

1. **Confidence below the floor.** The producing component's own stated
   uncertainty. A finding nobody was confident in is not made confident by
   surviving a challenge.
2. **A quoting kind that quotes nothing.** `Finding.evidence_span_ids` is
   documented as legitimately empty for a *computed* finding — a calibration
   factor is not something a document says. It is not legitimately empty for one
   that alleges a document says something, so the rule applies per kind rather
   than universally.
3. **A withdrawn subject.** An allegation about a record somebody has retracted
   is an allegation about a claim that no longer stands. `Repository.get`
   returns a retracted record rather than `None`, which is why this is a check
   and not an absence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from praxis.domain.enums import FindingKind, Verdict
from praxis.domain.records import Finding
from praxis.obs.logging import get_logger

_log = get_logger(__name__)

GATE_NAME: Final = "AbstentionGate"
"""Spelled as `praxis.config.models.NON_LLM_AGENTS` spells it, so the set that
refuses to route this agent and the agent itself cannot drift apart."""

DEFAULT_CONFIDENCE_FLOOR: Final = 0.5

QUOTING_KINDS: Final[frozenset[FindingKind]] = frozenset(
    {FindingKind.ASSUMPTION_BREACH, FindingKind.CONTRADICTION}
)
"""Kinds whose allegation is about what a document says, so a citation is owed.

The complement is deliberate rather than an omission. `CALIBRATION_BIAS`,
`COLLATERAL_IMPACT` and `STALE_DECISION` are all computed from accumulated
history and a factor, and `Finding.evidence_span_ids` says in its own docstring
that it may legitimately be empty for exactly that reason. Requiring a citation
from them would abstain on every fusion finding the product exists to produce.
"""


class Disposition(StrEnum):
    """What should happen to a finding that has been through the pipeline."""

    EMIT = "emit"
    """Argued, survived, and evidenced. It reaches a person as a conclusion."""

    NEEDS_HUMAN = "needs_human"
    """Not concluded. Something about the evidence is insufficient, and the
    person is being asked rather than told."""

    DROPPED = "dropped"
    """The challenger defeated it. Nothing to emit and nothing to ask about."""


class Insufficiency(StrEnum):
    """Why a conclusion was withheld. One per rule, so a report can group them."""

    NEVER_CHALLENGED = "never_challenged"
    """No verdict was reached. Either nothing argued it, or the argument was
    too close to record -- and `ARCHITECTURE.md` has said since Phase 0 that
    nothing reaches a human without surviving the challenger."""

    LOW_CONFIDENCE = "low_confidence"
    """The component that filed it was not confident, and surviving a challenge
    does not make it so."""

    UNCITED = "uncited"
    """It alleges a document says something and cites no span that says it."""

    SUBJECT_WITHDRAWN = "subject_withdrawn"
    """The record it is about has been retracted, so the claim it attacks no
    longer stands."""


@dataclass(frozen=True, slots=True)
class Routing:
    """One finding's disposition, and why.

    Attributes:
        finding_id: What was routed.
        disposition: Where it goes.
        insufficiencies: Every rule that failed, in rule order and never just
            the first. A finding that is both uncited and about a withdrawn
            subject has two problems, and a person told about one of them fixes
            it and is sent straight back.
        reason: The whole of it in a reader's words.
    """

    finding_id: str
    disposition: Disposition
    insufficiencies: tuple[Insufficiency, ...]
    reason: str

    @property
    def abstained(self) -> bool:
        """Whether a conclusion was withheld."""
        return self.disposition is Disposition.NEEDS_HUMAN


@dataclass(frozen=True, slots=True)
class GateResult:
    """What one routing pass concluded over a set of findings.

    Attributes:
        routings: One per finding, in read order.
    """

    routings: tuple[Routing, ...] = ()

    @property
    def emitted(self) -> tuple[Routing, ...]:
        """Findings that reach a person as conclusions."""
        return tuple(
            routing for routing in self.routings if routing.disposition is Disposition.EMIT
        )

    @property
    def abstained(self) -> tuple[Routing, ...]:
        """Findings routed to a person as questions."""
        return tuple(routing for routing in self.routings if routing.abstained)

    @property
    def dropped(self) -> tuple[Routing, ...]:
        """Findings the challenger defeated."""
        return tuple(
            routing for routing in self.routings if routing.disposition is Disposition.DROPPED
        )

    @property
    def considered(self) -> int:
        """The denominator of the abstention rate.

        Emitted plus abstained, and **not** every finding seen. A dropped
        finding was never a candidate for emission -- the challenger already
        decided it -- so counting it would make the abstention rate fall
        whenever the challenger conceded more, which is a different number
        moving the wrong one.
        """
        return len(self.emitted) + len(self.abstained)

    @property
    def abstention_rate(self) -> float:
        """Share of emittable findings on which a conclusion was withheld.

        Zero when nothing was considered, which is no measurement rather than a
        confident pipeline. `praxis.eval.governance` reports the denominator
        beside it, as it does for the other two rates.
        """
        return len(self.abstained) / self.considered if self.considered else 0.0

    def by_insufficiency(self) -> dict[Insufficiency, int]:
        """How many abstentions each rule caused, counted once per occurrence.

        A finding failing two rules contributes to both, so these sum to at
        least the abstention count and usually to more. That is the useful
        shape: "how often does this rule fire" is the question a person tuning
        the pipeline is asking, not "which single rule was blamed".
        """
        counted = dict.fromkeys(Insufficiency, 0)
        for routing in self.abstained:
            for insufficiency in routing.insufficiencies:
                counted[insufficiency] += 1
        return counted


class AbstentionGate:
    """Decides whether a finding is concluded, questioned, or dropped."""

    name: Final = GATE_NAME

    def __init__(self, *, confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR) -> None:
        """Set the one threshold this gate carries.

        Args:
            confidence_floor: Below this a finding is routed to a person rather
                than concluded.

        Raises:
            ValueError: if the floor is not a confidence.
        """
        if not 0.0 <= confidence_floor <= 1.0:
            message = f"a confidence floor is between 0 and 1, got {confidence_floor}"
            raise ValueError(message)
        self._floor = confidence_floor

    def route(self, finding: Finding, *, subject_withdrawn: bool = False) -> Routing:
        """Decide one finding's disposition.

        A pure function of its arguments. Nothing is written, nothing is read
        from a store, and calling it twice cannot disagree with itself.

        Args:
            finding: The finding, in whatever state the pipeline left it.
            subject_withdrawn: Whether the record it alleges something about has
                been retracted. Passed in rather than looked up, because this
                agent holds no store -- `route_all` is where a caller with one
                joins the two.

        Returns:
            Where it goes and why.
        """
        if finding.verdict is Verdict.OVERTURNED:
            return Routing(
                finding.id,
                Disposition.DROPPED,
                (),
                f"{finding.id} was overturned: {finding.challenge}",
            )
        insufficiencies = self._insufficiencies(finding, subject_withdrawn=subject_withdrawn)
        if not insufficiencies:
            return Routing(
                finding.id,
                Disposition.EMIT,
                (),
                f"{finding.id} survived a challenge and cites what it alleges",
            )
        return Routing(
            finding.id,
            Disposition.NEEDS_HUMAN,
            insufficiencies,
            _reason_for(finding, insufficiencies, self._floor),
        )

    def route_all(
        self, findings: Sequence[Finding], *, withdrawn: Sequence[str] = ()
    ) -> GateResult:
        """Decide a whole set, in read order.

        Args:
            findings: What to route.
            withdrawn: Subject ids known to have been retracted. A sequence
                rather than a per-finding lookup so a caller holding a store
                resolves them once.

        Returns:
            Every routing, and the rates over them.
        """
        retracted = frozenset(withdrawn)
        routings = tuple(
            self.route(finding, subject_withdrawn=finding.subject_id in retracted)
            for finding in findings
        )
        result = GateResult(routings=routings)
        _log.info(
            "abstention_pass",
            agent=self.name,
            considered=result.considered,
            emitted=len(result.emitted),
            abstained=len(result.abstained),
            dropped=len(result.dropped),
        )
        return result

    def _insufficiencies(
        self, finding: Finding, *, subject_withdrawn: bool
    ) -> tuple[Insufficiency, ...]:
        """Every rule this finding fails, in rule order.

        All four are evaluated rather than short-circuited. A person told only
        the first problem fixes it and is sent straight back, which turns one
        round trip into as many as the finding has defects.
        """
        failed: list[Insufficiency] = []
        if finding.verdict is not Verdict.UPHELD:
            failed.append(Insufficiency.NEVER_CHALLENGED)
        if finding.confidence < self._floor:
            failed.append(Insufficiency.LOW_CONFIDENCE)
        if finding.kind in QUOTING_KINDS and not finding.evidence_span_ids:
            failed.append(Insufficiency.UNCITED)
        if subject_withdrawn:
            failed.append(Insufficiency.SUBJECT_WITHDRAWN)
        return tuple(failed)


def _reason_for(finding: Finding, insufficiencies: Sequence[Insufficiency], floor: float) -> str:
    """Why a conclusion was withheld, in a reader's words rather than the enum's.

    One clause per failed rule, joined. A person reading this is being asked a
    question, so the sentence has to say what would answer it.
    """
    clauses = {
        Insufficiency.NEVER_CHALLENGED: (
            "no verdict was reached, so nothing has tested the case against it"
        ),
        Insufficiency.LOW_CONFIDENCE: (
            f"the component that filed it was {finding.confidence} confident, "
            f"under the {floor} this concludes at"
        ),
        Insufficiency.UNCITED: (
            f"a {finding.kind.value} alleges a document says something and this one cites no span"
        ),
        Insufficiency.SUBJECT_WITHDRAWN: (
            f"{finding.subject_id} has been retracted, so the claim it attacks no longer stands"
        ),
    }
    joined = "; ".join(clauses[insufficiency] for insufficiency in insufficiencies)
    return f"{finding.id} is not concluded: {joined}. Decide it yourself"
