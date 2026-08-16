"""The provider seam's exception vocabulary, and where a vendor SDK stops.

`praxis.store.errors` makes the same argument about `sqlite3`: a boundary that
holds for the query path and leaks on the failure path is not a boundary. A
caller forced to write `except anthropic.RateLimitError` has imported the SDK
as surely as one that builds a request, so every vendor exception is
translated into a class below before it leaves `praxis.llm`.

The distinctions drawn here are the ones a caller acts on differently: retry
the same call, repair the prompt and ask again, record a fixture, stop the run,
or fix a bug. Two failures that call for the same response do not get two
classes.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path


class ProviderError(Exception):
    """Base class for every failure raised out of `praxis.llm`.

    Catching this catches the whole model layer without naming a vendor, which
    is the entire point of the module.
    """


class ProviderUnavailableError(ProviderError):
    """Raised when the model could not be reached or would not authenticate.

    Network failure, a rejected key, a rate limit that outlasted its retries, a
    server error. Grouped because the response is the same for all of them:
    none is repairable by changing the prompt, and none is Praxis's bug.
    """


class ProviderRefusalError(ProviderError):
    """Raised when the model declined the request on safety grounds.

    A refusal arrives as a successful response with `stop_reason: refusal`, so
    it is not a transport failure and retrying the same prompt will not fix it.
    It is a class of its own because it is the one failure where the right
    answer may be to route the call elsewhere rather than to try again.

    Attributes:
        agent: The agent whose call was declined.
        category: The policy category the API reported, when it reported one.
    """

    def __init__(self, agent: str, category: str | None = None) -> None:
        """Record who was refused and why.

        Args:
            agent: The agent whose call was declined.
            category: The refusal category from the API, if present.
        """
        self.agent = agent
        self.category = category
        reason = "" if category is None else f" ({category})"
        super().__init__(f"the model declined {agent}'s request{reason}")


class ReplayCacheMissError(ProviderError):
    """Raised when `ReplayProvider` holds no recording for a request.

    Loud on purpose, and the reason the replay provider exists in this shape.
    A provider that fell back to the network on a miss would make an eval run
    silently non-reproducible: the numbers would depend on which fixtures
    happened to be present, which is the failure replay is meant to rule out.

    A fixture that exists but cannot be used -- corrupt, edited since it was
    recorded, or answered by a different model -- raises this too. It is one
    class rather than four because the response to all of them is the same:
    record it again. `detail` says which it was, so the message stays true.

    Attributes:
        prompt_hash: The key that was looked up.
        agent: The agent whose call missed.
        path: Where the fixture was expected.
        detail: Why the recording was unusable, when one was found at all.
    """

    def __init__(self, prompt_hash: str, agent: str, path: Path, detail: str | None = None) -> None:
        """Record the key, the caller, the path and what was wrong.

        Args:
            prompt_hash: The replay key that matched no usable fixture.
            agent: The agent whose call missed.
            path: The fixture file that does not exist, or cannot be used.
            detail: Why an existing fixture was rejected. Omitted when the file
                simply is not there.
        """
        self.prompt_hash = prompt_hash
        self.agent = agent
        self.path = path
        self.detail = detail
        problem = "no recorded response" if detail is None else detail
        super().__init__(
            f"{problem} for {agent} at {prompt_hash} ({path}); "
            f"record it with PRAXIS_LLM_PROVIDER=anthropic and "
            f"PRAXIS_RECORD_REPLAY=true, or use the mock provider"
        )


class MalformedOutputError(ProviderError):
    """Raised when a structured call never produced output matching its schema.

    Raised only after the repair loop is exhausted -- a single malformed answer
    is an attempt, not a failure. The last raw text is carried because it is
    the only evidence of what went wrong, and by the time this reaches a caller
    the trace store already holds one row per attempt.

    Attributes:
        schema_name: The response model that was asked for.
        attempts: How many attempts were made in total.
        detail: The validation error from the final attempt.
        raw: The final attempt's raw text.
    """

    def __init__(self, schema_name: str, attempts: int, detail: str, raw: str) -> None:
        """Record the schema, the attempt count and the last failure.

        Args:
            schema_name: The response model that was asked for.
            attempts: Total attempts, including the first.
            detail: The validation error from the final attempt.
            raw: The final attempt's raw text.
        """
        self.schema_name = schema_name
        self.attempts = attempts
        self.detail = detail
        self.raw = raw
        super().__init__(f"no valid {schema_name} after {attempts} attempts; last error: {detail}")


class SchemaNotSupportedError(ProviderError):
    """Raised when a response model cannot be expressed as a JSON schema.

    The structured-output dialect is a subset of JSON Schema -- no recursion,
    among other things -- so a model that cannot be reduced to it must fail
    when it is defined rather than on the first live call.
    """


class CostCeilingExceededError(ProviderError):
    """Raised when a run would spend past `PRAXIS_COST_CEILING_USD`.

    The owner of this project is paying for it personally, so a runaway agent
    loop should cost an error message rather than a bill. Checked *before* a
    call is made, not after: a ceiling enforced after the fact is a report.

    Attributes:
        spent: What the run has already cost.
        ceiling: The configured limit.
    """

    def __init__(self, spent: Decimal, ceiling: Decimal) -> None:
        """Record the running total and the limit it would have passed.

        Args:
            spent: What the run has already cost, in USD.
            ceiling: The configured limit, in USD.
        """
        self.spent = spent
        self.ceiling = ceiling
        super().__init__(
            f"this run has spent ${spent} against a ceiling of ${ceiling}; "
            f"raise PRAXIS_COST_CEILING_USD or use the mock provider"
        )
