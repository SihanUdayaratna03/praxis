"""ChallengerAgent: the case against a finding, before a person ever sees it.

`ARCHITECTURE.md` has said since Phase 0 that no high-severity finding reaches a
human without surviving this agent, and Phase 1 built the record for it:
`Finding` already carries `prosecution`, `challenge` and `verdict`, and
`Verdict`'s own docstring reads *"What survived `ChallengerAgent`"*. So this
adds no storage. It fills in fields that have been waiting eight phases.

**This is the first component in seven to make a model call, and that was
called in advance.** Phases 7 and 8 moved six consecutive components to the
deterministic side, and ADR 0027 -- the last of them -- named where the run
would stop: *"`ChallengerAgent` and `ReviewTriageAgent`, whose input is prose
nobody has reduced to a record, should keep their routes."* A
`Finding.prosecution` is prose. Whether a case is *sound* is not a fact any
table in this store holds, and there is no arithmetic that decides it, so this
keeps ADR 0006's `reason` route. See ADR 0030.

**ADR 0019 is untouched by that.** Only arithmetic may reach `BREACHED`, and
nothing here writes an `AssumptionStatus` at all -- the challenger writes
`Verdict`, which is a different field answering a different question. The
allegation was reached by evaluation; whether the allegation *holds up* is the
judgement, and it lands beside the allegation rather than replacing it.

## Refusing is most of what it does

A challenger that never concedes is a rubber stamp and one that always concedes
is a silencer, so every path that could quietly produce one of those is closed:

- **No provider at all** -- nothing is challenged, and the findings keep the
  `UNDECIDED` verdict they already had. Degrade quality, never correctness.
- **A finding that already carries a verdict** is not re-argued. Re-running this
  over a settled store costs nothing and cannot flip a recorded decision.
- **A malformed batch** is reported as a gap. The findings in it are simply not
  argued, which is a different fact from a batch of concessions nobody made.
- **A verdict below the confidence floor records no verdict.** The finding stays
  `UNDECIDED` and reaches `AbstentionGate` as exactly what it is -- something
  nobody was willing to decide -- rather than being pushed to whichever side
  looked safer.

The last of those is why `Challenge.decided` exists rather than a bare bool: a
challenge that produced prose and no verdict is a real outcome and the prose is
worth keeping, because it is the objection a person should read before deciding
for themselves.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from praxis.domain.enums import Verdict
from praxis.domain.records import Finding
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.prompts.library import load

_log = get_logger(__name__)

CHALLENGER_NAME: Final = "ChallengerAgent"
"""Spelled as ADR 0006's routing table spells it."""

CHALLENGE_TASK: Final = "challenge_finding"
"""The prompt file this agent reads -- ADR 0014."""

DEFAULT_BATCH: Final = 8
"""Findings per challenging call.

Smaller than `ContradictionDetector`'s twelve, and for a reason that is about
the input rather than about addressing: a prosecution is a paragraph, where a
contradiction candidate is two sentences. Eight paragraphs is already a long
listing to hold one argument against each of.
"""

DEFAULT_MIN_CONFIDENCE: Final = 0.6
"""Below this a verdict is not recorded at all.

Symmetric, unlike `ContradictionDetector`'s threshold, and deliberately so. That
agent's floor is asymmetric because a false contradiction costs more than a
missed one. Here both errors are expensive in the same way -- a wrongly upheld
finding wastes a person's attention, a wrongly overturned one buries something
real -- so an unconfident verdict is recorded as no verdict rather than nudged
toward either side.
"""

_NOTHING_ARGUED: Final = "The challenge produced no argument, so nothing was tested."
"""What a challenge says when the model returned an empty rebuttal.

`Finding.challenge` is a `NonEmptyStr`, so this is what stands in the record's
way of being unwritable. It is worded as an admission rather than as a
rebuttal, because a person reading it should treat the verdict beside it as
unsupported -- which is why an empty rebuttal also forfeits the verdict below.
"""


class Judgement(BaseModel):
    """What one call says about one finding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_ordinal: int | None
    rebuttal: str | None
    upheld: bool
    """Spelled as `Verdict.UPHELD` spells it.

    The polarity mirrors the enum this answer is written into rather than being
    chosen for how it reads in a prompt. That is worth a sentence because it has
    a measurable consequence offline: `MockProvider` synthesises a bare boolean
    true seventy per cent of the time (`praxis.llm.synthesis._TRUE_BIAS`), so the
    concede rate against the mock is a property of schema synthesis and not of
    any reasoning. `docs/reports/phase-9.md` reports it as such.
    """
    confidence: float = Field(ge=0.0, le=1.0)


class Judgements(BaseModel):
    """Everything one challenging call says."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judgements: tuple[Judgement, ...]


@dataclass(frozen=True, slots=True)
class Challenge:
    """One finding argued against, and what the argument concluded.

    Attributes:
        finding_id: What was challenged.
        rebuttal: The case against the finding. Never empty -- a challenge with
            no argument records `_NOTHING_ARGUED` instead, because
            `Finding.challenge` refuses an empty string and losing the whole
            challenge over a missing sentence would lose the verdict with it.
        verdict: `UPHELD`, `OVERTURNED`, or `UNDECIDED` when the model was not
            confident enough to be worth recording.
        confidence: How sure the verdict was.
    """

    finding_id: str
    rebuttal: str
    verdict: Verdict
    confidence: float

    @property
    def decided(self) -> bool:
        """Whether a verdict was actually reached.

        An undecided challenge still carries prose worth reading, so this is not
        the same question as whether the challenge exists.
        """
        return self.verdict is not Verdict.UNDECIDED

    @property
    def conceded(self) -> bool:
        """Whether the challenger defeated the finding it was arguing against."""
        return self.verdict is Verdict.OVERTURNED


@dataclass(frozen=True, slots=True)
class ChallengeResult:
    """What one challenging pass concluded.

    Attributes:
        challenges: One per finding argued, in read order.
        skipped: Findings not argued because a verdict already stood.
        unjudged: Findings in batches whose answer was unusable. Counted apart
            from `skipped`, because "we chose not to ask" and "we asked and
            could not use the answer" are different failures with different
            fixes.
        calls: Model calls made, repair attempts included.
    """

    challenges: tuple[Challenge, ...] = ()
    skipped: int = 0
    unjudged: int = 0
    calls: int = 0

    @property
    def decided(self) -> tuple[Challenge, ...]:
        """Challenges that reached a verdict."""
        return tuple(challenge for challenge in self.challenges if challenge.decided)

    @property
    def conceded(self) -> tuple[Challenge, ...]:
        """Challenges that defeated the finding."""
        return tuple(challenge for challenge in self.challenges if challenge.conceded)

    @property
    def concede_rate(self) -> float:
        """Share of *decided* challenges that overturned the finding.

        The denominator is deliberately the decided ones and not every finding
        seen. Counting undecided challenges as non-concessions would let a
        challenger that never commits to anything read as a challenger that
        never concedes, and those are opposite defects.

        Zero when nothing was decided, which is not a low concede rate -- it is
        no measurement. `praxis.eval.governance` reports the denominator beside
        the rate so the two cannot be confused.
        """
        decided = len(self.decided)
        return len(self.conceded) / decided if decided else 0.0


class ChallengerAgent:
    """Argues against findings, and records what survived the argument."""

    name: Final = CHALLENGER_NAME

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        batch: int = DEFAULT_BATCH,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        max_attempts: int = REPAIR_ATTEMPTS,
    ) -> None:
        """Wire the agent to a provider it did not choose, or to none at all.

        Args:
            provider: The seam, from `provider_for`. **Optional**: without one
                nothing is challenged and every finding keeps the undecided
                verdict it already had, which `AbstentionGate` then routes to a
                person. Degrade quality, never correctness.
            batch: Findings per call.
            min_confidence: Below this a verdict is not recorded.
            max_attempts: Attempts per call, repairs included.

        Raises:
            ValueError: if either bound is outside what it means.
        """
        if batch < 1:
            message = f"a batch holds at least one finding, got {batch}"
            raise ValueError(message)
        if not 0.0 <= min_confidence <= 1.0:
            message = f"a confidence threshold is between 0 and 1, got {min_confidence}"
            raise ValueError(message)
        self._provider = provider
        self._batch = batch
        self._min_confidence = min_confidence
        self._max_attempts = max_attempts

    def challenge(self, findings: Sequence[Finding]) -> ChallengeResult:
        """Argue against every finding that has not already been decided.

        Args:
            findings: Findings to test, in any order.

        Returns:
            One challenge per finding argued, and what was skipped or lost.

        Raises:
            ProviderError: for failures about the run rather than one batch.
        """
        pending = [finding for finding in findings if finding.verdict is Verdict.UNDECIDED]
        skipped = len(findings) - len(pending)
        if self._provider is None or not pending:
            return ChallengeResult(skipped=skipped)

        challenges: list[Challenge] = []
        unjudged = 0
        calls = 0
        for start in range(0, len(pending), self._batch):
            batch = pending[start : start + self._batch]
            answer, spent = self._ask(batch)
            calls += spent
            if answer is None:
                unjudged += len(batch)
                continue
            found = self._accepted(answer, batch)
            challenges.extend(found)
            # A batch that answered about only some of its findings leaves the
            # rest unargued, and that is the same gap a malformed batch leaves.
            unjudged += len(batch) - len(found)

        _log.info(
            "challenge_pass",
            agent=self.name,
            challenged=len(challenges),
            conceded=sum(1 for challenge in challenges if challenge.conceded),
            skipped=skipped,
            unjudged=unjudged,
            calls=calls,
        )
        return ChallengeResult(
            challenges=tuple(challenges),
            skipped=skipped,
            unjudged=unjudged,
            calls=calls,
        )

    def _ask(self, batch: Sequence[Finding]) -> tuple[Judgements | None, int]:
        """One challenging call. `None` means the answer was unusable."""
        if self._provider is None:  # pragma: no cover -- `challenge` has checked
            return None, 0
        prompt = load(CHALLENGE_TASK)
        request = LLMRequest(
            agent=self.name,
            task=CHALLENGE_TASK,
            system=prompt.render(),
            messages=(Message(role=MessageRole.USER, content=listing(batch)),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"findings": str(len(batch))},
        )
        try:
            result = ask_for(self._provider, request, Judgements, max_attempts=self._max_attempts)
        except (MalformedOutputError, ProviderRefusalError) as exc:
            # A bad answer about this batch is not a broken run. These findings
            # are simply not argued, which is reported as a gap rather than as
            # verdicts nobody reached.
            _log.warning(
                "challenge_failed",
                agent=self.name,
                findings=len(batch),
                reason=type(exc).__name__,
            )
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts

    def _accepted(self, answer: Judgements, batch: Sequence[Finding]) -> list[Challenge]:
        """The judgements this agent is willing to record.

        A mis-addressed or repeated ordinal is dropped rather than reassigned:
        recording a verdict against the wrong finding is worse than recording
        none, because the finding it lands on looks reviewed.
        """
        found: list[Challenge] = []
        seen: set[int] = set()
        for judgement in answer.judgements:
            ordinal = judgement.finding_ordinal
            if ordinal is None or not 0 <= ordinal < len(batch) or ordinal in seen:
                continue
            seen.add(ordinal)
            found.append(_challenge(batch[ordinal], judgement, self._min_confidence))
        return found


def _challenge(finding: Finding, judgement: Judgement, floor: float) -> Challenge:
    """One judgement as a challenge, with the confidence floor applied.

    An empty rebuttal forfeits the verdict as well as the prose. The two are not
    separable: `Verdict` means "what survived `ChallengerAgent`", and a verdict
    with nothing behind it is a claim that an argument happened when there is no
    evidence it did -- which is the same thing `Finding`'s own validator refuses.
    """
    rebuttal = (judgement.rebuttal or "").strip()
    confident = judgement.confidence >= floor and bool(rebuttal)
    verdict = Verdict.UNDECIDED
    if confident:
        verdict = Verdict.UPHELD if judgement.upheld else Verdict.OVERTURNED
    return Challenge(
        finding_id=finding.id,
        rebuttal=rebuttal or _NOTHING_ARGUED,
        verdict=verdict,
        confidence=judgement.confidence,
    )


def listing(batch: Sequence[Finding]) -> str:
    """The numbered findings one challenging call is shown.

    Public because the CLI's dry run renders the same listing without making a
    call, and two spellings of "what the model was shown" would eventually
    disagree.
    """
    lines: list[str] = []
    for ordinal, finding in enumerate(batch):
        lines.append(f"[{ordinal}] {finding.kind.value} against {finding.subject_id}")
        lines.append(f"  severity: {finding.severity.value}")
        lines.append(f"  the case: {finding.prosecution}")
        lines.append("")
    return "\n".join(lines)
