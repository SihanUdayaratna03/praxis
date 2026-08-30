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

`input_usd_per_mtok` is the *base* input rate: what an uncached token costs.
Cached tokens are billed off that same base by a multiplier rather than by a
separate per-model price, which is why the two multipliers below are model
independent and why the registry carries one input figure and not three.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

TOKENS_PER_MILLION: Final = Decimal(1_000_000)

# Prompt caching is priced as a multiple of a model's base input rate, the same
# multiple for every model, so it belongs here as a constant rather than as a
# third and fourth price column on ModelSpec. Read from the Anthropic docs on
# 2026-08-16, not recalled:
# https://platform.claude.com/docs/en/build-with-claude/prompt-caching
CACHE_READ_MULTIPLIER: Final = Decimal("0.1")
"""A cache hit costs a tenth of an ordinary input token."""

CACHE_WRITE_MULTIPLIER: Final = Decimal("1.25")
"""Writing the 5-minute cache costs a quarter more than sending the tokens
plainly. Praxis sends no `cache_control` yet, so this prices zero tokens today;
it is here so that the first agent to turn caching on cannot quietly make the
eval harness's cost column wrong."""

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
        "CalibratorAgent",
        "FusionBridge",
        "ScoringAgent",
        "SourceAdapter",
        "VerifierAgent",
    }
)
"""`FusionBridge` joined this set in Phase 8 and `CalibratorAgent` in Phase 7;
neither started here.

`FusionBridge` is the more surprising of the two, because `ARCHITECTURE.md`
describes its job as *recognising* that a predicate's subject is an estimate in
disguise -- which sounds like judgement, and is. The judgement is real and it is
already paid for: `AssumptionExtractor` makes it in Phase 4, in a `reason`-tier
call that is already holding the assumption and its quantity, and writes the
`estimated_as` edge recording the answer (ADR 0016). What is left for Phase 8 is
a graph walk over those edges, one indexed read per group, a multiplication and
two predicate evaluations -- and ADR 0019 already forbids a model the second of
those, since only arithmetic can reach `BREACHED`. See ADR 0026; ADR 0006's
assignment list carries the amendment.

`CalibratorAgent` was assigned to `extract` in Phase 2, on the reasonable-looking
assumption that explaining a correction in plain language is a writing task.
Building it showed the explanation is a template over numbers it was handed: the
factor, the band, `n` and the confidence all exist before the sentence does, and
asking a model to restate them adds nothing but the possibility of a restated
number disagreeing with the number it came from. See ADR 0025; ADR 0006's
assignment list carries the amendment.
"""


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
        # Fusion. `FusionBridge` was assigned `reason` here in Phase 0 and left
        # this table in Phase 8, once building it showed the judgement its route
        # was paying for had already been made in Phase 4. See ADR 0026.
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
