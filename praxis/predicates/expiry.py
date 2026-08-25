"""Expiry conditions: the three forms this project writes, parsed and fired.

```
expiry := when(<predicate>) | after("<iso-8601>") | on_event("<description>")
```

Three and no more, and the list is not a design so much as an observation: every
expiry condition in `docs/adr/` and in `praxis/corpus/topics.py` is one of these,
and a fourth form invented here would be a form nothing writes. They mean
different things and a caller acts on each differently, which is why they are
three shapes rather than one string with a convention inside it.

**Firing is three-valued, like a predicate.** `when(corpus_documents > 500)`
against a world that has never counted documents has not failed to fire -- it is
undecided, and an undecided expiry does not expire anything. The same
`UNKNOWN`-is-not-`FALSE` rule the evaluator turns on predicates, turned on the
condition that governs them.

**A bare date is read at UTC midnight, and that is a choice.** `after("2026-08-31")`
names a day and not an instant. Reading it in the local zone would make an
assumption expire at a different moment in Colombo and in CI, and invariant 5
exists precisely so that an expiry is one comparison against one clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from praxis.predicates.ast import FALSE, TRUE, Evaluation, Formula, identifiers_in, render
from praxis.predicates.errors import ExpiryError, PredicateSyntaxError
from praxis.predicates.evaluator import evaluate
from praxis.predicates.lexer import TokenKind, tokenize
from praxis.predicates.parser import parse
from praxis.predicates.world import WorldState

_CALL: Final = re.compile(r"\A\s*(?P<name>when|after|on_event)\s*\((?P<argument>.*)\)\s*\Z", re.S)
"""The one shape all three forms share. Anchored at both ends, so trailing text
after the closing bracket is a refusal rather than something quietly ignored."""


@dataclass(frozen=True, slots=True)
class WhenPredicate:
    """`when(p)` -- fires once the predicate `p` becomes true.

    The only form whose firing depends on measurement, and therefore the only
    one that can be undecided for the same reasons a predicate can.
    """

    predicate: Formula


@dataclass(frozen=True, slots=True)
class AfterInstant:
    """`after("2026-08-31")` -- fires once the clock passes an instant."""

    instant: datetime


@dataclass(frozen=True, slots=True)
class OnEvent:
    """`on_event("the work ships")` -- fires once that event has been observed.

    Matching is `WorldState.observed`, which compares after normalising case and
    whitespace and nothing looser. Recognising a differently-worded observation
    as this event is a judgement, and `AssumptionMonitor` makes it with a model
    -- on a path that can only age an assumption and never breach one.
    """

    description: str


type Expiry = WhenPredicate | AfterInstant | OnEvent
"""What an `Assumption.expiry_condition` compiles to."""


def parse_expiry(source: str) -> Expiry:
    """Read an expiry condition.

    Args:
        source: The condition as written.

    Returns:
        The compiled form.

    Raises:
        ExpiryError: if it is not one of the three forms.
        PredicateSyntaxError: if a `when(...)`'s inner predicate will not parse.
            Deliberately not wrapped: "that is not an expiry condition" and
            "that expression is malformed" send a reader to different places.
    """
    found = _CALL.match(source)
    if found is None:
        message = "an expiry condition is when(...), after(...) or on_event(...)"
        raise ExpiryError(source, message)
    argument = found.group("argument")
    name = found.group("name")
    if name == "when":
        return WhenPredicate(predicate=parse(argument))
    text = _quoted(source, argument, name)
    if name == "after":
        return AfterInstant(instant=_instant(source, text))
    return OnEvent(description=text)


def parses_expiry(source: str) -> bool:
    """Whether an expiry condition parses, without caring why it does not.

    Args:
        source: The condition as written.

    Returns:
        True if it is one of the three forms and well formed inside.
    """
    try:
        parse_expiry(source)
    except (ExpiryError, PredicateSyntaxError):
        return False
    return True


def expires(expiry: Expiry, world: WorldState) -> Evaluation:
    """Whether an expiry condition has fired.

    Args:
        expiry: The compiled condition.
        world: The facts, events and clock to read it against.

    Returns:
        `TRUE` if it has fired, `FALSE` if it has not, `UNKNOWN` with a reason
        if the world does not say. An undecided condition expires nothing.
    """
    match expiry:
        case WhenPredicate(predicate=predicate):
            return evaluate(predicate, world)
        case AfterInstant(instant=instant):
            return TRUE if world.now >= instant else FALSE
        case OnEvent(description=description):
            # Not undecided when unobserved: the observations are a closed list
            # of what has been recorded, so "this is not among them" is an
            # answer rather than an absence. A world that has recorded nothing
            # at all is a caller's problem, and `praxis.monitor` says so.
            return TRUE if world.observed(description) else FALSE


def render_expiry(expiry: Expiry) -> str:
    """Write a compiled condition back out as the text it came from.

    Normalising, in the same way and for the same reason `praxis.predicates.ast.render`
    is: `AssumptionFormalizer` stores this rendering, so two agents writing the
    same condition two ways produce one string.

    Args:
        expiry: The compiled condition.

    Returns:
        Text that `parse_expiry` reads back to an equal condition.
    """
    match expiry:
        case WhenPredicate(predicate=predicate):
            return f"when({render(predicate)})"
        case AfterInstant(instant=instant):
            return f'after("{instant.isoformat()}")'
        case OnEvent(description=description):
            return f'on_event("{_escaped(description)}")'


def identifiers_in_expiry(expiry: Expiry) -> frozenset[str]:
    """Every identifier a condition's firing depends on.

    Empty for the two forms that do not measure anything, which is the honest
    answer rather than a special case: `after` reads the clock and `on_event`
    reads the observations, and neither is a quantity anybody could go and
    measure.

    Args:
        expiry: The compiled condition.

    Returns:
        The names, deduplicated.
    """
    if isinstance(expiry, WhenPredicate):
        return identifiers_in(expiry.predicate)
    return frozenset()


def _quoted(source: str, argument: str, name: str) -> str:
    """Read the single quoted string `after` and `on_event` each take.

    Read through the lexer rather than by stripping quotes, so the escape rules
    are the grammar's own and a condition naming a path with a backslash in it
    means the same thing here as inside a predicate.
    """
    tokens = tokenize(argument)
    if len(tokens) != 2 or tokens[0].kind is not TokenKind.STRING:  # noqa: PLR2004 -- string, end
        message = f"{name}(...) takes one quoted string"
        raise ExpiryError(source, message)
    literal = tokens[0].literal
    if not isinstance(literal, str):  # pragma: no cover -- the lexer's invariant
        message = f"{name}(...) takes one quoted string"
        raise ExpiryError(source, message)
    return literal


def _instant(source: str, text: str) -> datetime:
    """An ISO-8601 date or datetime, made timezone-aware if it was not.

    Invariant 5. A naive instant here would make the same expiry fire at a
    different moment on two machines, which is exactly the failure the
    invariant names.
    """
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        message = f'after(...) takes an ISO-8601 date or datetime, not "{text}"'
        raise ExpiryError(source, message) from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _escaped(description: str) -> str:
    """A description as it appears inside quotes."""
    return description.replace("\\", "\\\\").replace('"', '\\"')
