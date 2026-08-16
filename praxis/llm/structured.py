"""Asking a model for a shape, and asking again when it answers badly.

Phase 2 built the seam and deliberately stopped short of this layer:
`ResponseSchema` took a schema the caller had already reduced, and
`MalformedOutputError` sat in the vocabulary with nothing raising it, because
the repair loop's shape is decided by what agents actually get back and writing
it earlier would have been guessing at a failure distribution nobody had
measured. `SegmenterAgent` is the first agent, so this is where it lands.

Two halves.

**Reduction.** `schema_for` turns a Pydantic model into the dialect the API's
`output_config.format` accepts. That dialect is a *subset* of JSON Schema, and
the subtraction is the whole job: numerical bounds, string bounds and most
array bounds are not supported, `additionalProperties` must be exactly `false`,
and recursion is not allowed at all. Read from the Anthropic docs on
2026-08-16, not recalled -- including the documented list of transformations
the official SDKs apply, which is what this mirrors:
https://platform.claude.com/docs/en/build-with-claude/structured-outputs

An unsupported keyword is a 400, not a silently ignored field, so reduction is
required rather than tidy. A stripped constraint is not lost either: it is
appended to the field's `description` as a sentence, and **the Pydantic model
remains the only validator**. Those four steps -- strip, describe, close every
object, validate locally against the original -- are deliberately the same ones
the official SDKs perform, which is where they were read from rather than
invented.

The consequence worth stating: a bound the API cannot enforce is enforced by
this module and not by the model, so a response model whose validity depends on
a numeric range should expect that range to be *asked for* in prose and
*checked* in Python. `MockProvider` synthesises from the reduced schema, so it
is subject to the same asymmetry -- see `tests/llm/test_structured.py`, which
pins the pairing for the shapes this project actually uses.

**Repair.** One malformed answer is an attempt, not a failure. The loop appends
the bad answer and the validation error to the conversation and asks again, up
to `REPAIR_ATTEMPTS`. Each attempt is a separate request with its own attempt
number, so the trace store holds one row per attempt and a run of them is
visible as what it is rather than as a single slow call.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from praxis.llm.errors import MalformedOutputError, SchemaNotSupportedError
from praxis.llm.provider import LLMProvider
from praxis.llm.types import LLMRequest, LLMResponse, Message, MessageRole, ResponseSchema
from praxis.obs.logging import get_logger

_log = get_logger(__name__)

REPAIR_ATTEMPTS: Final = 3
"""Total attempts, first included, before a structured call gives up.

Three rather than more because the failure this repairs is nearly always
truncation or a near-miss field name, and both are fixed on the second try or
not at all. A longer loop mostly buys a longer bill.
"""

MAX_REPAIR_QUOTE_CHARS: Final = 2_000
"""How much of a bad answer is quoted back in the repair prompt.

A truncated answer can be the entire output cap, and echoing all of it doubles
the input of every retry to restate something the model just said.
"""

_EMPTY_ANSWER: Final = "(the previous turn produced no text)"
"""Stands in for an empty answer in the repaired conversation.

An assistant turn with no content is not a request the API will accept, and a
refusal has already been raised by the seam before this module sees it -- so
this covers only the case of a call that returned nothing at all.
"""

_UNSUPPORTED: Final[Mapping[str, str]] = {
    "minimum": "at least {value}",
    "maximum": "at most {value}",
    "exclusiveMinimum": "greater than {value}",
    "exclusiveMaximum": "less than {value}",
    "multipleOf": "a multiple of {value}",
    "minLength": "at least {value} characters",
    "maxLength": "at most {value} characters",
    "pattern": "matching the pattern {value}",
    "maxItems": "at most {value} entries",
    "uniqueItems": "without repeats",
}
"""Keywords the dialect does not support, and how to say each one in prose.

`pattern` is not named in the documented supported set. It is stripped rather
than risked, on the same reasoning as the bounds: an unsupported keyword is a
rejected request, while a sentence in a description costs a few tokens and the
Pydantic model enforces the real rule either way.
"""

_SUPPORTED_MIN_ITEMS: Final = (0, 1)
"""The only two `minItems` values the dialect accepts."""

_SUPPORTED_FORMATS: Final = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)
"""String formats the dialect understands, read from the same page.

Anything else -- Pydantic emits `path` and `binary` among others -- is dropped
rather than sent, because `format` is an annotation everywhere else in JSON
Schema and a rejected request over an annotation would be an expensive way to
learn that.
"""

_CHILD_KEYS: Final = ("properties", "$defs", "definitions")
_BRANCH_KEYS: Final = ("anyOf", "allOf", "oneOf", "prefixItems")


@dataclass(frozen=True, slots=True)
class StructuredResult[T: BaseModel]:
    """A parsed answer, and what it took to get one.

    Attributes:
        value: The validated model.
        attempts: How many calls were made, the first included. Anything above
            one is worth reporting: it is the number ADR 0005's assumption 3 is
            about, and offline it is entirely a property of the mock.
        response: The raw response the accepted answer came from.
    """

    value: T
    attempts: int
    response: LLMResponse


def schema_for(answer: type[BaseModel]) -> ResponseSchema:
    """Reduce a Pydantic model to the schema dialect the API accepts.

    Args:
        answer: The response model.

    Returns:
        A `ResponseSchema` named after the model, carrying the reduced schema.

    Raises:
        SchemaNotSupportedError: if the model is recursive, references anything
            external, or has a field the dialect cannot express -- an
            open-ended mapping, say. Raised when the model is first asked for
            rather than on the first live call, because a schema that cannot be
            sent is a bug in the agent and not a runtime condition.
    """
    raw = answer.model_json_schema()
    _reject_recursion(raw, answer.__name__)
    return ResponseSchema(name=answer.__name__, json_schema=_reduce(raw))


def ask_for[T: BaseModel](
    provider: LLMProvider,
    request: LLMRequest,
    answer: type[T],
    *,
    max_attempts: int = REPAIR_ATTEMPTS,
) -> StructuredResult[T]:
    """Make a structured call, repairing a malformed answer up to a limit.

    Args:
        provider: The seam. Chosen by `provider_for`, never named by an agent.
        request: What to ask. Its schema is filled in from `answer` when the
            caller left it unset, which is the ordinary case.
        answer: The response model, and the only thing that decides whether an
            answer was acceptable.
        max_attempts: Total attempts, the first included.

    Returns:
        The validated answer and the number of attempts it cost.

    Raises:
        MalformedOutputError: if no attempt produced a valid answer.
        ProviderRefusalError: if the model declined. Not repaired -- asking a
            refusing model the same question again is not a repair strategy.
        ProviderError: anything else the seam raises.
        ValueError: if the request carries a schema for a different model.
    """
    current = _with_schema(request, answer)
    detail = ""
    raw = ""
    for attempt in range(1, max_attempts + 1):
        response = provider.complete(current)
        raw = response.text
        parsed = _parse(raw, answer)
        if isinstance(parsed, str):
            detail = parsed
        else:
            return StructuredResult(value=parsed, attempts=attempt, response=response)
        _log.info(
            "structured_output_rejected",
            agent=current.agent,
            task=current.task,
            schema=answer.__name__,
            attempt=attempt,
            truncated=response.is_truncated,
            detail=detail,
        )
        if attempt < max_attempts:
            current = _repaired(current, raw, detail, attempt=attempt + 1)
    raise MalformedOutputError(answer.__name__, max_attempts, detail, raw)


def _with_schema(request: LLMRequest, answer: type[BaseModel]) -> LLMRequest:
    """Attach the reduced schema, or check the one the caller supplied."""
    schema = schema_for(answer)
    if request.schema is None:
        return LLMRequest(
            agent=request.agent,
            task=request.task,
            system=request.system,
            messages=request.messages,
            schema=schema,
            max_tokens=request.max_tokens,
            attempt=request.attempt,
            metadata=request.metadata,
        )
    if request.schema.name != schema.name:
        message = (
            f"{request.agent} asked for {request.schema.name} and wants to parse {schema.name}"
        )
        raise ValueError(message)
    return request


def _parse[T: BaseModel](raw: str, answer: type[T]) -> T | str:
    """Parse and validate, returning the model or the reason it failed.

    A string rather than a raised exception because both failures are ordinary
    control flow here: the caller's next move is the same for either, and the
    text of the failure is what goes into the repair prompt.
    """
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return f"the answer is not valid JSON: {exc}"
    try:
        return answer.model_validate(payload)
    except ValidationError as exc:
        return f"the answer does not satisfy {answer.__name__}: {exc}"


def _repaired(request: LLMRequest, raw: str, detail: str, *, attempt: int) -> LLMRequest:
    """Continue the conversation with the bad answer and what was wrong with it.

    The correction is a turn rather than a rewritten prompt, which is why
    `LLMRequest.attempt` is metadata and the replay key still changes: the
    second attempt is genuinely a different question, and a trace store where
    it hashed the same as the first would make two rows indistinguishable.
    """
    quoted = (raw or _EMPTY_ANSWER)[:MAX_REPAIR_QUOTE_CHARS]
    correction = (
        f"That answer could not be used: {detail}\n\n"
        f"Reply with one JSON object satisfying the schema and nothing else -- "
        f"no prose before it, no code fence around it. If the previous answer was "
        f"cut off, shorten the content rather than the JSON."
    )
    return request.with_messages(
        [
            *request.messages,
            Message(role=MessageRole.ASSISTANT, content=quoted),
            Message(role=MessageRole.USER, content=correction),
        ],
        attempt=attempt,
    )


def _reduce(node: Any) -> Any:
    """Rewrite one schema node into the supported dialect.

    Raises:
        SchemaNotSupportedError: on an external reference or an open-ended
            mapping, neither of which has a lossless reduction.
    """
    if isinstance(node, list):
        return [_reduce(item) for item in node]
    if not isinstance(node, Mapping):
        return node

    reduced = {key: value for key, value in node.items() if key != "title"}
    reduced = _flatten_single_ref(reduced)
    _check_reference(reduced)
    _check_open_mapping(reduced)
    reduced = _drop_unsupported(reduced)

    for key in _CHILD_KEYS:
        if key in reduced:
            reduced[key] = {name: _reduce(child) for name, child in reduced[key].items()}
    for key in (*_BRANCH_KEYS, "items"):
        if key in reduced:
            reduced[key] = _reduce(reduced[key])
    return _close_object(reduced)


def _flatten_single_ref(node: dict[str, Any]) -> dict[str, Any]:
    """Turn `allOf: [{$ref}]` into a plain `$ref`.

    Pydantic wraps a referenced model in `allOf` whenever the field carries
    anything of its own -- a description, a default. The dialect does not
    support `allOf` combined with `$ref`, so the wrapper is unwrapped rather
    than sent.
    """
    branch = node.get("allOf")
    if not (isinstance(branch, Sequence) and len(branch) == 1):
        return node
    only = branch[0]
    if not (isinstance(only, Mapping) and set(only) == {"$ref"}):
        return node
    return {**{k: v for k, v in node.items() if k != "allOf"}, "$ref": only["$ref"]}


def _check_reference(node: Mapping[str, Any]) -> None:
    """Reject a reference the dialect cannot follow."""
    reference = node.get("$ref")
    if isinstance(reference, str) and not reference.startswith("#/"):
        message = f"{reference!r} is an external reference, which structured output cannot follow"
        raise SchemaNotSupportedError(message)


def _check_open_mapping(node: Mapping[str, Any]) -> None:
    """Reject a field typed as an open-ended mapping.

    `dict[str, str]` reduces to `additionalProperties: {...}`, and the dialect
    accepts `additionalProperties` only as `false`. There is no lossless
    reduction, so the model has to be declared with named fields instead --
    which is the better answer anyway: an agent returning a bag of keys returns
    something no reader can validate.
    """
    extra = node.get("additionalProperties")
    if extra is not None and extra is not False:
        message = (
            "an open-ended mapping cannot be expressed: structured output accepts "
            "additionalProperties only as false. Declare the fields instead."
        )
        raise SchemaNotSupportedError(message)


def _drop_unsupported(node: dict[str, Any]) -> dict[str, Any]:
    """Strip unsupported keywords, saying what they were in the description."""
    said: list[str] = []
    for keyword, phrase in _UNSUPPORTED.items():
        if keyword in node:
            value = node.pop(keyword)
            if keyword != "uniqueItems" or value:
                said.append(phrase.format(value=value))
    if node.get("minItems") not in (None, *_SUPPORTED_MIN_ITEMS):
        said.append(f"at least {node.pop('minItems')} entries")
    if node.get("format") not in (None, *_SUPPORTED_FORMATS):
        node.pop("format")
    if said:
        constraint = f"Must be {', '.join(said)}."
        existing = node.get("description", "")
        node["description"] = f"{existing} {constraint}".strip()
    return node


def _close_object(node: dict[str, Any]) -> dict[str, Any]:
    """Close an object and require everything it declares.

    Every property is required because that is what the API does with a
    class-derived schema, and matching it keeps one behaviour rather than two:
    an optional field would otherwise be present in a live answer and absent in
    a replayed one. A field that is genuinely optional says so by being
    nullable, which is a fact about the value rather than about whether the key
    was sent.
    """
    if "properties" not in node and node.get("type") != "object":
        return node
    properties = node.get("properties", {})
    return {**node, "additionalProperties": False, "required": list(properties)}


def _reject_recursion(schema: Mapping[str, Any], name: str) -> None:
    """Refuse a model that refers to itself, directly or through others.

    The dialect has no recursion, so a self-referential response model is a bug
    to be reported when the model is written rather than a 400 on the first
    live call -- which, given the default provider is offline, could be weeks
    later.
    """
    definitions: Mapping[str, Any] = schema.get("$defs") or schema.get("definitions") or {}
    edges = {key: _references_in(value) for key, value in definitions.items()}
    walking: set[str] = set()
    done: set[str] = set()

    def visit(key: str) -> None:
        if key in done:
            return
        if key in walking:
            message = f"{name} is recursive through {key!r}, which structured output cannot express"
            raise SchemaNotSupportedError(message)
        walking.add(key)
        for target in edges.get(key, ()):
            visit(target)
        walking.discard(key)
        done.add(key)

    for key in list(edges) + list(_references_in(schema)):
        visit(key)


def _references_in(node: Any) -> tuple[str, ...]:
    """Every internal definition name a schema node refers to, at any depth."""
    found: list[str] = []
    if isinstance(node, Mapping):
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/"):
            found.append(reference.rsplit("/", 1)[-1])
        for key, value in node.items():
            if key != "$ref":
                found.extend(_references_in(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_references_in(item))
    return tuple(dict.fromkeys(found))
