"""What a call cost, and what a run has spent so far.

Cost is a reported eval metric and a column a judge will read, so the arithmetic
lives in deterministic code with the rest of the statistics rather than
anywhere near a provider implementation. Three consequences shape this module:

- **Every figure is a `Decimal`** (invariant 4) and nothing is rounded. Per-call
  rounding accumulates visible error over an eval run of thousands of calls,
  and the sum is what gets reported.
- **Prices come from `praxis.config.models`, never from here.** This module
  knows how to combine rates; it holds none of them, which is invariant 2 for
  money instead of for model ids.
- **The ceiling is checked before a call, not after.** A limit enforced
  afterwards is a report, and the point of `PRAXIS_COST_CEILING_USD` is that a
  runaway agent loop costs an error message rather than a bill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from praxis.config.models import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    TOKENS_PER_MILLION,
    ModelSpec,
    estimate_cost_usd,
)
from praxis.config.settings import Settings
from praxis.llm.errors import CostCeilingExceededError
from praxis.llm.types import LLMRequest, TokenUsage

ZERO_USD: Final = Decimal("0")

CHARS_PER_TOKEN_FLOOR: Final = 2
"""How few characters a token is assumed to be when pricing a call in advance.

Not an API fact and not a tokenizer: counting a prompt's tokens exactly needs a
round trip, and a ceiling that has to make a network call to decide whether it
can afford a network call is not a ceiling. Two characters per token
over-counts ordinary English by roughly a factor of two, which is the direction
a spending limit should be wrong in.
"""


def cost_of(spec: ModelSpec, usage: TokenUsage) -> Decimal:
    """Price one completed call in dollars, cache tokens included.

    Cached input is billed off the model's base input rate by the multipliers
    in `praxis.config.models`, so a call that read a warm cache is cheaper than
    the same call cold and one that wrote the cache is dearer.

    Args:
        spec: The model that served the call.
        usage: What the response reported consuming.

    Returns:
        The exact cost in USD, unrounded.
    """
    uncached = estimate_cost_usd(spec, usage.input_tokens, usage.output_tokens)
    cached_tokens = CACHE_READ_MULTIPLIER * Decimal(
        usage.cache_read_input_tokens
    ) + CACHE_WRITE_MULTIPLIER * Decimal(usage.cache_creation_input_tokens)
    return uncached + (spec.input_usd_per_mtok * cached_tokens) / TOKENS_PER_MILLION


def upper_bound_cost(spec: ModelSpec, request: LLMRequest) -> Decimal:
    """Price the most a call could possibly cost before making it.

    The output side is exact: `max_tokens` is a cap the API enforces. The input
    side is the pessimistic character estimate above, and cache tokens are
    ignored because they can only make the real figure smaller.

    Args:
        spec: The model that would serve the call.
        request: The call being considered.

    Returns:
        A figure the actual cost will not exceed.
    """
    input_tokens = -(-len(request.prompt_text) // CHARS_PER_TOKEN_FLOOR)
    return estimate_cost_usd(spec, input_tokens, request.max_tokens)


@dataclass(slots=True)
class CostLedger:
    """What one run has spent, and whether it may spend more.

    Deliberately mutable and deliberately not global: an eval run, a CLI
    invocation and a test each hold their own, so a ceiling reached in one
    cannot leak into another and make a suite order-dependent.

    Attributes:
        ceiling_usd: The limit this run must not pass.
        spent_usd: What has been recorded so far.
        calls: How many calls have been recorded.
        usage: Every recorded call's tokens, summed.
    """

    ceiling_usd: Decimal
    spent_usd: Decimal = ZERO_USD
    calls: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)

    def __post_init__(self) -> None:
        """Reject a ceiling that would stop the run before it started."""
        if self.ceiling_usd <= ZERO_USD:
            message = f"the cost ceiling must be positive, got {self.ceiling_usd}"
            raise ValueError(message)

    @classmethod
    def from_settings(cls, settings: Settings) -> CostLedger:
        """Build a ledger from the configured ceiling.

        The setting is a float because it arrives from the environment through
        pydantic; it becomes a `Decimal` here, via `str`, and every figure
        downstream of this line is exact.
        """
        return cls(ceiling_usd=Decimal(str(settings.cost_ceiling_usd)))

    @property
    def remaining_usd(self) -> Decimal:
        """What is left to spend. Never negative."""
        return max(ZERO_USD, self.ceiling_usd - self.spent_usd)

    def check_affordable(self, spec: ModelSpec, request: LLMRequest) -> None:
        """Raise unless this run can afford the call at its worst.

        Args:
            spec: The model that would serve the call.
            request: The call being considered.

        Raises:
            CostCeilingExceededError: if the worst case would pass the ceiling.
                The run stops here rather than after the money is gone.
        """
        if self.spent_usd + upper_bound_cost(spec, request) > self.ceiling_usd:
            raise CostCeilingExceededError(self.spent_usd, self.ceiling_usd)

    def record(self, spec: ModelSpec, usage: TokenUsage) -> Decimal:
        """Add a completed call to the running total.

        Args:
            spec: The model that served the call.
            usage: What the response reported consuming.

        Returns:
            What this one call cost, for the trace row.
        """
        cost = cost_of(spec, usage)
        self.spent_usd += cost
        self.calls += 1
        self.usage += usage
        return cost

    def record_free(self, usage: TokenUsage) -> Decimal:
        """Add a call that cost nothing -- a mock or a replayed fixture.

        Offline calls still count tokens, because the eval harness reports what
        a run *would* have cost and a mock run with no token counts at all
        would make that column unavailable offline.
        """
        self.calls += 1
        self.usage += usage
        return ZERO_USD
