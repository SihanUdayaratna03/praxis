"""Answering the question out of the question: a response shaped like a real one.

[ADR 0005](../../docs/adr/0005-offline-first-llm-provider.md) rejected a mock
returning fixed strings, on the grounds that the pipeline would run and prove
nothing. This is what replaces it. Given a JSON schema and the prompt that asked
for it, build an answer that satisfies the schema and is made out of the prompt.

Three properties everything downstream leans on:

- **Deterministic.** Every choice is drawn from a generator seeded with the
  request's own replay key, so the same question gets the same answer on any
  machine in any order. An eval run whose numbers moved because a mock rolled
  differently would be measuring the mock.
- **Schema-shaped, agent-blind.** The walk is over the schema, so a new response
  model needs no code here and nothing in this module names an agent. That is
  what keeps ADR 0005's first assumption -- no agent file referencing a provider
  -- true as the agents arrive in Phases 3 to 8.
- **Quoted from the source.** A field the schema asks for as a quotation is
  filled with a sentence that really occurs in the prompt, and an id field with
  an id that really appears there. `VerifierAgent` re-reads the cited span and
  rejects a claim the span does not contain (invariant 6); a mock inventing its
  quotations would let the offline pipeline pass a check the live one has to
  earn, which is the one way an offline default could quietly become a lie.

What this is not is a model. The answers are plausible in *shape* and not in
*content*: the sentence chosen as a decision's rationale is a real sentence, but
nothing decided that it was the right one. No consumer may read a synthesised
answer as evidence about the world. It is evidence about the plumbing.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from math import ceil, floor
from types import MappingProxyType
from typing import Any, Final

from praxis.domain.enums import RecordKind
from praxis.domain.ids import PREFIXES
from praxis.llm.errors import SchemaNotSupportedError
from praxis.llm.hashing import canonical_json
from praxis.llm.types import ResponseSchema

MIN_SENTENCE_CHARS: Final = 24
"""Below this a fragment is a heading or a table cell, not something a model
would quote back. Quoting one would produce citations that verify against half
the corpus, which is a weaker test of `VerifierAgent` than no citation at all."""

MAX_DEPTH: Final = 8
"""How deep a response schema may nest before this refuses to walk it.

The structured-output dialect has no recursion, so a schema deeper than this is
a bug in the caller rather than an ambitious response model. Failing loudly
beats a synthesiser that runs until the stack ends."""

_DEFAULT_ITEMS: Final = 2
"""Array length when the schema does not say. Two rather than one: an agent
that mishandles the plural case passes every test against a single-element
list, and that is exactly the bug an offline pipeline should surface."""

_TRUE_BIAS: Final = 0.7
"""How often a bare boolean comes back true. Not 0.5: the fields agents ask for
as booleans are mostly `is_...` questions asked because the answer is usually
yes, and a coin flip would halve the pipeline's useful output."""

_UNBOUNDED_RANGE: Final = (0.0, 100.0)
_EPSILON: Final = 1e-6

_SYNTHETIC_EPOCH: Final = datetime(2026, 1, 1, tzinfo=UTC)
"""The clock a synthesised date is measured from.

Deliberately not the wall clock. A timestamp read from `now()` would make two
runs over one corpus differ in a column, and determinism is the property the
whole offline story rests on. Timezone-aware because invariant 5 has no
exception for values a mock invented."""

_MAX_DATE_OFFSET_DAYS: Final = 400

_NOTHING_TO_QUOTE: Final = "The source text offered no sentence long enough to quote."
"""What a quotation field gets when the prompt has no quotable sentence.

Returned rather than raised, and deliberately not present in any real document:
a citation of it fails `VerifierAgent` honestly, which is the correct outcome
for an extraction from a prompt that contained nothing to extract."""

_SENTENCE_BREAK: Final = re.compile(r"(?<=[.!?])\s+")
_LIST_MARKER: Final = re.compile(r"^[-*+>#|\s]+")

# Ids are matched in the two shapes ADR 0008 allocates -- a zero-padded ordinal
# and a 16-hex digest -- with the prefixes read from the domain rather than
# spelled again here, so a new record kind cannot make this quietly stale.
_ID_PATTERN: Final = re.compile(
    r"\b(?:{prefixes})-(?:\d{{4,}}|[0-9a-f]{{16}})\b".format(
        prefixes="|".join(sorted(set(PREFIXES.values()), key=len, reverse=True))
    )
)

_ORDINAL_LABEL: Final = re.compile(r"\[(\d{1,5})\]")
"""How this pipeline presents an enumerated listing to a model.

`praxis.ingest.segmenter` renders `[12] paragraph` and asks for entries back by
number. Matching the label rather than any integer in the prompt is the point:
a document full of dates and version numbers would otherwise supply most of the
candidates, and an answer drawn from those refers to nothing.
"""

_ORDINAL_HINTS: Final = ("block", "index", "ordinal", "position")

_QUOTE_HINTS: Final = (
    "quote",
    "text",
    "excerpt",
    "snippet",
    "statement",
    "claim",
    "sentence",
    "evidence",
    "verbatim",
    "passage",
)
_PROSE_HINTS: Final = (
    "reason",
    "rationale",
    "justification",
    "explanation",
    "summary",
    "why",
    "note",
    "description",
    "detail",
)
_ID_HINTS: Final = ("_id", "id_", "identifier")

_RATIONALE_TEMPLATES: Final = (
    "The passage says: {quote} That is what this rests on.",
    "Read directly from the source -- {quote} -- with nothing inferred beyond it.",
    "This follows from one line of the document: {quote}",
    "Supported by the text, which states: {quote}",
)

_HINT_RANGES: Final[Mapping[str, tuple[float, float]]] = MappingProxyType(
    {
        "confidence": (0.55, 0.95),
        "probability": (0.05, 0.95),
        "score": (0.0, 1.0),
        "hours": (1.0, 40.0),
        "days": (1.0, 30.0),
        "weeks": (1.0, 12.0),
        "months": (1.0, 18.0),
        "count": (1.0, 8.0),
        "offset": (0.0, 2000.0),
        "byte": (0.0, 2000.0),
    }
)
"""Plausible ranges for the quantities this domain actually asks for.

A confidence uniform over 0 to 1 is what a schema permits and not what a model
produces; an `AbstentionGate` tuned against the first would be tuned against
noise. Only used where the schema leaves room -- an explicit bound always
wins."""


def sentences_of(text: str) -> tuple[str, ...]:
    """Return the quotable sentences in a prompt, in order and without repeats.

    Markdown list markers and quote carets are stripped so that a bullet is a
    sentence like any other -- the corpus is ADRs and meeting notes, where most
    of the claims worth extracting are bullets.

    Repeats are dropped because a quotation occurring twice cannot be resolved
    to one span, and an ambiguous citation would fail `VerifierAgent` for a
    reason that has nothing to do with the agent under test.

    Args:
        text: Every word the model was given, usually `LLMRequest.source_text`.

    Returns:
        Each distinct sentence of at least `MIN_SENTENCE_CHARS`, in the order
        it appeared.
    """
    found: list[str] = []
    for line in text.splitlines():
        stripped = _LIST_MARKER.sub("", line).strip()
        if not stripped:
            continue
        found.extend(
            candidate
            for part in _SENTENCE_BREAK.split(stripped)
            if len(candidate := part.strip()) >= MIN_SENTENCE_CHARS
        )
    return tuple(dict.fromkeys(found))


def identifiers_in(text: str) -> tuple[str, ...]:
    """Return the Praxis record ids that really occur in a prompt, deduplicated.

    An agent is told the spans it is reading, so the ids it cites back should be
    ids it was given. This is what lets a synthesised citation be checkable
    rather than merely well-formed.
    """
    return tuple(dict.fromkeys(_ID_PATTERN.findall(text)))


def ordinals_in(text: str) -> tuple[int, ...]:
    """Return the bracketed ordinal labels a prompt presents, deduplicated.

    The same argument `identifiers_in` makes, for the other kind of reference
    this pipeline hands a model. An agent shown an enumerated listing --
    `[0] heading`, `[1] paragraph` -- and asked which entries belong together
    answers with numbers from that listing. Drawing them from a range instead
    would make almost every answer refer to an entry that was never offered,
    which is a systematic failure rather than a realistic one, and it would
    leave the code that honours a *good* answer never exercised offline.
    """
    return tuple(dict.fromkeys(int(found) for found in _ORDINAL_LABEL.findall(text)))


def synthesise_answer(schema: ResponseSchema | None, source_text: str, seed: str) -> str:
    """Answer one request as text, structured or free.

    Args:
        schema: The shape demanded, or `None` for a free-text call.
        source_text: Every word the model was given.
        seed: Any stable string identifying the request -- in practice its
            replay key, which is what makes the answer a function of the
            question.

    Returns:
        Canonical JSON satisfying the schema, or a short prose answer. Rendered
        with `canonical_json` so the mock's output is byte-identical between
        runs, which the trace store's `response_text` column then records.

    Raises:
        SchemaNotSupportedError: if the schema nests past `MAX_DEPTH`.
    """
    if schema is None:
        return synthesise_prose(source_text, seed)
    return canonical_json(synthesise_value(schema.json_schema, source_text, seed))


def synthesise_value(json_schema: Mapping[str, Any], source_text: str, seed: str) -> Any:
    """Build a Python value satisfying `json_schema` out of `source_text`.

    Raises:
        SchemaNotSupportedError: if the schema nests past `MAX_DEPTH`.
    """
    return _Answerer(source_text, seed).value(json_schema)


def synthesise_prose(source_text: str, seed: str) -> str:
    """Answer a free-text call with a sentence lifted from what was asked.

    Free-text calls are the minority in this pipeline and none of them feeds the
    store directly, so the answer only has to be the right kind of thing: prose
    that demonstrably read its input.
    """
    return _Answerer(source_text, seed).prose()


class _Answerer:
    """One request's worth of synthesis: a seeded generator over a prompt."""

    def __init__(self, source_text: str, seed: str) -> None:
        """Seed the generator from the request and index what can be quoted."""
        self._rng = random.Random(_seed_int(seed))  # noqa: S311 -- reproducibility, not secrecy
        self._sentences = sentences_of(source_text) or (_NOTHING_TO_QUOTE,)
        self._identifiers = identifiers_in(source_text)
        self._ordinals = ordinals_in(source_text)
        self._defs: Mapping[str, Any] = {}

    def value(self, schema: Mapping[str, Any], *, name: str = "", depth: int = 0) -> Any:
        """Synthesise one value for one node of a schema."""
        if depth > MAX_DEPTH:
            message = f"a response schema nested past {MAX_DEPTH} levels at {name!r}"
            raise SchemaNotSupportedError(message)
        resolved = self._resolve(schema)
        if "const" in resolved:
            return resolved["const"]
        if resolved.get("enum"):
            return self._pick(resolved["enum"])
        branches = resolved.get("anyOf") or resolved.get("oneOf")
        if branches:
            return self.value(_preferred(branches), name=name, depth=depth + 1)
        return self._by_type(resolved, name, depth)

    def prose(self) -> str:
        """A short free-text answer quoting the prompt."""
        return self._pick(_RATIONALE_TEMPLATES).format(quote=self._quote())

    def _by_type(self, schema: Mapping[str, Any], name: str, depth: int) -> Any:
        """Dispatch on the JSON type the schema declares or implies."""
        kind = _type_of(schema)
        if kind == "object":
            return self._object(schema, depth)
        if kind == "array":
            return self._array(schema, name, depth)
        if kind == "boolean":
            return self._rng.random() < _TRUE_BIAS
        if kind in {"integer", "number"}:
            return self._number(schema, name, integral=kind == "integer")
        if kind == "null":
            return None
        return self._string(schema, name)

    def _object(self, schema: Mapping[str, Any], depth: int) -> dict[str, Any]:
        """Fill every declared property, optional ones included.

        A field the schema permits to be absent is still filled, because the
        missing-field path is what the structured layer's repair tests exercise
        deliberately. A mock that withheld data at random would make an agent's
        happy path the thing that is never covered.
        """
        properties: Mapping[str, Any] = schema.get("properties") or {}
        return {
            field: self.value(subschema, name=field, depth=depth + 1)
            for field, subschema in properties.items()
        }

    def _array(self, schema: Mapping[str, Any], name: str, depth: int) -> list[Any]:
        """Build a list of the length the schema allows, without repeats."""
        items = schema.get("items")
        if items is None:
            return []
        low = int(schema.get("minItems", 1))
        high = max(low, int(schema.get("maxItems", max(low, _DEFAULT_ITEMS))))
        built = [
            self.value(items, name=_singular(name), depth=depth + 1)
            for _ in range(self._rng.randint(low, high))
        ]
        return _deduplicated(built) if schema.get("uniqueItems") else built

    def _number(self, schema: Mapping[str, Any], name: str, *, integral: bool) -> float | int:
        """Draw a number inside both the schema's bounds and a plausible range."""
        if integral and self._ordinals and _mentions(name.lower(), _ORDINAL_HINTS):
            return self._ordinal()
        low, high = _bounds(schema, name)
        if not integral:
            # Rounded to two places because that is what a model writes, then
            # clamped because rounding can step back outside a tight exclusive
            # bound -- and a value the schema forbids is not an answer.
            return min(max(round(self._rng.uniform(low, high), 2), low), high)
        lower, upper = ceil(low), floor(high)
        return lower if lower > upper else self._rng.randint(lower, upper)

    def _string(self, schema: Mapping[str, Any], name: str) -> str:
        """Choose what kind of string a field wants, then fit it to the bounds."""
        return _fit(self._string_body(schema, name), schema)

    def _string_body(self, schema: Mapping[str, Any], name: str) -> str:
        """Read the field name as the question it is really asking."""
        declared = schema.get("format")
        if isinstance(declared, str) and (formatted := self._formatted(declared)) is not None:
            return formatted
        lowered = name.lower()
        if _mentions(lowered, _ID_HINTS):
            return self._identifier(lowered)
        if _mentions(lowered, _QUOTE_HINTS):
            return self._quote()
        if _mentions(lowered, _PROSE_HINTS):
            return self.prose()
        return self._label()

    def _quote(self) -> str:
        """A sentence that really occurs in the prompt."""
        return self._pick(self._sentences)

    def _ordinal(self) -> int:
        """A label from an enumerated listing the prompt really presented.

        Deliberately drawn independently per field, so a pair of them is as
        likely to be reversed as ordered. A mock that emitted well-formed
        ranges would be imitating a competent model rather than a model, and
        the code that refuses a reversed range would never run offline.
        """
        return self._pick(self._ordinals)

    def _label(self) -> str:
        """A short title-shaped string, still drawn from the source."""
        words = self._quote().rstrip(".!?").split()
        return " ".join(words[:8])

    def _identifier(self, name: str) -> str:
        """An id from the prompt, preferring one whose kind the field names.

        Falls back to a well-formed span id derived from the seed when the
        prompt carries no ids at all. That fabrication is intentional and
        visible: it is well-formed, it cites nothing, and `VerifierAgent`
        rejects it -- which is what should happen to a citation of a document
        the agent was never shown.
        """
        wanted = _prefix_wanted_by(name)
        preferred = [found for found in self._identifiers if found.startswith(f"{wanted}-")]
        pool = preferred or list(self._identifiers)
        if pool:
            return self._pick(pool)
        return f"{PREFIXES[RecordKind.SPAN]}-{self._rng.getrandbits(64):016x}"

    def _formatted(self, declared: str) -> str | None:
        """Answer a declared string format, or `None` if this build ignores it.

        An unknown format is answered like an ordinary string rather than
        refused: `format` is annotation in JSON Schema, and a synthesiser that
        died on an unrecognised one would make adding a response model to an
        agent a change to this file.
        """
        if declared == "date-time":
            return self._timestamp().isoformat()
        if declared == "date":
            return self._timestamp().date().isoformat()
        return _STATIC_FORMATS.get(declared)

    def _timestamp(self) -> datetime:
        """A tz-aware datetime measured from the synthetic epoch, never `now`."""
        return _SYNTHETIC_EPOCH + timedelta(
            days=self._rng.randint(0, _MAX_DATE_OFFSET_DAYS),
            seconds=self._rng.randint(0, 86_399),
        )

    def _resolve(self, schema: Mapping[str, Any]) -> Mapping[str, Any]:
        """Follow a `$ref` into `$defs`, remembering the definitions on the way.

        Pydantic hoists every nested model into `$defs` and refers to it, so a
        synthesiser that did not follow references would answer a nested schema
        with an empty object and call it valid.
        """
        if "$defs" in schema:
            self._defs = {**self._defs, **schema["$defs"]}
        reference = schema.get("$ref")
        if not isinstance(reference, str):
            return schema
        key = reference.rsplit("/", 1)[-1]
        try:
            return self._resolve(self._defs[key])
        except KeyError as exc:
            message = f"{reference!r} points at a definition the schema does not carry"
            raise SchemaNotSupportedError(message) from exc

    def _pick[T](self, choices: Sequence[T]) -> T:
        """Choose deterministically from a non-empty sequence.

        Generic rather than returning `Any`, so that a caller picking from a
        tuple of sentences is known to be holding a string. `Any` here would
        turn off type checking for most of the module's return paths.
        """
        return choices[self._rng.randrange(len(choices))]


_STATIC_FORMATS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "uri": "https://example.invalid/praxis/synthesised",
        "email": "someone@example.invalid",
    }
)
"""Formats whose answer does not vary, pointed at reserved names.

`.invalid` is reserved by RFC 2606 precisely so that a fabricated host can never
resolve. A synthesised URL that happened to be a real one would be a mock that
can cause a network request, which is the one thing this package's default may
not do."""


def _seed_int(seed: str) -> int:
    """Turn any stable string into a stable integer seed.

    Hashed explicitly rather than handed to `Random(str)`: the Mersenne
    Twister's sequence for an integer seed is fixed, while how a string seed is
    reduced to one is an implementation detail this project should not make its
    reproducibility depend on.
    """
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest(), "big")


def _type_of(schema: Mapping[str, Any]) -> str:
    """Return the JSON type of a node, inferring one when it is left implicit."""
    declared = schema.get("type")
    if isinstance(declared, list):
        return next((member for member in declared if member != "null"), "null")
    if isinstance(declared, str):
        return declared
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return "string"


def _preferred(branches: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Pick the branch of a union worth answering -- anything but null.

    An optional field gets its value rather than `None` for the reason
    `_object` gives: the absent path is covered on purpose elsewhere, and a mock
    that took it at random would decide which of an agent's branches is tested.
    """
    return next((branch for branch in branches if branch.get("type") != "null"), branches[0])


def _prefix_wanted_by(name: str) -> str:
    """Read the record kind out of a field name, defaulting to a span.

    `span_id` and `decision_id` are the two an extraction usually carries, and
    a span is the right default: it is the citation invariant 6 is about.
    """
    for kind, prefix in PREFIXES.items():
        if kind.value in name or (kind is RecordKind.DOCUMENT and "doc" in name):
            return prefix
    return PREFIXES[RecordKind.SPAN]


def _mentions(name: str, hints: Sequence[str]) -> bool:
    """Whether a field name contains any of a set of hints."""
    return any(hint in name for hint in hints)


def _singular(name: str) -> str:
    """Name an array's elements after the array, so hints survive the descent."""
    return name[:-1] if name.endswith("s") and len(name) > 1 else name


def _deduplicated(values: Sequence[Any]) -> list[Any]:
    """Drop repeats from a list whose schema forbids them, keeping order."""
    seen: dict[str, Any] = {}
    for value in values:
        seen.setdefault(canonical_json(value), value)
    return list(seen.values())


def _fit(text: str, schema: Mapping[str, Any]) -> str:
    """Trim or repeat a string until it satisfies the schema's length bounds."""
    body = text or _NOTHING_TO_QUOTE
    minimum = int(schema.get("minLength", 0))
    while len(body) < minimum:
        body = f"{body} {text or _NOTHING_TO_QUOTE}"
    maximum = schema.get("maxLength")
    return body if maximum is None else body[: int(maximum)]


def _bounds(schema: Mapping[str, Any], name: str) -> tuple[float, float]:
    """Intersect a plausible range for the field with what the schema allows.

    The schema always wins. Where intersecting the two would leave nothing --
    a `weeks` field bounded to 100 and up -- the schema's own bounds are used
    and the plausible range is abandoned, because a value outside the schema is
    not an answer at all.
    """
    low, high = _hint_range(name) or _UNBOUNDED_RANGE
    floor_, ceiling = _schema_floor(schema), _schema_ceiling(schema)
    if floor_ is not None and ceiling is not None:
        inner_low, inner_high = max(low, floor_), min(high, ceiling)
        return (inner_low, inner_high) if inner_low <= inner_high else (floor_, ceiling)
    if floor_ is not None:
        return floor_, max(high, floor_)
    if ceiling is not None:
        return min(low, ceiling), ceiling
    return low, high


def _hint_range(name: str) -> tuple[float, float] | None:
    """The plausible range a field name implies, if any."""
    lowered = name.lower()
    return next((span for hint, span in _HINT_RANGES.items() if hint in lowered), None)


def _schema_floor(schema: Mapping[str, Any]) -> float | None:
    """The lowest value a schema permits, exclusive bounds nudged inward."""
    if "minimum" in schema:
        return float(schema["minimum"])
    if "exclusiveMinimum" in schema:
        return float(schema["exclusiveMinimum"]) + _EPSILON
    return None


def _schema_ceiling(schema: Mapping[str, Any]) -> float | None:
    """The highest value a schema permits, exclusive bounds nudged inward."""
    if "maximum" in schema:
        return float(schema["maximum"])
    if "exclusiveMaximum" in schema:
        return float(schema["exclusiveMaximum"]) - _EPSILON
    return None
