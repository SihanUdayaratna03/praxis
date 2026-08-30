"""The model registry, its routing invariants, and cost arithmetic.

The cost properties are checked with hypothesis rather than examples: pricing
is summed across thousands of calls in the eval harness, and the failure mode
worth catching is a rounding or overflow behaviour that only shows up at an
input nobody thought to write down.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.config.models import (
    MOCK_MODEL_ID,
    NON_LLM_AGENTS,
    ModelRole,
    UnknownAgentError,
    estimate_cost_usd,
    resolve,
    role_for_agent,
    routed_agents,
)

TOKENS = st.integers(min_value=0, max_value=10_000_000)


@pytest.mark.parametrize("role", list(ModelRole))
def test_every_role_resolves_to_a_priced_model(role: ModelRole) -> None:
    spec = resolve(role)

    assert spec.model_id
    assert spec.input_usd_per_mtok > 0
    assert spec.output_usd_per_mtok > 0
    assert spec.context_tokens > 0
    assert spec.max_output_tokens > 0
    assert spec.rationale


def test_roles_are_ordered_cheapest_to_most_expensive() -> None:
    """The routing table only saves money if the tiers are actually tiered."""
    scan = resolve(ModelRole.SCAN)
    extract = resolve(ModelRole.EXTRACT)
    reason = resolve(ModelRole.REASON)

    assert scan.input_usd_per_mtok < extract.input_usd_per_mtok < reason.input_usd_per_mtok
    assert scan.output_usd_per_mtok < extract.output_usd_per_mtok < reason.output_usd_per_mtok


def test_each_role_uses_a_distinct_model() -> None:
    """Three roles pointing at one model is a routing table that does nothing."""
    ids = [resolve(role).model_id for role in ModelRole]

    assert len(set(ids)) == len(ids)


def test_prices_are_decimal_not_float() -> None:
    """Money must never be binary floating point in this codebase."""
    for role in ModelRole:
        spec = resolve(role)
        assert isinstance(spec.input_usd_per_mtok, Decimal)
        assert isinstance(spec.output_usd_per_mtok, Decimal)


def test_specs_are_immutable() -> None:
    spec = resolve(ModelRole.SCAN)

    with pytest.raises(AttributeError):
        spec.model_id = "something-else"


@pytest.mark.parametrize("agent", sorted(routed_agents()))
def test_every_routed_agent_reaches_a_real_model(agent: str) -> None:
    assert resolve(role_for_agent(agent)).model_id


def test_deterministic_agents_are_never_routed() -> None:
    """The invariant that keeps the calibration maths reproducible."""
    assert not (routed_agents() & NON_LLM_AGENTS)


@pytest.mark.parametrize("agent", sorted(NON_LLM_AGENTS))
def test_asking_for_a_route_on_a_deterministic_agent_raises(agent: str) -> None:
    with pytest.raises(UnknownAgentError, match="deterministic"):
        role_for_agent(agent)


def test_an_unknown_agent_raises_rather_than_defaulting() -> None:
    """Defaulting to a model would silently bill a typo to the strongest tier."""
    with pytest.raises(UnknownAgentError, match="no model route"):
        role_for_agent("NotAnAgent")


def test_the_expensive_model_is_reserved_for_judgement_calls() -> None:
    """The strongest tier carries judgement, and only judgement.

    `FusionBridge` was named here until Phase 8 and is deliberately not any
    more. It sat on the `reason` tier from Phase 0 on the strength of
    `ARCHITECTURE.md`'s description -- recognising that a predicate's subject is
    an estimate in disguise -- and building it showed that recognition is made
    once, in Phase 4, by `AssumptionExtractor`, which is on this tier and pays
    for it. What Phase 8 does with the answer is a graph walk and two predicate
    evaluations. See ADR 0026, and `test_boundaries.py` for the check that it
    cannot reach a provider at all.
    """
    reasoning = {a for a in routed_agents() if role_for_agent(a) is ModelRole.REASON}

    assert "AssumptionExtractor" in reasoning
    assert "ContradictionDetector" in reasoning
    assert "ChallengerAgent" in reasoning
    assert "SegmenterAgent" not in reasoning
    assert "FusionBridge" not in reasoning
    assert "CollateralAgent" not in reasoning


def test_the_mock_model_id_is_not_a_real_model() -> None:
    """Traces from offline runs must be distinguishable from live ones."""
    assert MOCK_MODEL_ID not in {resolve(role).model_id for role in ModelRole}


def test_cost_of_a_known_call_is_exact() -> None:
    spec = resolve(ModelRole.REASON)  # 5.00 in, 25.00 out

    cost = estimate_cost_usd(spec, input_tokens=1_000_000, output_tokens=100_000)

    assert cost == Decimal("7.5")


def test_a_free_call_costs_nothing() -> None:
    assert estimate_cost_usd(resolve(ModelRole.SCAN), 0, 0) == Decimal(0)


@pytest.mark.parametrize(("inp", "out"), [(-1, 0), (0, -1)])
def test_negative_token_counts_are_rejected(inp: int, out: int) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        estimate_cost_usd(resolve(ModelRole.SCAN), inp, out)


@given(inp=TOKENS, out=TOKENS)
def test_cost_is_never_negative(inp: int, out: int) -> None:
    assert estimate_cost_usd(resolve(ModelRole.EXTRACT), inp, out) >= 0


@given(inp=TOKENS, out=TOKENS)
def test_cost_is_monotonic_in_tokens(inp: int, out: int) -> None:
    spec = resolve(ModelRole.EXTRACT)

    assert estimate_cost_usd(spec, inp + 1, out) > estimate_cost_usd(spec, inp, out)
    assert estimate_cost_usd(spec, inp, out + 1) > estimate_cost_usd(spec, inp, out)


@given(inp=TOKENS, out=TOKENS)
def test_cost_is_additive_across_a_split_call(inp: int, out: int) -> None:
    """Summing per-call costs must equal costing the total, exactly.

    This is the property a float would eventually break, and it is the one the
    eval harness depends on when it reports cost per document.
    """
    spec = resolve(ModelRole.REASON)
    half_in, half_out = inp // 2, out // 2

    split = estimate_cost_usd(spec, half_in, half_out) + estimate_cost_usd(
        spec, inp - half_in, out - half_out
    )

    assert split == estimate_cost_usd(spec, inp, out)


@given(inp=TOKENS, out=TOKENS)
def test_the_cheap_tier_never_costs_more_than_the_expensive_one(inp: int, out: int) -> None:
    scan = estimate_cost_usd(resolve(ModelRole.SCAN), inp, out)
    reason = estimate_cost_usd(resolve(ModelRole.REASON), inp, out)

    assert scan <= reason
