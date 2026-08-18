"""DecisionScout, against answers built to be wrong in specific ways.

The mechanism tests use a real `LLMProvider` subclass rather than a stub object,
so every call still goes through routing, the cost ledger and the trace sink --
the lesson Phase 2's report recorded, and the reason the segmenter's tests found
real defects. Only `_invoke` is replaced.
"""

from __future__ import annotations

import json

import pytest
from praxis.agents.errors import Refusal
from praxis.agents.offering import offering_of, windows_of
from praxis.agents.scout import (
    SCAN_TASK,
    SCOUT_NAME,
    Candidate,
    DecisionScout,
    DecisionSightings,
)
from praxis.config.models import ModelRole, role_for_agent
from praxis.config.settings import Settings
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.prompts.library import load

from tests.agents.conftest import Answering, Refusing


def sightings(*entries) -> str:
    """Render an answer the way the model would."""
    return json.dumps(
        {
            "sightings": [
                {
                    "passage_ordinal": ordinal,
                    "label": "chose something",
                    "why": "it says we are going with it",
                    "confidence": confidence,
                }
                for ordinal, confidence in entries
            ]
        }
    )


class TestRouting:
    def test_the_scout_is_on_the_cheap_tier(self):
        # ADR 0013. It runs once per span over the whole corpus, so this is the
        # cost-per-document decision and not a preference.
        assert role_for_agent(SCOUT_NAME) is ModelRole.SCAN

    def test_the_agent_names_itself_and_never_a_model(self, spans):
        provider = Answering([sightings((0, 0.6))])
        DecisionScout(provider).scan(spans)
        assert all(request.agent == SCOUT_NAME for request in provider.requests)


class TestPromptProvenance:
    def test_every_call_names_the_prompt_version_it_read(self, spans):
        provider = Answering([sightings()])
        DecisionScout(provider, window_spans=100).scan(spans)
        prompt = load(SCAN_TASK)
        assert provider.requests[0].prompt_id == prompt.id
        assert provider.requests[0].prompt_sha == prompt.sha256

    def test_the_task_and_the_prompt_name_agree(self):
        # ADR 0014's construction: task and prompt_id can never describe two
        # different things, because the prompt file is named after the task.
        assert load(SCAN_TASK).name == SCAN_TASK


class TestFindingCandidates:
    def test_a_marked_passage_becomes_a_candidate_over_that_span(self, spans):
        provider = Answering([sightings((2, 0.7))])
        report = DecisionScout(provider, window_spans=100).scan(spans)
        assert [candidate.span for candidate in report.candidates] == [spans[2]]

    def test_the_scout_carries_the_model_s_reasoning_without_storing_it(self, spans):
        provider = Answering([sightings((1, 0.55))])
        (candidate,) = DecisionScout(provider, window_spans=100).scan(spans).candidates
        assert candidate.label == "chose something"
        assert candidate.why
        assert candidate.confidence == 0.55

    def test_low_confidence_is_still_a_candidate(self, spans):
        # The whole point of this agent. Precision is recovered downstream;
        # a passage it filters out is a decision nothing recovers.
        provider = Answering([sightings((0, 0.01))])
        assert len(DecisionScout(provider, window_spans=100).scan(spans).candidates) == 1

    def test_an_empty_answer_is_a_real_answer(self, spans):
        provider = Answering([sightings()])
        report = DecisionScout(provider, window_spans=100).scan(spans)
        assert report.candidates == ()
        assert report.blind_windows == 0

    def test_a_document_with_no_spans_makes_no_calls(self):
        provider = Answering([])
        report = DecisionScout(provider).scan(())
        assert report == type(report)()
        assert provider.requests == []

    def test_candidates_come_back_in_document_order(self, spans):
        provider = Answering([sightings((3, 0.5), (1, 0.9), (2, 0.4))])
        report = DecisionScout(provider, window_spans=100).scan(spans)
        assert [c.span for c in report.candidates] == [spans[1], spans[2], spans[3]]

    def test_a_repeated_ordinal_is_one_candidate(self, spans):
        # Two candidates over one span would pay the structurer twice for one
        # question.
        provider = Answering([sightings((1, 0.5), (1, 0.9))])
        assert len(DecisionScout(provider, window_spans=100).scan(spans).candidates) == 1


class TestCitationsItCannotFabricate:
    @pytest.mark.parametrize("ordinal", [-1, 99, 1000])
    def test_an_ordinal_that_was_not_offered_is_refused(self, spans, ordinal):
        provider = Answering([sightings((ordinal, 0.9))])
        report = DecisionScout(provider, window_spans=100).scan(spans)
        assert report.candidates == ()
        (rejection,) = report.rejections
        assert rejection.refusal is Refusal.UNOFFERED_SPAN
        assert rejection.ordinal == ordinal

    def test_a_good_citation_survives_beside_a_bad_one(self, spans):
        provider = Answering([sightings((0, 0.8), (999, 0.9))])
        report = DecisionScout(provider, window_spans=100).scan(spans)
        assert len(report.candidates) == 1
        assert len(report.rejections) == 1

    def test_the_scout_never_quotes_and_so_cannot_mis_attribute(self):
        # ADR 0015 at its strongest: with no quotation there is nothing to
        # attribute to the wrong passage. The response model is the evidence.
        assert "quote" not in str(DecisionSightings.model_json_schema())


class TestWindows:
    def test_a_long_document_is_scanned_in_several_calls(self, spans):
        provider = Answering([sightings() for _ in windows_of(spans, 3)])
        report = DecisionScout(provider, window_spans=3).scan(spans)
        assert report.windows == len(windows_of(spans, 3))
        assert len(provider.requests) == report.windows

    def test_every_span_is_offered_exactly_once(self, spans):
        provider = Answering([sightings() for _ in windows_of(spans, 3)])
        DecisionScout(provider, window_spans=3).scan(spans)
        shown = "\n".join(request.messages[0].content for request in provider.requests)
        for span in spans:
            assert span.text.strip().splitlines()[0] in shown

    def test_each_window_numbers_from_zero_so_ordinals_stay_small(self, spans):
        provider = Answering([sightings((0, 0.5)) for _ in windows_of(spans, 2)])
        report = DecisionScout(provider, window_spans=2).scan(spans)
        # Ordinal 0 of every window resolves to a different span.
        assert len({candidate.span.id for candidate in report.candidates}) == report.windows

    @pytest.mark.parametrize("size", [0, -3])
    def test_a_window_must_hold_something(self, size):
        with pytest.raises(ValueError, match="at least one span"):
            DecisionScout(Answering([]), window_spans=size)


class TestBadAnswersAboutOneDocument:
    def test_a_refusal_leaves_the_window_blind_rather_than_ending_the_run(self, spans):
        report = DecisionScout(Refusing([]), window_spans=100).scan(spans)
        assert report.blind_windows == 1
        assert report.blind
        assert report.candidates == ()

    def test_an_unrepairable_answer_leaves_the_window_blind(self, spans):
        provider = Answering(["not json", "still not json", "no"])
        report = DecisionScout(provider, window_spans=100).scan(spans)
        assert report.blind_windows == 1
        assert report.calls == 3

    def test_one_blind_window_does_not_blind_the_others(self, spans):
        windows = len(windows_of(spans, 3))
        answers = ["not json", "not json", "not json"] + [sightings((0, 0.5))] * (windows - 1)
        report = DecisionScout(Answering(answers), window_spans=3).scan(spans)
        assert report.blind_windows == 1
        assert len(report.candidates) == windows - 1

    def test_a_repaired_answer_is_counted_as_the_attempts_it_took(self, spans):
        provider = Answering(["not json", sightings((0, 0.5))])
        report = DecisionScout(provider, window_spans=100).scan(spans)
        assert report.calls == 2
        assert len(report.candidates) == 1

    def test_there_is_no_floor_to_degrade_to(self, spans):
        # Deliberate, and the opposite of the segmenter. "Every passage holds a
        # decision" is not a cheaper answer than none, it is a more expensive
        # one -- it would pay the structurer for the whole corpus.
        report = DecisionScout(Refusing([]), window_spans=100).scan(spans)
        assert report.candidates == ()


class TestTracing:
    def test_every_attempt_is_traced(self, spans):
        provider = Answering(["not json", sightings((0, 0.5))])
        DecisionScout(provider, window_spans=100).scan(spans)
        assert len(provider.sink.traces) == 2

    def test_the_trace_says_which_document_the_call_was_about(self, spans):
        provider = Answering([sightings()])
        DecisionScout(provider, window_spans=100).scan(spans)
        assert provider.requests[0].metadata["doc_id"] == spans[0].doc_id


class TestOfflineTheAnswersAreRealOrdinals:
    def test_the_mock_cites_passages_it_was_really_shown(self, spans):
        # praxis.llm.synthesis draws an ordinal-named integer from the bracketed
        # labels in the prompt. If that ever stops holding, every offline answer
        # would cite a passage that was never offered and this agent's numbers
        # would be a systematic failure rather than a measurement.
        provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
        report = DecisionScout(provider, window_spans=4).scan(spans)
        assert report.rejections == ()
        assert all(isinstance(candidate, Candidate) for candidate in report.candidates)

    def test_two_offline_runs_over_one_document_agree(self, spans):
        # The determinism the whole ablation table rests on.
        def run():
            provider = MockProvider(sink=MemoryTraceSink(), settings=Settings())
            return DecisionScout(provider, window_spans=4).scan(spans)

        assert [c.span.id for c in run().candidates] == [c.span.id for c in run().candidates]


def test_the_offering_the_scout_builds_is_the_shared_one(spans):
    """No second citation path -- the design constraint, asserted."""
    assert offering_of(spans).render().startswith("[0] ")
