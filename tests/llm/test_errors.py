"""The provider seam's exception vocabulary.

These assert two things worth asserting: that everything the layer raises can
be caught with one name that does not mention a vendor, and that the messages
name the fix. A cache miss whose message does not say how to record the fixture
is a message that costs someone an hour.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from praxis.llm.errors import (
    CostCeilingExceededError,
    MalformedOutputError,
    ProviderError,
    ProviderRefusalError,
    ProviderUnavailableError,
    ReplayCacheMissError,
    SchemaNotSupportedError,
)

EVERY_ERROR = [
    ProviderUnavailableError,
    ProviderRefusalError,
    ReplayCacheMissError,
    MalformedOutputError,
    SchemaNotSupportedError,
    CostCeilingExceededError,
]


@pytest.mark.parametrize("error_class", EVERY_ERROR)
def test_everything_is_catchable_as_one_provider_error(error_class):
    # The whole point of the module: a caller catches the model layer without
    # naming a vendor, the same way praxis.store.errors hides sqlite3.
    assert issubclass(error_class, ProviderError)


class TestRefusal:
    def test_it_names_the_agent_and_the_category(self):
        error = ProviderRefusalError("ChallengerAgent", category="cyber")
        assert error.agent == "ChallengerAgent"
        assert error.category == "cyber"
        assert "ChallengerAgent" in str(error)
        assert "cyber" in str(error)

    def test_the_category_is_optional_because_the_api_omits_it(self):
        # stop_details is informational and can be absent even on a refusal,
        # so the class cannot require it.
        error = ProviderRefusalError("ChallengerAgent")
        assert error.category is None
        assert "ChallengerAgent" in str(error)


class TestCacheMiss:
    def test_it_carries_the_key_the_agent_and_the_path(self):
        error = ReplayCacheMissError("abc123", "DecisionScout", Path("fixtures/abc123.json"))
        assert error.prompt_hash == "abc123"
        assert error.agent == "DecisionScout"
        assert error.path == Path("fixtures/abc123.json")

    def test_the_message_says_how_to_record_the_missing_fixture(self):
        # A miss is the expected failure of a fresh corpus, so the message is
        # a set of instructions rather than a complaint.
        message = str(ReplayCacheMissError("abc123", "DecisionScout", Path("f.json")))
        assert "abc123" in message
        assert "PRAXIS_RECORD_REPLAY" in message
        assert "mock" in message


class TestMalformedOutput:
    def test_it_carries_the_evidence_of_the_final_attempt(self):
        error = MalformedOutputError("Candidates", attempts=3, detail="not an object", raw="[]")
        assert error.schema_name == "Candidates"
        assert error.attempts == 3
        assert error.detail == "not an object"
        assert error.raw == "[]"

    def test_the_message_reports_how_many_attempts_were_made(self):
        # Distinguishing "malformed once" from "malformed three times" is the
        # difference between a flake and a broken prompt.
        assert "3 attempts" in str(
            MalformedOutputError("Candidates", attempts=3, detail="x", raw="")
        )


class TestCostCeiling:
    def test_it_carries_both_figures_as_decimals(self):
        error = CostCeilingExceededError(Decimal("5.01"), Decimal("5.00"))
        assert error.spent == Decimal("5.01")
        assert error.ceiling == Decimal("5.00")

    def test_the_message_names_the_setting_that_raises_it(self):
        message = str(CostCeilingExceededError(Decimal("5.01"), Decimal("5.00")))
        assert "5.01" in message
        assert "PRAXIS_COST_CEILING_USD" in message
