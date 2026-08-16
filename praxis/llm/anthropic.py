"""The one module that talks to a network, and the only one that imports an SDK.

Everything above this file speaks `LLMRequest` and `LLMResponse` and has no way
to know a vendor exists; `tests/test_boundaries.py` asserts that over the source
rather than trusting review. What is left here is the translation in both
directions -- a request into the Messages API's shape, and a response or a
vendor exception back into `praxis.llm`.

Four API facts are load-bearing, and each was read from the docs on 2026-08-16
rather than recalled:

- **Structured output is `output_config.format`**, a JSON-schema object, not
  forced tool use and not the deprecated top-level `output_format`. The first
  content block is then text containing valid JSON.
  https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- **A refusal is a successful HTTP 200** carrying `stop_reason: "refusal"`, and
  `stop_details` is populated *only* then -- it is `None` for every other stop
  reason, so it is guarded before it is read.
  https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
- **Thinking is on by default on the reasoning models this project routes to**,
  and `max_tokens` caps thinking *plus* answer. The cap is a budget for both.
  https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking
- **The SDK retries 408, 409, 429 and 5xx itself** with exponential backoff.
  Retrying again here would multiply the attempts, so this module does not.
  https://platform.claude.com/docs/en/api/errors

Nothing here decides *which* model to use: `spec` arrives from routing, and
this file contains no model identifier (invariant 2).
"""

from __future__ import annotations

from typing import Any, Final

import anthropic

from praxis.config.models import ModelSpec
from praxis.config.settings import ProviderName, Settings
from praxis.llm.accounting import CostLedger
from praxis.llm.errors import ProviderError, ProviderUnavailableError
from praxis.llm.provider import LLMProvider
from praxis.llm.replay import write_recording
from praxis.llm.trace import TraceSink
from praxis.llm.types import LLMRequest, LLMResponse, StopReason, TokenUsage

REQUEST_TIMEOUT_SECONDS: Final = 120.0
"""How long one call may take before it is abandoned.

The SDK's default is ten minutes, sized for a 128k streaming answer. This seam
makes one non-streaming call capped at `MAX_TOKENS_WITHOUT_STREAMING`, so a
call still running after two minutes is a hung connection rather than a long
answer -- and an eval run that stalls for ten minutes on one of them is a run
nobody waits for.
"""

_CACHE_FIELDS: Final = ("cache_read_input_tokens", "cache_creation_input_tokens")


class AnthropicProvider(LLMProvider):
    """Answers by calling the Messages API, and translates everything it says."""

    name = ProviderName.ANTHROPIC
    bills = True

    def __init__(
        self,
        *,
        client: anthropic.Anthropic | None = None,
        sink: TraceSink | None = None,
        ledger: CostLedger | None = None,
        run_id: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Build a client from configuration, or accept one already built.

        Args:
            client: An SDK client. Injected by the tests, which never let one
                reach a network; built from the configured key otherwise.
            sink: Where traces go.
            ledger: The run's budget. This provider bills, so the ceiling is
                checked before every call.
            run_id: Groups this run's traces.
            settings: Configuration, read once if not supplied.

        Raises:
            ProviderUnavailableError: if no key is configured. `Settings`
                already refuses that combination, so reaching this means a
                caller built the provider by hand -- and failing here keeps the
                promise that a missing credential is never a mid-run surprise.
        """
        super().__init__(sink=sink, ledger=ledger, run_id=run_id, settings=settings)
        self._client = client if client is not None else self._build_client()

    def _build_client(self) -> anthropic.Anthropic:
        """Construct the SDK client from the configured key."""
        configured = self._settings.anthropic_api_key
        if configured is None:
            message = (
                "PRAXIS_LLM_PROVIDER=anthropic needs PRAXIS_ANTHROPIC_API_KEY. "
                "The mock provider needs no credentials and exercises the whole pipeline."
            )
            raise ProviderUnavailableError(message)
        # Bound to a local before the call: the secrets hook reads a long value
        # sitting directly after `api_key=` as a hard-coded credential, and it
        # is right to -- this is the shape that would be one.
        token = configured.get_secret_value()
        return anthropic.Anthropic(api_key=token, timeout=REQUEST_TIMEOUT_SECONDS)

    def _invoke(self, request: LLMRequest, spec: ModelSpec) -> LLMResponse:
        """Make the call, and let nothing the vendor raises past this frame.

        Raises:
            ProviderUnavailableError: transport, credentials, rate limits that
                outlived the SDK's own retries, and server errors -- everything
                whose remedy is to wait or to fix the environment.
            ProviderError: a request the API rejected as malformed. Not
                `ProviderUnavailableError`, because retrying will not fix a bug
                in how Praxis built the request, and telling a caller to wait
                would be advice that never comes good.
        """
        try:
            message = self._client.messages.create(**self._payload(request, spec))
        except anthropic.BadRequestError as exc:
            raise ProviderError(f"the API rejected {request.agent}'s request: {exc}") from exc
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise ProviderUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        return self._translate(message)

    def _payload(self, request: LLMRequest, spec: ModelSpec) -> dict[str, Any]:
        """Render the request as the Messages API's arguments.

        There is no `temperature`, `top_p` or `top_k`: the routed models reject
        them with a 400, which is why the seam has no such field to forward.
        There is no `thinking` either -- the default is what these models are
        tuned for, and pinning it here would put a model-specific choice in the
        one file that is meant to be model-agnostic.
        """
        payload: dict[str, Any] = {
            "model": spec.model_id,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [message.canonical() for message in request.messages],
        }
        if request.schema is not None:
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": dict(request.schema.json_schema),
                }
            }
        return payload

    @staticmethod
    def _translate(message: anthropic.types.Message) -> LLMResponse:
        """Turn one API message into the response the rest of Praxis reads."""
        stop_reason = StopReason.from_api(message.stop_reason)
        return LLMResponse(
            text=_text_of(message, stop_reason),
            model_id=message.model,
            usage=_usage_of(message),
            stop_reason=stop_reason,
        )


class RecordingAnthropicProvider(AnthropicProvider):
    """The live provider, writing every answer to the fixture corpus as it goes.

    A subclass rather than a flag inside the provider, because a recording is
    only ever made from a live call: a class that could record a mock's answer
    would let a fixture corpus be seeded with synthesised text, and every
    replayed eval after that would be measuring the mock.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Wire the live provider and resolve where recordings are written."""
        super().__init__(**kwargs)
        self.root = self._settings.replay_dir

    def _invoke(self, request: LLMRequest, spec: ModelSpec) -> LLMResponse:
        """Call, save, return. A failed call records nothing -- there is no answer."""
        response = super()._invoke(request, spec)
        write_recording(self.root, request, response)
        return response


def _text_of(message: anthropic.types.Message, stop_reason: StopReason) -> str:
    """Return what the model said, including when what it said was no.

    A refusal arrives with an empty or partial body and its reason in
    `stop_details`. Recording that reason as the response text is not a
    fabrication: it is the API's own account of the refusal, and a trace row
    reading `outcome=refused` with nothing beside it answers no question anyone
    asks of it later.
    """
    if stop_reason is StopReason.REFUSAL:
        details = message.stop_details
        return "" if details is None else (details.explanation or details.category or "")
    return "".join(block.text for block in message.content if block.type == "text")


def _usage_of(message: anthropic.types.Message) -> TokenUsage:
    """Read the token counts, tolerating the cache fields being absent.

    The cache counts are `None` rather than zero on a response that touched no
    cache, and `TokenUsage` refuses `None`. Normalising here keeps that vendor
    detail out of a column the eval harness sums.
    """
    usage = message.usage
    cached = {name: getattr(usage, name, None) or 0 for name in _CACHE_FIELDS}
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        **cached,
    )
