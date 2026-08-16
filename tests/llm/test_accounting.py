"""Cost arithmetic and the spending ceiling.

Two things are being defended here. One is invariant 4: money is `Decimal`, and
a float creeping in through a setting or a multiplier would be invisible until
a run of thousands of calls reported a total nobody could reproduce. The other
is that the ceiling is checked *before* the money is spent -- a limit that only
notices afterwards is a report, and the person paying for this asked for a
limit.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.config.models import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    ModelRole,
    resolve,
)
from praxis.config.settings import Settings
from praxis.llm.accounting import (
    CHARS_PER_TOKEN_FLOOR,
    ZERO_USD,
    CostLedger,
    cost_of,
    upper_bound_cost,
)
from praxis.llm.errors import CostCeilingExceededError
from praxis.llm.types import LLMRequest, Message, MessageRole, TokenUsage

SPEC = resolve(ModelRole.EXTRACT)

TOKEN_COUNTS = st.integers(min_value=0, max_value=200_000)


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


class TestCostOfOneCall:
    def test_a_call_that_consumed_nothing_costs_nothing(self):
        assert cost_of(SPEC, TokenUsage()) == ZERO_USD

    def test_input_and_output_are_priced_at_their_own_rates(self):
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost_of(SPEC, usage) == SPEC.input_usd_per_mtok + SPEC.output_usd_per_mtok

    def test_a_cache_hit_is_a_tenth_of_an_ordinary_input_token(self):
        cached = TokenUsage(cache_read_input_tokens=1_000_000)
        plain = TokenUsage(input_tokens=1_000_000)
        assert cost_of(SPEC, cached) == cost_of(SPEC, plain) * CACHE_READ_MULTIPLIER

    def test_writing_the_cache_costs_more_than_sending_the_tokens_plainly(self):
        written = TokenUsage(cache_creation_input_tokens=1_000_000)
        plain = TokenUsage(input_tokens=1_000_000)
        assert cost_of(SPEC, written) == cost_of(SPEC, plain) * CACHE_WRITE_MULTIPLIER

    def test_every_figure_is_a_decimal(self):
        # Invariant 4. A float here survives every test that only checks
        # magnitudes and shows up as an irreproducible total in the eval table.
        assert isinstance(cost_of(SPEC, TokenUsage(input_tokens=3)), Decimal)

    def test_nothing_is_rounded_per_call(self):
        # One token of the cheapest input is far below a cent. Rounding it to
        # money here would price a thousand such calls at zero.
        assert cost_of(SPEC, TokenUsage(input_tokens=1)) > ZERO_USD

    def test_a_more_expensive_model_costs_more_for_the_same_work(self):
        usage = TokenUsage(input_tokens=10_000, output_tokens=1_000)
        scan = cost_of(resolve(ModelRole.SCAN), usage)
        reason = cost_of(resolve(ModelRole.REASON), usage)
        assert scan < reason

    @given(inputs=TOKEN_COUNTS, outputs=TOKEN_COUNTS, reads=TOKEN_COUNTS, writes=TOKEN_COUNTS)
    def test_cost_never_goes_negative(self, inputs, outputs, reads, writes):
        usage = TokenUsage(
            input_tokens=inputs,
            output_tokens=outputs,
            cache_read_input_tokens=reads,
            cache_creation_input_tokens=writes,
        )
        assert cost_of(SPEC, usage) >= ZERO_USD

    @given(first=TOKEN_COUNTS, second=TOKEN_COUNTS)
    def test_pricing_two_calls_together_equals_pricing_them_apart(self, first, second):
        # The eval harness sums per-call costs and also prices summed usage.
        # If those two disagree the reported total depends on the order the
        # report was written in.
        one = TokenUsage(input_tokens=first, output_tokens=second)
        two = TokenUsage(input_tokens=second, output_tokens=first)
        assert cost_of(SPEC, one + two) == cost_of(SPEC, one) + cost_of(SPEC, two)


class TestUpperBound:
    def test_it_prices_the_full_output_cap(self):
        # max_tokens is what the API will let the model spend, so the worst
        # case is that it spends all of it.
        cheap = upper_bound_cost(SPEC, request(max_tokens=100))
        dear = upper_bound_cost(SPEC, request(max_tokens=10_000))
        assert dear > cheap

    def test_a_longer_prompt_bounds_higher(self):
        short = upper_bound_cost(SPEC, request(system="Find decisions."))
        long = upper_bound_cost(SPEC, request(system="Find decisions. " * 500))
        assert long > short

    def test_the_bound_over_counts_rather_than_under_counts(self):
        # The estimate is ours, not the API's, and it is wrong on purpose: a
        # ceiling that under-counts is not a ceiling.
        assert CHARS_PER_TOKEN_FLOOR <= 2

    def test_it_really_bounds_a_plausible_call(self):
        call = request()
        actual = cost_of(
            SPEC,
            TokenUsage(input_tokens=len(call.prompt_text) // 4, output_tokens=call.max_tokens),
        )
        assert upper_bound_cost(SPEC, call) >= actual


class TestLedger:
    def test_a_new_ledger_has_spent_nothing(self):
        ledger = CostLedger(ceiling_usd=Decimal("5"))
        assert ledger.spent_usd == ZERO_USD
        assert ledger.calls == 0
        assert ledger.remaining_usd == Decimal("5")

    def test_a_ceiling_of_zero_is_a_bug_not_a_configuration(self):
        with pytest.raises(ValueError, match="positive"):
            CostLedger(ceiling_usd=ZERO_USD)

    def test_recording_a_call_moves_the_total_and_the_tokens(self):
        ledger = CostLedger(ceiling_usd=Decimal("5"))
        usage = TokenUsage(input_tokens=1_000, output_tokens=200)
        cost = ledger.record(SPEC, usage)
        assert ledger.spent_usd == cost
        assert ledger.calls == 1
        assert ledger.usage.total == usage.total

    def test_two_ledgers_do_not_see_each_other(self):
        # Not a global. A ceiling reached in one test must not decide the
        # outcome of the next one.
        first = CostLedger(ceiling_usd=Decimal("5"))
        second = CostLedger(ceiling_usd=Decimal("5"))
        first.record(SPEC, TokenUsage(input_tokens=1_000_000))
        assert second.spent_usd == ZERO_USD

    def test_remaining_never_goes_below_zero(self):
        ledger = CostLedger(ceiling_usd=Decimal("0.01"))
        ledger.record(SPEC, TokenUsage(input_tokens=10_000_000))
        assert ledger.remaining_usd == ZERO_USD

    def test_an_affordable_call_passes_the_check(self):
        CostLedger(ceiling_usd=Decimal("5")).check_affordable(SPEC, request())

    def test_a_call_past_the_ceiling_is_refused_before_it_is_made(self):
        ledger = CostLedger(ceiling_usd=Decimal("5"))
        ledger.record(SPEC, TokenUsage(input_tokens=2_000_000))
        with pytest.raises(CostCeilingExceededError) as caught:
            ledger.check_affordable(SPEC, request())
        assert caught.value.ceiling == Decimal("5")
        assert caught.value.spent == ledger.spent_usd

    def test_the_refusal_says_how_to_proceed(self):
        # The error is read by the person paying, so it names the setting and
        # the offline alternative rather than only the number.
        ledger = CostLedger(ceiling_usd=Decimal("0.000001"))
        with pytest.raises(CostCeilingExceededError, match="PRAXIS_COST_CEILING_USD"):
            ledger.check_affordable(SPEC, request())

    def test_a_free_call_counts_tokens_but_no_money(self):
        # A mock run still reports what it would have cost in tokens; the
        # eval table's token column has to work offline.
        ledger = CostLedger(ceiling_usd=Decimal("5"))
        assert ledger.record_free(TokenUsage(input_tokens=40, output_tokens=9)) == ZERO_USD
        assert ledger.spent_usd == ZERO_USD
        assert ledger.calls == 1
        assert ledger.usage.total == 49

    def test_a_free_run_never_hits_the_ceiling(self):
        ledger = CostLedger(ceiling_usd=Decimal("0.01"))
        for _ in range(50):
            ledger.record_free(TokenUsage(input_tokens=100_000))
        assert ledger.remaining_usd == Decimal("0.01")


class TestFromSettings:
    def test_the_configured_ceiling_arrives_as_a_decimal(self):
        # The setting is a float on the way in. This is the one line where
        # that stops being true, and it goes through str so that 0.1 is 0.1.
        ledger = CostLedger.from_settings(Settings(cost_ceiling_usd=0.1))
        assert ledger.ceiling_usd == Decimal("0.1")

    def test_the_default_ceiling_is_carried_through(self):
        settings = Settings()
        ledger = CostLedger.from_settings(settings)
        assert ledger.ceiling_usd == Decimal(str(settings.cost_ceiling_usd))

    @given(ceiling=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("100"), places=2))
    def test_any_positive_ceiling_starts_with_that_much_headroom(self, ceiling):
        assert CostLedger(ceiling_usd=ceiling).remaining_usd == ceiling
