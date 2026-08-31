"""ChallengerAgent: what survives an argument, and what nobody was willing to decide.

Three claims run through this file, and the third is the one worth reading.

The first is that **a challenge is recorded or it is not, never half of it**.
`Finding`'s own validator refuses a verdict with no challenge behind it, so
every path that produces a verdict here has to produce the prose that reached
it -- including the paths where the model answered badly. The tests exercise
those paths rather than the happy one.

The second is that **an unconfident verdict is no verdict**. This is the
refusal a challenger most needs and is easiest to omit, because omitting it
looks like decisiveness. `TestWhatIsNotRecorded` is the longer class for the
same reason `test_contradiction.py`'s negative class is.

The third is that **every refusal test carries a control**. Phase 7's lesson,
and it is not decoration: a test asserting that a low-confidence verdict is
dropped passes trivially against an agent that drops everything, so each one is
paired with the same call above the floor, which must be recorded.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from praxis.agents.challenger import (
    CHALLENGE_TASK,
    CHALLENGER_NAME,
    DEFAULT_MIN_CONFIDENCE,
    ChallengerAgent,
    listing,
)
from praxis.config.models import NON_LLM_AGENTS, ModelRole, role_for_agent, routed_agents
from praxis.domain.enums import FindingKind, RecordKind, Severity, Verdict
from praxis.domain.records import Finding
from praxis.llm.errors import MalformedOutputError

from tests.agents.conftest import Answering, Refusing

AT = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def make_finding(
    number: int = 1,
    *,
    kind: FindingKind = FindingKind.ASSUMPTION_BREACH,
    verdict: Verdict = Verdict.UNDECIDED,
    challenge: str | None = None,
    severity: Severity = Severity.MEDIUM,
) -> Finding:
    """A finding in the state the store hands one to this agent.

    Undecided by default, because that is what every producer in this system
    writes: `praxis.monitor.breach`, `praxis.agents.calibration` and
    `praxis.agents.fusion_pass` all build findings at version 1 with no verdict
    and each one's docstring says the challenger is what changes that.
    """
    return Finding(
        id=f"F-{number:04d}",
        kind=kind,
        subject_kind=RecordKind.ASSUMPTION,
        subject_id=f"A-{number:04d}",
        prosecution=(
            f"A-{number:04d} assumed the index stays under 50 GB; it is measured at 61 GB."
        ),
        challenge=challenge,
        verdict=verdict,
        severity=severity,
        confidence=0.8,
        detected_at=AT,
        created_by="AssumptionMonitor",
        created_at=AT,
    )


def judgements(
    *entries: tuple[int, bool], confidence: float = 0.9, rebuttal: str = "tested"
) -> str:
    """A challenging answer as the structured layer would receive it."""
    return json.dumps(
        {
            "judgements": [
                {
                    "finding_ordinal": ordinal,
                    "rebuttal": rebuttal,
                    "upheld": upheld,
                    "confidence": confidence,
                }
                for ordinal, upheld in entries
            ]
        }
    )


class TestWhatIsRecorded:
    """The verdicts that reach a record, and the prose that has to come with them."""

    def test_an_upheld_finding_carries_the_objection_that_was_rejected(self) -> None:
        """A survived challenge is not an empty field.

        The rebuttal is kept whichever way the verdict went, because a person
        reading an upheld finding is entitled to see what was argued against it
        and rejected. A challenger that recorded prose only on concessions would
        make every upheld finding look unexamined.
        """
        provider = Answering([judgements((0, True), rebuttal="the quoted figure is current")])

        result = ChallengerAgent(provider).challenge([make_finding()])

        assert len(result.challenges) == 1
        assert result.challenges[0].verdict is Verdict.UPHELD
        assert result.challenges[0].rebuttal == "the quoted figure is current"

    def test_an_overturned_finding_is_a_concession(self) -> None:
        provider = Answering([judgements((0, False), rebuttal="the assumption was superseded")])

        result = ChallengerAgent(provider).challenge([make_finding()])

        assert result.challenges[0].verdict is Verdict.OVERTURNED
        assert result.challenges[0].conceded
        assert result.conceded == result.challenges

    def test_the_concede_rate_counts_decided_challenges_only(self) -> None:
        """The denominator is what was decided, not what was seen.

        Counting undecided challenges as non-concessions would let an agent that
        never commits read as an agent that never concedes, and those are
        opposite defects. Two decided, one conceded, one undecided: the rate is
        a half rather than a third.
        """
        provider = Answering(
            [
                json.dumps(
                    {
                        "judgements": [
                            {
                                "finding_ordinal": 0,
                                "rebuttal": "held",
                                "upheld": True,
                                "confidence": 0.9,
                            },
                            {
                                "finding_ordinal": 1,
                                "rebuttal": "gave way",
                                "upheld": False,
                                "confidence": 0.9,
                            },
                            {
                                "finding_ordinal": 2,
                                "rebuttal": "close",
                                "upheld": False,
                                "confidence": 0.1,
                            },
                        ]
                    }
                )
            ]
        )

        result = ChallengerAgent(provider).challenge(
            [make_finding(1), make_finding(2), make_finding(3)]
        )

        assert len(result.challenges) == 3
        assert len(result.decided) == 2
        assert result.concede_rate == pytest.approx(0.5)

    def test_a_batch_is_one_call_per_batch_and_not_one_per_finding(self) -> None:
        """Cost is a reported metric, so it is asserted rather than assumed."""
        provider = Answering([judgements(*[(ordinal, True) for ordinal in range(3)])])

        result = ChallengerAgent(provider, batch=8).challenge(
            [make_finding(number) for number in (1, 2, 3)]
        )

        assert result.calls == 1
        assert len(provider.requests) == 1

    def test_the_prompt_is_the_versioned_one_and_the_trace_can_prove_it(self) -> None:
        """ADR 0014: a metric means nothing unless its prompt can still be read."""
        provider = Answering([judgements((0, True))])

        ChallengerAgent(provider).challenge([make_finding()])

        request = provider.requests[0]
        assert request.task == CHALLENGE_TASK
        assert request.prompt_id == f"{CHALLENGE_TASK}@v1"
        assert request.prompt_sha


class TestWhatIsNotRecorded:
    """The refusals, each with the control that makes it mean something."""

    def test_a_verdict_below_the_floor_is_not_recorded(self) -> None:
        provider = Answering([judgements((0, False), confidence=DEFAULT_MIN_CONFIDENCE - 0.01)])

        result = ChallengerAgent(provider).challenge([make_finding()])

        assert result.challenges[0].verdict is Verdict.UNDECIDED
        assert not result.decided

    def test_the_same_call_above_the_floor_is_recorded(self) -> None:
        """The control. Without it the test above passes against an agent that
        records nothing at all, which is the opposite defect and looks the same
        from the outside."""
        provider = Answering([judgements((0, False), confidence=DEFAULT_MIN_CONFIDENCE)])

        result = ChallengerAgent(provider).challenge([make_finding()])

        assert result.challenges[0].verdict is Verdict.OVERTURNED

    def test_an_undecided_challenge_keeps_its_prose(self) -> None:
        """Losing the argument with the verdict would lose the useful half.

        The objection a challenger could not commit to is exactly what a person
        deciding for themselves wants to read.
        """
        provider = Answering(
            [judgements((0, False), confidence=0.1, rebuttal="arguable either way")]
        )

        result = ChallengerAgent(provider).challenge([make_finding()])

        assert result.challenges[0].rebuttal == "arguable either way"
        assert not result.challenges[0].decided

    def test_an_empty_rebuttal_forfeits_the_verdict(self) -> None:
        """A verdict with nothing behind it is a claim that a review happened.

        `Finding`'s validator refuses exactly this from the other side, so an
        agent that produced one would be building a record the store cannot
        hold. It is refused here, where the reason can be stated.
        """
        provider = Answering([judgements((0, True), rebuttal="   ")])

        result = ChallengerAgent(provider).challenge([make_finding()])

        assert result.challenges[0].verdict is Verdict.UNDECIDED
        assert result.challenges[0].rebuttal

    def test_the_same_call_with_a_rebuttal_reaches_a_verdict(self) -> None:
        """The control for the empty-rebuttal refusal."""
        provider = Answering([judgements((0, True), rebuttal="the figure is current")])

        result = ChallengerAgent(provider).challenge([make_finding()])

        assert result.challenges[0].verdict is Verdict.UPHELD

    def test_a_finding_that_already_carries_a_verdict_is_not_re_argued(self) -> None:
        """Re-running over a settled store cannot flip a recorded decision."""
        decided = make_finding(verdict=Verdict.UPHELD, challenge="already argued")
        provider = Answering([judgements((0, False))])

        result = ChallengerAgent(provider).challenge([decided])

        assert result.skipped == 1
        assert not result.challenges
        assert result.calls == 0
        assert not provider.requests

    def test_an_undecided_finding_beside_it_is_still_argued(self) -> None:
        """The control: skipping is per finding, not per pass."""
        decided = make_finding(1, verdict=Verdict.UPHELD, challenge="already argued")
        provider = Answering([judgements((0, True))])

        result = ChallengerAgent(provider).challenge([decided, make_finding(2)])

        assert result.skipped == 1
        assert [challenge.finding_id for challenge in result.challenges] == ["F-0002"]

    def test_a_malformed_batch_is_a_gap_and_not_a_set_of_concessions(self) -> None:
        """The difference matters: an unread finding is not an overturned one."""
        provider = Answering(["not json"] * 3)

        result = ChallengerAgent(provider).challenge([make_finding(1), make_finding(2)])

        assert result.unjudged == 2
        assert not result.challenges
        assert not result.conceded

    def test_a_refusing_provider_leaves_every_finding_undecided(self) -> None:
        provider = Refusing([])

        result = ChallengerAgent(provider).challenge([make_finding()])

        assert result.unjudged == 1
        assert not result.challenges

    def test_no_provider_challenges_nothing_and_raises_nothing(self) -> None:
        """Degrade quality, never correctness.

        Without a provider the findings keep the undecided verdict they already
        had, which `AbstentionGate` then routes to a person -- so the pipeline
        gets more careful rather than less.
        """
        result = ChallengerAgent(None).challenge([make_finding()])

        assert not result.challenges
        assert result.calls == 0

    def test_an_ordinal_outside_the_batch_is_dropped(self) -> None:
        """Recording a verdict against the wrong finding is worse than none.

        The finding it landed on would look reviewed, which is the one state
        `AbstentionGate` cannot detect.
        """
        provider = Answering([judgements((7, False))])

        result = ChallengerAgent(provider).challenge([make_finding()])

        assert not result.challenges
        assert result.unjudged == 1

    def test_a_repeated_ordinal_is_counted_once(self) -> None:
        provider = Answering([judgements((0, True), (0, False))])

        result = ChallengerAgent(provider).challenge([make_finding()])

        assert len(result.challenges) == 1
        assert result.challenges[0].verdict is Verdict.UPHELD

    def test_a_partial_answer_reports_the_rest_as_unjudged(self) -> None:
        """Answering about two of three findings leaves one unargued."""
        provider = Answering([judgements((0, True), (1, True))])

        result = ChallengerAgent(provider).challenge(
            [make_finding(1), make_finding(2), make_finding(3)]
        )

        assert len(result.challenges) == 2
        assert result.unjudged == 1

    def test_nothing_to_challenge_costs_no_call(self) -> None:
        provider = Answering([judgements((0, True))])

        result = ChallengerAgent(provider).challenge([])

        assert result.calls == 0
        assert not provider.requests

    def test_a_concede_rate_with_no_decisions_is_not_a_low_concede_rate(self) -> None:
        """Zero out of zero is no measurement, and reads as one.

        `praxis.eval.governance` reports the denominator beside the rate for
        exactly this reason -- 0.0 here and 0.0 from a challenger that upheld
        everything are different facts wearing the same number.
        """
        result = ChallengerAgent(None).challenge([make_finding()])

        assert result.concede_rate == 0.0
        assert not result.decided


class TestTheAgentsBounds:
    """The arguments the constructor refuses, and where it sits in the routing table."""

    def test_a_batch_of_zero_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one finding"):
            ChallengerAgent(None, batch=0)

    def test_a_confidence_threshold_outside_zero_to_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="between 0 and 1"):
            ChallengerAgent(None, min_confidence=1.5)

    def test_the_challenger_keeps_its_model_route(self) -> None:
        """ADR 0030, and the prediction ADR 0027 made about where the run stops.

        Six consecutive components moved to the deterministic side across Phases
        7 and 8. This is the seventh and it does not, which is what ADR 0027
        said would happen: its input is prose nobody has reduced to a record.
        """
        assert CHALLENGER_NAME not in NON_LLM_AGENTS
        assert CHALLENGER_NAME in routed_agents()
        assert role_for_agent(CHALLENGER_NAME) is ModelRole.REASON


class TestTheListing:
    """What the model is actually shown, since the verdict rests on it."""

    def test_every_finding_is_addressable_by_its_ordinal(self) -> None:
        rendered = listing([make_finding(1), make_finding(2)])

        assert "[0]" in rendered
        assert "[1]" in rendered

    def test_the_prosecution_and_the_severity_both_reach_the_model(self) -> None:
        """The case and how loudly it will be shouted are both part of the test.

        A finding worth overturning is often one whose severity outruns its
        evidence, and a listing that hid the severity would make that
        unarguable.
        """
        rendered = listing([make_finding(1, severity=Severity.CRITICAL)])

        assert "under 50 GB" in rendered
        assert "critical" in rendered

    def test_the_finding_kind_is_named_so_a_projection_is_not_read_as_a_measurement(
        self,
    ) -> None:
        """ADR 0028's distinction has to survive the trip to the model.

        A `STALE_DECISION` is a projection about a number nobody has observed;
        an `ASSUMPTION_BREACH` is something that happened. A challenger shown
        neither kind would argue against both the same way.
        """
        rendered = listing([make_finding(1, kind=FindingKind.STALE_DECISION)])

        assert "stale_decision" in rendered


def test_a_malformed_answer_is_retried_before_it_is_given_up_on() -> None:
    """The repair loop is the seam's, and this asserts the agent lets it run.

    Three attempts, then the batch is a gap. An agent that caught
    `MalformedOutputError` on the first attempt would silently halve the
    pipeline's yield against a model having a bad minute.
    """
    provider = Answering(["not json", "still not json", json.dumps({"judgements": []})])

    result = ChallengerAgent(provider).challenge([make_finding()])

    assert len(provider.requests) == 3
    assert result.unjudged == 1


def test_giving_up_reports_the_attempts_it_spent() -> None:
    """A gap that cost three calls is not a gap that cost none."""
    provider = Answering(["not json"] * 3)

    result = ChallengerAgent(provider).challenge([make_finding()])

    assert result.calls == 3


def test_the_malformed_error_carries_its_attempt_count() -> None:
    """Pins the contract this agent reads `calls` off, from the other side."""
    error = MalformedOutputError(schema_name="Judgements", attempts=3, detail="unusable", raw="{")

    assert error.attempts == 3
