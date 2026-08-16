"""Playing back a recording, and refusing to play back the wrong one.

What matters here is mostly what does not happen. A replay run that fell back
to the network on a miss, or that played a fixture recorded for a different
model or a hand-edited prompt, would still produce numbers -- and those numbers
would depend on which fixtures a machine happened to have. Every test below is
about that failure being loud instead.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest
from praxis.config.models import resolve, role_for_agent
from praxis.config.settings import ProviderName, Settings
from praxis.llm.accounting import CostLedger
from praxis.llm.errors import ProviderRefusalError, ReplayCacheMissError
from praxis.llm.replay import Recording, ReplayProvider, fixture_path, write_recording
from praxis.llm.trace import MemoryTraceSink
from praxis.llm.types import (
    CallOutcome,
    LLMRequest,
    LLMResponse,
    Message,
    MessageRole,
    StopReason,
    TokenUsage,
)

LIVE_MODEL = resolve(role_for_agent("DecisionScout")).model_id
USAGE = TokenUsage(input_tokens=812, output_tokens=64, cache_read_input_tokens=100)


def request(**overrides) -> LLMRequest:
    """Build a valid request, overriding one field at a time."""
    fields = {
        "agent": "DecisionScout",
        "task": "scan_for_decisions",
        "system": "You find decisions.",
        "messages": (Message(role=MessageRole.USER, content="We chose SQLite."),),
    }
    fields.update(overrides)
    return LLMRequest(**fields)


def response(**overrides) -> LLMResponse:
    """Build a response as a live call would have returned one."""
    fields = {
        "text": '{"candidates": []}',
        "model_id": LIVE_MODEL,
        "usage": USAGE,
        "stop_reason": StopReason.END_TURN,
    }
    fields.update(overrides)
    return LLMResponse(**fields)


@pytest.fixture
def root(tmp_path):
    """A fixture directory of this test's own."""
    return tmp_path / "replay"


class TestWhereARecordingLives:
    def test_the_path_is_a_function_of_the_request(self, root):
        # One request, one path, on any machine -- otherwise a recorded corpus
        # is not shareable.
        assert fixture_path(root, request()) == fixture_path(root, request())

    def test_two_questions_are_two_files(self, root):
        assert fixture_path(root, request()) != fixture_path(root, request(task="other"))

    def test_recordings_are_filed_under_the_agent_that_asked(self, root):
        # A corpus of bare digests is unreadable, and the person recording
        # fixtures needs to see which agent is missing them.
        assert fixture_path(root, request()).parent.name == "DecisionScout"

    def test_writing_creates_the_directory_it_needs(self, root):
        assert write_recording(root, request(), response()).is_file()


class TestPlayingBack:
    def test_a_recorded_answer_comes_back_unchanged(self, root):
        write_recording(root, request(), response())
        played = ReplayProvider(root=root).complete(request())
        assert played.text == response().text
        assert played.stop_reason is StopReason.END_TURN

    def test_the_recorded_model_is_reported_not_a_placeholder(self, root):
        # A replayed row describes the model that really answered, so a metric
        # over replayed traces is a metric about that model.
        write_recording(root, request(), response())
        assert ReplayProvider(root=root).complete(request()).model_id == LIVE_MODEL

    def test_the_recorded_token_counts_survive_the_round_trip(self, root):
        write_recording(root, request(), response())
        assert ReplayProvider(root=root).complete(request()).usage == USAGE

    def test_replaying_costs_nothing(self, root):
        write_recording(root, request(), response())
        provider = ReplayProvider(root=root)
        provider.complete(request())
        assert provider.ledger.spent_usd == Decimal("0")

    def test_tokens_are_still_counted(self, root):
        # "What this run would have cost" is a reported figure, and a replayed
        # run is the cheapest place to produce it.
        write_recording(root, request(), response())
        provider = ReplayProvider(root=root)
        provider.complete(request())
        assert provider.ledger.usage.input_tokens == USAGE.input_tokens

    def test_the_trace_says_which_provider_served_it(self, root):
        write_recording(root, request(), response())
        sink = MemoryTraceSink()
        ReplayProvider(root=root, sink=sink).complete(request())
        assert sink.traces[0].provider == ProviderName.REPLAY.value
        assert sink.traces[0].outcome is CallOutcome.OK

    def test_a_recorded_refusal_is_replayed_as_a_refusal(self, root):
        # The failure paths are the ones worth having a corpus of.
        write_recording(root, request(), response(text="", stop_reason=StopReason.REFUSAL))
        sink = MemoryTraceSink()
        with pytest.raises(ProviderRefusalError):
            ReplayProvider(root=root, sink=sink).complete(request())
        assert sink.traces[0].outcome is CallOutcome.REFUSED

    def test_the_default_directory_is_the_configured_one(self):
        assert ReplayProvider().root == Settings().replay_dir


class TestAMissIsLoud:
    def test_an_absent_recording_raises(self, root):
        # Never a fallback to the network. An eval run that silently went live
        # on a miss would depend on which fixtures a machine happened to have.
        with pytest.raises(ReplayCacheMissError):
            ReplayProvider(root=root).complete(request())

    def test_the_error_says_where_to_look_and_how_to_record(self, root):
        with pytest.raises(ReplayCacheMissError) as caught:
            ReplayProvider(root=root).complete(request())
        assert caught.value.agent == "DecisionScout"
        assert caught.value.path == fixture_path(root, request())
        assert "PRAXIS_RECORD_REPLAY=true" in str(caught.value)

    def test_a_miss_is_still_written_down(self, root):
        # A trace store missing exactly the calls that went wrong describes a
        # pipeline that never has any trouble.
        sink = MemoryTraceSink()
        with pytest.raises(ReplayCacheMissError):
            ReplayProvider(root=root, sink=sink).complete(request())
        assert sink.traces[0].outcome is CallOutcome.ERROR
        assert "ReplayCacheMissError" in (sink.traces[0].error or "")

    def test_a_recording_by_another_model_is_refused(self, root):
        # A recording made before a deprecation is evidence about the old
        # model. praxis/config/models.py is the only file that changes, and
        # this is where that change surfaces.
        write_recording(root, request(), response(model_id="claude-haiku-3-5"))
        with pytest.raises(ReplayCacheMissError, match="rather than"):
            ReplayProvider(root=root).complete(request())

    def test_a_fixture_edited_by_hand_is_refused(self, root):
        # Fixing a typo in a recorded prompt makes the fixture the answer to a
        # different question, and nothing else in the system would notice.
        path = write_recording(root, request(), response())
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["request"]["system"] = "You find estimates."
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ReplayCacheMissError, match="different question"):
            ReplayProvider(root=root).complete(request())

    def test_an_unreadable_fixture_is_refused(self, root):
        path = fixture_path(root, request())
        path.parent.mkdir(parents=True)
        path.write_text("{ half a fi", encoding="utf-8")
        with pytest.raises(ReplayCacheMissError, match="unreadable"):
            ReplayProvider(root=root).complete(request())

    def test_a_fixture_missing_fields_is_refused(self, root):
        path = fixture_path(root, request())
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"prompt_hash": "abc"}), encoding="utf-8")
        with pytest.raises(ReplayCacheMissError, match="unreadable"):
            ReplayProvider(root=root).complete(request())

    def test_the_ceiling_is_never_consulted(self, root):
        # bills=False: a replayed call spends nothing, so a run out of budget
        # can still be reproduced from its fixtures.
        write_recording(root, request(), response())
        provider = ReplayProvider(root=root, ledger=CostLedger(ceiling_usd=Decimal("0.0000001")))
        assert provider.complete(request()).text


class TestTheFixtureFormat:
    def test_it_survives_a_round_trip(self, root):
        original = Recording.of(request(), response())
        rebuilt = Recording.from_json(json.loads(original.to_json()))
        assert rebuilt.usage == original.usage
        assert rebuilt.stop_reason is original.stop_reason
        assert rebuilt.request == original.request

    def test_it_is_readable_by_a_person(self, root):
        # Fixtures are diffed by hand. The replay key is computed from the
        # request rather than from these bytes, so the formatting is free.
        text = write_recording(root, request(), response()).read_text(encoding="utf-8")
        assert "\n" in text
        assert "We chose SQLite." in text

    def test_non_ascii_is_kept_as_itself(self, root):
        sinhala = request(messages=(Message(role=MessageRole.USER, content="අපි SQLite තෝරාගත්තා."),))
        text = write_recording(root, sinhala, response()).read_text(encoding="utf-8")
        assert "අපි SQLite තෝරාගත්තා." in text

    def test_a_recorded_time_is_timezone_aware(self):
        # Invariant 5. "Which recording is newer" must not depend on which
        # machine wrote it.
        assert Recording.of(request(), response()).recorded_at.tzinfo is not None

    def test_a_naive_recorded_time_is_refused(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            Recording(
                prompt_hash="0" * 32,
                request={},
                response="{}",
                model_id=LIVE_MODEL,
                stop_reason=StopReason.END_TURN,
                usage=TokenUsage(),
                recorded_at=datetime(2026, 8, 16, 12, 0),  # noqa: DTZ001 -- the point
            )

    def test_a_stop_reason_this_build_does_not_know_is_tolerated(self, root):
        # A new member appearing in the API is not a reason for a replay run
        # to die, but it is a reason for the trace to say so.
        path = write_recording(root, request(), response())
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["stop_reason"] = "something_new"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert ReplayProvider(root=root).complete(request()).stop_reason is StopReason.OTHER
