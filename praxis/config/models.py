"""The single source of truth for model identifiers, prices and routing.

No model ID may appear anywhere else in the codebase. Two reasons, both
practical: a model deprecation should be a one-file change, and the cost
accounting in the trace store has to be able to price any call it sees without
a lookup table that can drift from the one the caller used.

Model IDs, context windows and prices below were read from the Anthropic model
reference on 2026-08-09 (cached upstream 2026-06-24), not recalled:
https://platform.claude.com/docs/en/about-claude/models/overview
https://platform.claude.com/docs/en/pricing

Prices are USD per million tokens and are held as Decimal, never float. Cost is
money; binary floating point is the wrong representation for it, and these
figures are summed over thousands of calls in the eval harness.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

TOKENS_PER_MILLION: Final = Decimal(1_000_000)

# MockProvider records this in traces so that every LLM call in the store has a
# model field, offline runs included. It is not a real model and never reaches
# the network.
MOCK_MODEL_ID: Final = "mock-deterministic-v1"


class ModelRole(StrEnum):
    """What a call is for, rather than which model happens to serve it.

    Agents ask for a role. Swapping the model behind a role is then a one-line
    change here instead of a search across the agent implementations.
    """

    SCAN = "scan"
    """High-recall, low-precision passes over raw text. Cheapest model."""

    EXTRACT = "extract"
    """Structured extraction against a schema. Mid model."""

    REASON = "reason"
    """Judgement calls the whole product rests on. Strongest model."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """An immutable description of one model as Praxis uses it."""

    model_id: str
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    context_tokens: int
    max_output_tokens: int
    rationale: str


_REGISTRY: Final[Mapping[ModelRole, ModelSpec]] = MappingProxyType(
    {
        ModelRole.SCAN: ModelSpec(
            model_id="claude-haiku-4-5",
            input_usd_per_mtok=Decimal("1.00"),
            output_usd_per_mtok=Decimal("5.00"),
            context_tokens=200_000,
            max_output_tokens=64_000,
            rationale=(
                "Scanning runs once per span and is the highest-volume call in "
                "the pipeline. Recall is what matters; precision is recovered "
                "downstream, so the cheapest capable model wins on cost per "
                "document, which is a reported eval metric."
            ),
        ),
        ModelRole.EXTRACT: ModelSpec(
            model_id="claude-sonnet-5",
            input_usd_per_mtok=Decimal("3.00"),
            output_usd_per_mtok=Decimal("15.00"),
            context_tokens=1_000_000,
            max_output_tokens=128_000,
            rationale=(
                "Filling a schema from a span that has already been identified "
                "as relevant. Needs reliable structured output and citation "
                "discipline, not deep reasoning. Introductory pricing of "
                "2.00/10.00 applies through 2026-08-31; the standard rate is "
                "recorded here so cost estimates never flatter themselves."
            ),
        ),
        ModelRole.REASON: ModelSpec(
            model_id="claude-opus-5",
            input_usd_per_mtok=Decimal("5.00"),
            output_usd_per_mtok=Decimal("25.00"),
            context_tokens=1_000_000,
            max_output_tokens=128_000,
            rationale=(
                "Implicit assumptions, contradictions, the fusion link between "
                "an assumption and an estimate, and the adversarial challenge. "
                "These are the outputs a judge will read, and a wrong answer "
                "here is worse than an expensive one."
            ),
        ),
    }
)


# Agents whose output is computed, not generated. They must never acquire an
# LLM call: their determinism is what makes the metrics reproducible and the
# property tests meaningful.
NON_LLM_AGENTS: Final[frozenset[str]] = frozenset(
    {
        "BiasDetective",
        "ScoringAgent",
        "SourceAdapter",
        "VerifierAgent",
    }
)


_ROUTING: Final[Mapping[str, ModelRole]] = MappingProxyType(
    {
        # Ingestion
        "SegmenterAgent": ModelRole.SCAN,
        # Half A -- provenance
        "DecisionScout": ModelRole.SCAN,
        "DecisionStructurer": ModelRole.EXTRACT,
        "AssumptionExtractor": ModelRole.REASON,
        "AssumptionFormalizer": ModelRole.REASON,
        "AssumptionMonitor": ModelRole.EXTRACT,
        "ContradictionDetector": ModelRole.REASON,
        "ArchaeologistAgent": ModelRole.REASON,
        # Half B -- calibration
        "EstimateExtractor": ModelRole.SCAN,
        "WorkClassifier": ModelRole.SCAN,
        "OutcomeMatcher": ModelRole.EXTRACT,
        "CalibratorAgent": ModelRole.EXTRACT,
        # Fusion
        "FusionBridge": ModelRole.REASON,
        "CollateralAgent": ModelRole.REASON,
        "ReviewTriageAgent": ModelRole.EXTRACT,
        # Cross-cutting
        "ChallengerAgent": ModelRole.REASON,
        "CuratorAgent": ModelRole.EXTRACT,
        "AbstentionGate": ModelRole.EXTRACT,
        "ReporterAgent": ModelRole.EXTRACT,
    }
)


class UnknownAgentError(KeyError):
    """Raised when an agent asks for a route it was never assigned."""


def resolve(role: ModelRole) -> ModelSpec:
    """Return the model currently serving a role."""
    return _REGISTRY[role]


def role_for_agent(agent_name: str) -> ModelRole:
    """Return the role an agent's calls are routed to.

    Raises:
        UnknownAgentError: if the agent is deterministic, or simply not in the
            routing table. Both cases are bugs worth failing loudly on: a
            deterministic agent reaching for a model is exactly the drift the
            NON_LLM_AGENTS set exists to catch.
    """
    if agent_name in NON_LLM_AGENTS:
        message = (
            f"{agent_name} is deterministic by design and must not make an LLM call. "
            f"See docs/adr/0006-model-routing-table.md."
        )
        raise UnknownAgentError(message)
    try:
        return _ROUTING[agent_name]
    except KeyError as exc:
        message = f"{agent_name} has no model route. Add one to praxis/config/models.py."
        raise UnknownAgentError(message) from exc


def estimate_cost_usd(spec: ModelSpec, input_tokens: int, output_tokens: int) -> Decimal:
    """Price one call exactly, in dollars.

    Deliberately not rounded: costs are summed across a whole eval run before
    anyone looks at them, and rounding per call would accumulate visible error
    over thousands of calls.
    """
    if input_tokens < 0 or output_tokens < 0:
        message = f"token counts must be non-negative, got {input_tokens} and {output_tokens}"
        raise ValueError(message)
    inputs = spec.input_usd_per_mtok * Decimal(input_tokens)
    outputs = spec.output_usd_per_mtok * Decimal(output_tokens)
    return (inputs + outputs) / TOKENS_PER_MILLION


def routed_agents() -> frozenset[str]:
    """Every agent that is allowed to make an LLM call."""
    return frozenset(_ROUTING)
