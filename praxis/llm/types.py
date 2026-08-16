"""What a model call is, and what comes back from one.

These types are the vocabulary every agent will speak from Phase 3 onward, so
the shape of them decides what an agent is allowed to think about. Three
choices are deliberate and each removes a whole class of mistake:

- **An agent names itself and its task, never a model.** `LLMRequest.agent`
  routes through `praxis.config.models`, which keeps invariant 2 true by
  construction rather than by review.
- **There is no temperature, `top_p` or `top_k`.** Not an omission: those
  parameters are rejected with a 400 on the models this project routes to.
  Read from the Anthropic migration guide on 2026-08-15:
  https://platform.claude.com/docs/en/about-claude/models/migration-guide
  Determinism therefore comes from the provider — `MockProvider` and
  `ReplayProvider` are functions of the request — and not from a sampling knob
  that no longer exists.
- **Every request can be rendered canonically.** `canonical()` is what the
  replay key hashes and what the trace store records, so a fixture cannot be
  keyed on one rendering and replayed against another.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Self

MAX_TOKENS_WITHOUT_STREAMING: Final = 16_000
"""Above this, a non-streaming request risks an SDK HTTP timeout.

The SDKs refuse a non-streaming request they estimate will outlive the
connection. Phase 2 makes one call per request and never streams, so this is
the ceiling `LLMRequest` enforces; streaming is in `BACKLOG.md` against the
first agent that needs a longer answer. Read from the Anthropic docs on
2026-08-15:
https://platform.claude.com/docs/en/build-with-claude/streaming
"""


class MessageRole(StrEnum):
    """Who is speaking in one turn of a conversation.

    There is no `system` member. A system prompt is a field of the request on
    the Messages API, not a message in the list, and modelling it as one would
    let a caller build a request the API cannot express.
    """

    USER = "user"
    ASSISTANT = "assistant"


class StopReason(StrEnum):
    """Why the model stopped generating.

    The first six are the documented set, read from the Anthropic docs on
    2026-08-15:
    https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
    """

    END_TURN = "end_turn"
    """Finished naturally. The only reason a structured call may trust."""

    MAX_TOKENS = "max_tokens"
    """Hit the output cap. The answer is truncated, so JSON will not parse --
    this is the most common cause of a malformed structured response."""

    STOP_SEQUENCE = "stop_sequence"
    TOOL_USE = "tool_use"
    PAUSE_TURN = "pause_turn"

    REFUSAL = "refusal"
    """Declined on safety grounds. Arrives as a successful HTTP 200 with an
    empty or partial body, which is why every caller checks the stop reason
    before reading the text."""

    OTHER = "other"
    """A reason this build does not know. Recorded rather than raised: a new
    member appearing in the API is not a reason for a run to die, but it is a
    reason for the trace to say so."""

    @classmethod
    def from_api(cls, value: str | None) -> Self:
        """Map an API stop reason onto this enum, tolerating an unknown one."""
        if value is None:
            return cls(cls.OTHER)
        try:
            return cls(value)
        except ValueError:
            return cls(cls.OTHER)


class CallOutcome(StrEnum):
    """How one attempt at a call ended, as the trace store records it."""

    OK = "ok"
    MALFORMED = "malformed"
    """The response arrived but did not satisfy the requested schema."""

    REFUSED = "refused"
    ERROR = "error"
    """The call did not complete: transport, credentials, or a cache miss."""


@dataclass(frozen=True, slots=True)
class Message:
    """One turn of the conversation sent to a model."""

    role: MessageRole
    content: str

    def canonical(self) -> dict[str, str]:
        """Render this turn as the plain data the request hash covers."""
        return {"role": self.role.value, "content": self.content}


@dataclass(frozen=True, slots=True)
class ResponseSchema:
    """The shape a structured call demands of its answer.

    Attributes:
        name: The Pydantic model's name. Recorded on the trace so a run can be
            grouped by what it was asking for, and used by `MockProvider` to
            find the generator that answers this shape.
        json_schema: The schema as the API's `output_config.format` accepts it
            -- already reduced by `praxis.llm.structured`, which drops the
            keywords that dialect does not support.
    """

    name: str
    json_schema: Mapping[str, Any]

    def canonical(self) -> dict[str, Any]:
        """Render this schema as the plain data the request hash covers."""
        return {"name": self.name, "json_schema": dict(self.json_schema)}


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """One call to a model, described in terms an agent can supply.

    Attributes:
        agent: The agent making the call. Routed to a `ModelRole` by
            `praxis.config.models`; an agent in `NON_LLM_AGENTS` raises there.
        task: A stable name for this *kind* of call, e.g.
            `scan_for_decisions`. Not free text: it groups traces, so it must
            be the same string every time the same prompt shape is sent.
        system: The system prompt.
        messages: The conversation, starting with a user turn.
        schema: The response shape, or `None` for a free-text call.
        max_tokens: The output cap.
        attempt: 1 for the first try, 2+ for a repair after malformed output.
            Part of the record, not of the prompt -- the repair itself is a
            message, so the hash changes because the conversation changed.
    """

    agent: str
    task: str
    system: str
    messages: tuple[Message, ...]
    schema: ResponseSchema | None = None
    max_tokens: int = 4_096
    attempt: int = 1
    metadata: Mapping[str, str] = field(default_factory=dict)
    """Anything worth keeping on the trace that is not part of the prompt --
    a span id, a document id. Excluded from the hash on purpose, so a fixture
    recorded while processing one document replays against another."""

    def __post_init__(self) -> None:
        """Reject a request the API would reject, before it costs anything."""
        if not self.messages:
            message = f"{self.agent} sent no messages"
            raise ValueError(message)
        if self.messages[0].role is not MessageRole.USER:
            message = f"the first message must be from the user, got {self.messages[0].role.value}"
            raise ValueError(message)
        if self.max_tokens < 1:
            message = f"max_tokens must be positive, got {self.max_tokens}"
            raise ValueError(message)
        if self.max_tokens > MAX_TOKENS_WITHOUT_STREAMING:
            message = (
                f"max_tokens={self.max_tokens} needs streaming, which this phase does not "
                f"implement; the non-streaming ceiling is {MAX_TOKENS_WITHOUT_STREAMING}"
            )
            raise ValueError(message)
        if self.attempt < 1:
            message = f"attempts are numbered from 1, got {self.attempt}"
            raise ValueError(message)

    @property
    def prompt_text(self) -> str:
        """Every word the model will read.

        What the cost estimate is measured over, since the API bills the system
        prompt like any other input.
        """
        return "\n".join([self.system, *(message.content for message in self.messages)])

    @property
    def source_text(self) -> str:
        """The material the answer is supposed to be *about*, without the brief.

        The system prompt is instruction, not evidence. `MockProvider` quotes
        this rather than `prompt_text` because a quotation lifted from the
        instructions cites nothing that exists in any document -- every offline
        citation would then fail `VerifierAgent` the same way, which is a
        systematic bias rather than a realistic failure, and it would hide the
        near-miss citations the mock exists to produce.
        """
        return "\n".join(message.content for message in self.messages)

    def canonical(self) -> dict[str, Any]:
        """Render the request as the plain data that identifies it.

        This is the input to the replay key and the body of the trace's
        `request_json`. `metadata` and `attempt` are excluded: neither reaches
        the model, so including them would key otherwise identical calls to
        different fixtures.
        """
        return {
            "agent": self.agent,
            "task": self.task,
            "system": self.system,
            "messages": [message.canonical() for message in self.messages],
            "schema": None if self.schema is None else self.schema.canonical(),
            "max_tokens": self.max_tokens,
        }

    def with_messages(self, messages: Sequence[Message], *, attempt: int) -> LLMRequest:
        """Return the same request carrying a continued conversation.

        Used by the repair loop, which appends the malformed answer and the
        validation error and asks again.
        """
        return LLMRequest(
            agent=self.agent,
            task=self.task,
            system=self.system,
            messages=tuple(messages),
            schema=self.schema,
            max_tokens=self.max_tokens,
            attempt=attempt,
            metadata=self.metadata,
        )


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """What one call consumed.

    Cache fields are carried because the Anthropic response reports them and
    they are priced differently from ordinary input; Phase 2 sends no
    `cache_control`, so they are zero until something does. Read from the
    Anthropic docs on 2026-08-15:
    https://platform.claude.com/docs/en/build-with-claude/prompt-caching
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __post_init__(self) -> None:
        """Reject a count that cannot be a count."""
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = getattr(self, name)
            if value < 0:
                message = f"{name} must be non-negative, got {value}"
                raise ValueError(message)

    @property
    def total(self) -> int:
        """Every token the call was billed for, cached or not."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """Sum two calls' usage, so a run can report one figure."""
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
        )


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """What one call returned, before anything tries to parse it.

    Held as text rather than as a parsed object because the trace records what
    the model actually said. A response that failed to satisfy its schema is
    the most interesting row in the trace store, and it only exists if the raw
    text survived the failure.
    """

    text: str
    model_id: str
    usage: TokenUsage
    stop_reason: StopReason

    @property
    def is_refusal(self) -> bool:
        """Whether the model declined rather than answered."""
        return self.stop_reason is StopReason.REFUSAL

    @property
    def is_truncated(self) -> bool:
        """Whether the answer stops mid-sentence because it hit the cap."""
        return self.stop_reason is StopReason.MAX_TOKENS
