"""AssumptionMonitor: what a formalized assumption's predicate says today.

**No model output can breach an assumption.** That is the safety property this
module is built around and it is structural, not a convention. Evaluation is
`praxis.predicates.evaluator`, which is arithmetic and never a model, so the only
thing that can produce `BREACHED` is a predicate evaluating false against facts.
The model is asked exactly one question -- does an observation somebody recorded
report the event an `on_event(...)` condition names -- and the answer to that can
only add an observation, which can only make an expiry *fire*, which can only
produce `EXPIRED`. A wrong answer there ages an assumption early. It cannot
accuse a decision of resting on something false.

That is what makes the distinction this phase is graded on -- a genuinely
violated predicate against one that is merely old -- a property of the design
rather than of the prompt.

The four verdicts follow from three-valued evaluation and nothing else:

| Predicate | Expiry fired | Status |
| --- | --- | --- |
| false | either | `BREACHED` -- and a `Finding` |
| true | either | `HOLDING` -- a fresh verdict resets the clock |
| unknown | yes | `EXPIRED` -- the old verdict is stale and no new one is available |
| unknown | no | *unchanged* -- reported, not written |

The last row is the whole of re-runnability. Evaluation is free, so the monitor
always evaluates; **it writes only when the verdict changes.** A second run over
an unchanged world writes no version, no audit row and no duplicate finding,
because the store already holds the last verdict and the store is what is
compared against. No column, no marker file, no second bookkeeping.

An assumption whose predicate does not parse is `UNVERIFIED` with a reason and is
never evaluated, which is `AssumptionFormalizer`'s uncheckable output arriving
here and being handled rather than guessed at.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from praxis.domain.enums import AssumptionStatus
from praxis.domain.records import Assumption
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.predicates.ast import Evaluation, Formula, unknown
from praxis.predicates.errors import ExpiryError, PredicateSyntaxError
from praxis.predicates.evaluator import evaluate, unbound_in
from praxis.predicates.expiry import Expiry, OnEvent, expires, parse_expiry
from praxis.predicates.parser import parse
from praxis.predicates.world import WorldState
from praxis.prompts.library import load

_log = get_logger(__name__)

MONITOR_NAME: Final = "AssumptionMonitor"
"""Spelled as ADR 0006's routing table spells it."""

MATCH_TASK: Final = "match_event"
"""The one thing this agent asks a model, and the name of the prompt file."""

MAX_MATCHED_EVENTS: Final = 40
"""How many awaited events one matching call may carry.

A cap rather than a page size: beyond this the listing stops being something an
ordinal can address unambiguously, which is the same argument ADR 0015 makes
about a span offering. A run with more awaited events makes more calls.
"""


@dataclass(frozen=True, slots=True)
class Verdict:
    """What one assumption's predicate says now, and whether that is news.

    Attributes:
        assumption: The record as it was read.
        status: What it should be. `assumption.status` is what it was.
        evaluation: The predicate's own three-valued verdict.
        expiry: Whether the expiry condition has fired. Undecided when the
            condition itself measures something nobody has measured.
        reason: Why the status is what it is, in one sentence. Becomes the
            audit row's reason when the verdict is written.
    """

    assumption: Assumption
    status: AssumptionStatus
    evaluation: Evaluation
    expiry: Evaluation
    reason: str

    @property
    def changed(self) -> bool:
        """Whether this is a different verdict from the one already stored.

        The whole of re-runnability. A second run over an unchanged world
        answers `False` here for every assumption and therefore writes nothing.
        """
        return self.status is not self.assumption.status

    @property
    def breached(self) -> bool:
        """Whether the predicate was found false. Only this raises a finding."""
        return self.status is AssumptionStatus.BREACHED

    @property
    def aged(self) -> bool:
        """Whether the assumption merely expired.

        Named apart from `breached` because telling these two apart is the
        thing this phase is graded on, and a caller that had to compare enum
        members would eventually compare the wrong ones.
        """
        return self.status is AssumptionStatus.EXPIRED


class EventMatch(BaseModel):
    """One awaited event and the observation a model says reports it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    awaited_ordinal: int | None
    observed_ordinal: int | None
    confidence: float = Field(ge=0.0, le=1.0)


class EventMatches(BaseModel):
    """Everything one matching call says."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: tuple[EventMatch, ...]


class AssumptionMonitor:
    """Evaluates formalized assumptions and says which have been violated."""

    name: Final = MONITOR_NAME

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        max_attempts: int = REPAIR_ATTEMPTS,
        min_match_confidence: float = 0.6,
    ) -> None:
        """Wire the agent to a provider it did not choose, or to none at all.

        Args:
            provider: The seam, from `provider_for`. **Optional**, and that is
                the design rather than convenience: without one the monitor
                still evaluates every predicate and still raises every breach,
                and only the fuzzy half of event matching is lost. Degrade
                quality, never correctness.
            max_attempts: Attempts per call, repairs included.
            min_match_confidence: Below this a claimed event match is ignored.
                An event match can only age an assumption, so the cost of being
                strict is a re-check that happens later than it might have.

        Raises:
            ValueError: if the threshold is not a probability.
        """
        if not 0.0 <= min_match_confidence <= 1.0:
            message = f"a confidence threshold is between 0 and 1, got {min_match_confidence}"
            raise ValueError(message)
        self._provider = provider
        self._max_attempts = max_attempts
        self._min_match_confidence = min_match_confidence

    def check(self, assumption: Assumption, world: WorldState) -> Verdict:
        """Decide what one assumption's predicate says against a world.

        Deterministic and free: no model call happens here. `enrich` is what
        makes the one call this agent makes, once per run rather than once per
        assumption, and the caller does it before checking anything.

        Args:
            assumption: The record, as the store holds it.
            world: The facts, events and clock, from `praxis.monitor.facts`.

        Returns:
            The verdict, and whether it differs from the stored status.
        """
        compiled = _compile(assumption)
        if isinstance(compiled, str):
            return _unverified(assumption, compiled)
        predicate, expiry = compiled
        evaluation = evaluate(predicate, world)
        fired = expires(expiry, world)
        return _verdict(assumption, evaluation, fired, world)

    def enrich(
        self, world: WorldState, assumptions: Sequence[Assumption]
    ) -> tuple[WorldState, int]:
        """Recognise which recorded observations report the events being awaited.

        The only model call this agent makes, and it is made once for a whole
        run rather than once per assumption -- the question is about the two
        lists, not about any single record.

        A recognised match is added to the world as an ordinary observation, so
        everything downstream stays deterministic and nothing has to know a
        model was involved. **The result can only make an expiry fire.** It
        cannot bind a fact, so it cannot move a predicate off `UNKNOWN`, so it
        cannot produce a breach.

        Args:
            world: The world so far.
            assumptions: The records about to be checked, for their awaited
                events.

        Returns:
            The world with any recognised events observed, and the number of
            model calls made.

        Raises:
            ProviderError: for failures about the run rather than this batch.
        """
        awaited = _awaited_events(assumptions, world)
        observed = tuple(world.events)
        if self._provider is None or not awaited or not observed:
            return world, 0
        matched, calls = self._matched(awaited[:MAX_MATCHED_EVENTS], observed)
        return world.with_events(*matched), calls

    def _matched(
        self, awaited: Sequence[str], observed: Sequence[str]
    ) -> tuple[tuple[str, ...], int]:
        """Ask which observations report which awaited events."""
        if self._provider is None:  # pragma: no cover -- `enrich` has checked
            return (), 0
        prompt = load(MATCH_TASK)
        request = LLMRequest(
            agent=self.name,
            task=MATCH_TASK,
            system=prompt.render(),
            messages=(Message(role=MessageRole.USER, content=_listing(awaited, observed)),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"awaited": str(len(awaited)), "observed": str(len(observed))},
        )
        try:
            result = ask_for(self._provider, request, EventMatches, max_attempts=self._max_attempts)
        except (MalformedOutputError, ProviderRefusalError) as exc:
            # A bad answer about this batch is not a broken run, and the cost of
            # losing it is that some assumptions are re-checked later than they
            # could have been. Nothing becomes wrong.
            _log.warning("event_matching_failed", agent=self.name, reason=type(exc).__name__)
            return (), exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return self._accepted(result.value, awaited, observed), result.attempts

    def _accepted(
        self, answer: EventMatches, awaited: Sequence[str], observed: Sequence[str]
    ) -> tuple[str, ...]:
        """The awaited events a model matched well enough to act on.

        The *awaited* wording is what is added to the world, not the observed
        wording, because `WorldState.observed` compares an `on_event(...)`
        condition against what the world holds -- and the condition is spelled
        the awaited way.
        """
        accepted: list[str] = []
        for match in answer.matches:
            if match.confidence < self._min_match_confidence:
                continue
            if not _addresses(match.awaited_ordinal, awaited):
                continue
            if not _addresses(match.observed_ordinal, observed):
                continue
            accepted.append(awaited[match.awaited_ordinal or 0])
        return tuple(dict.fromkeys(accepted))


def _compile(assumption: Assumption) -> tuple[Formula, Expiry] | str:
    """Parse both halves, or say in one sentence why they cannot be."""
    try:
        predicate = parse(assumption.predicate)
    except PredicateSyntaxError as exc:
        return f"its predicate does not parse: {exc.problem}"
    try:
        expiry = parse_expiry(assumption.expiry_condition)
    except (ExpiryError, PredicateSyntaxError) as exc:
        return f"its expiry condition does not parse: {exc}"
    return predicate, expiry


def _verdict(
    assumption: Assumption, evaluation: Evaluation, fired: Evaluation, world: WorldState
) -> Verdict:
    """Turn a three-valued evaluation into one of the four statuses."""
    if evaluation.violated:
        return Verdict(
            assumption=assumption,
            status=AssumptionStatus.BREACHED,
            evaluation=evaluation,
            expiry=fired,
            reason=f"the predicate `{assumption.predicate}` evaluated false",
        )
    if evaluation.holds:
        return Verdict(
            assumption=assumption,
            status=AssumptionStatus.HOLDING,
            evaluation=evaluation,
            expiry=fired,
            reason=f"the predicate `{assumption.predicate}` evaluated true",
        )
    if fired.holds:
        return Verdict(
            assumption=assumption,
            status=AssumptionStatus.EXPIRED,
            evaluation=evaluation,
            expiry=fired,
            reason=(
                f"the expiry condition `{assumption.expiry_condition}` has fired and "
                f"the predicate cannot be re-checked: {evaluation.reason}"
            ),
        )
    return Verdict(
        assumption=assumption,
        status=assumption.status,
        evaluation=evaluation,
        expiry=fired,
        reason=_still_unchecked(assumption, evaluation, world),
    )


def _still_unchecked(assumption: Assumption, evaluation: Evaluation, world: WorldState) -> str:
    """Why nothing changed, naming the quantities somebody would have to measure."""
    try:
        missing = sorted(unbound_in(parse(assumption.predicate), world))
    except PredicateSyntaxError:  # pragma: no cover -- `_compile` has parsed it
        missing = []
    if missing:
        return f"still unchecked: nothing has measured {', '.join(missing)}"
    return f"still unchecked: {evaluation.reason}"


def _unverified(assumption: Assumption, problem: str) -> Verdict:
    """The verdict for an assumption that was never checkable.

    Its status stays whatever it was, which for an assumption the formalizer
    could not compile is `UNVERIFIED`. Deliberately not `BREACHED`: an
    assumption nobody can evaluate has not been violated.
    """
    reason = f"not evaluated: {problem}"
    undecided = unknown(problem)
    return Verdict(
        assumption=assumption,
        status=assumption.status,
        evaluation=undecided,
        expiry=undecided,
        reason=reason,
    )


def _awaited_events(assumptions: Sequence[Assumption], world: WorldState) -> tuple[str, ...]:
    """The `on_event` descriptions being waited for that are not already observed.

    Ones the world already reports are excluded, so a model is never asked
    about a question deterministic matching has already answered -- which keeps
    the call's size a function of what is genuinely ambiguous.
    """
    awaited: list[str] = []
    for assumption in assumptions:
        try:
            expiry = parse_expiry(assumption.expiry_condition)
        except (ExpiryError, PredicateSyntaxError):
            continue
        if isinstance(expiry, OnEvent) and not world.observed(expiry.description):
            awaited.append(expiry.description)
    return tuple(dict.fromkeys(awaited))


def _listing(awaited: Sequence[str], observed: Sequence[str]) -> str:
    """The two numbered lists a matching call is shown."""
    return "\n".join(
        [
            "Awaited:",
            *(f"[{index}] {text}" for index, text in enumerate(awaited)),
            "",
            "Observed:",
            *(f"[{index}] {text}" for index, text in enumerate(observed)),
        ]
    )


def _addresses(ordinal: int | None, listing: Sequence[str]) -> bool:
    """Whether an ordinal names an entry that was really offered."""
    return ordinal is not None and 0 <= ordinal < len(listing)
