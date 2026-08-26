"""A predicate and a world state to a truth value. Arithmetic, and never a model.

Invariant 3 names this module: *a predicate whose truth depends on sampling is
not a predicate*. Everything here is a pure function of a tree and a
`WorldState`, so two runs over one store agree, and the property tests can
generate a world instead of arranging one.

Three rules are the substance.

**Anything the facts do not settle is `UNKNOWN`, never `FALSE`.** An unbound
identifier, a division by zero, two values of different kinds, an ordering asked
of two booleans -- each comes back undecided and carrying the reason. `FALSE` is
what raises an `AssumptionBreach` against every decision resting on the
assumption, and answering "I was not told" with "your decision is broken" is the
failure mode that would make the whole monitor untrustworthy.

**`and` and `or` are Kleene's, not Python's.** `false and unknown` is `false`,
because no measurement could rescue it; `true or unknown` is `true` for the same
reason. That is strictly more informative than propagating the unknown, and it
is what lets a compound predicate reach a verdict when only part of the world
has been measured.

**Arithmetic runs in a context this module owns.** `Decimal`'s precision is
process-global and settable by anyone; a division evaluated under a caller's
context could round differently on two machines, and every number here is
compared against a threshold. The context is fixed at the default 28 digits and
passed explicitly, so no import order can move a verdict.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Context, Decimal, DecimalException
from typing import Final

from praxis.predicates.ast import (
    FALSE,
    TRUE,
    Arithmetic,
    ArithOp,
    CompareOp,
    Comparison,
    Conjunction,
    Constant,
    Disjunction,
    Evaluation,
    Formula,
    Identifier,
    Negation,
    Term,
    identifiers_in,
    unknown,
)
from praxis.predicates.world import Value, WorldState

_CONTEXT: Final = Context(prec=28)
"""The arithmetic context every operation here runs in.

Owned rather than inherited. `decimal.getcontext()` is per-thread and anyone can
change it, and a rate divided at one precision here and another there would make
`adr_predicates_parsed / adr_predicates_total >= 0.9` answer differently for a
reason that is not the data.
"""

_OPERATIONS: Final[dict[ArithOp, Callable[[Decimal, Decimal], Decimal]]] = {
    ArithOp.ADD: _CONTEXT.add,
    ArithOp.SUBTRACT: _CONTEXT.subtract,
    ArithOp.MULTIPLY: _CONTEXT.multiply,
    ArithOp.DIVIDE: _CONTEXT.divide,
}
"""The four operators, bound to the owned context rather than to `Decimal`'s own
methods -- which would use whatever context the caller's thread happens to have."""

_KINDS: Final[dict[type, str]] = {bool: "a boolean", Decimal: "a number", str: "a string"}


def evaluate(formula: Formula, world: WorldState) -> Evaluation:
    """Decide a predicate against the facts, or say why it cannot be decided.

    Args:
        formula: The parsed predicate.
        world: The facts, events and clock to read it against.

    Returns:
        `TRUE`, `FALSE`, or `UNKNOWN` carrying the reason.
    """
    match formula:
        case Comparison(op=op, left=left, right=right):
            return _compare(op, _value(left, world), _value(right, world))
        case Negation(operand=operand):
            return _negate(evaluate(operand, world))
        case Conjunction(left=first, right=second):
            return _conjoin(evaluate(first, world), evaluate(second, world))
        case Disjunction(left=first, right=second):
            return _disjoin(evaluate(first, world), evaluate(second, world))


def unbound_in(formula: Formula, world: WorldState) -> frozenset[str]:
    """Which of a predicate's identifiers the world does not bind.

    Reported rather than derived from the verdict, because "unknown" and "these
    three quantities were never measured" are different amounts of help. It is
    what `AssumptionMonitor` puts in an assumption's reason and what a person
    reads to know which number to go and find.

    Args:
        formula: The parsed predicate.
        world: The facts to check against.

    Returns:
        The names nothing bound, which is empty when everything is measured.
    """
    return frozenset(name for name in identifiers_in(formula) if world.value_of(name) is None)


def _value(term: Term, world: WorldState) -> Value | _Unsettled:
    """What a term denotes, or why it denotes nothing."""
    match term:
        case Constant(value=value):
            return value
        case Identifier(name=name):
            bound = world.value_of(name)
            if bound is None:
                return _Unsettled(f"nothing has measured {name}")
            return bound
        case Arithmetic(op=op, left=left, right=right):
            return _arithmetic(op, _value(left, world), _value(right, world))


def _arithmetic(
    op: ArithOp, left: Value | _Unsettled, right: Value | _Unsettled
) -> Value | _Unsettled:
    """Combine two terms, refusing anything that is not two numbers."""
    if isinstance(left, _Unsettled):
        return left
    if isinstance(right, _Unsettled):
        return right
    if not isinstance(left, Decimal) or not isinstance(right, Decimal):
        return _Unsettled(f"{op.value} needs two numbers, got {_kind(left)} and {_kind(right)}")
    try:
        return _OPERATIONS[op](left, right)
    except DecimalException:
        # Division by zero is the one that really happens: a ratio predicate
        # over a denominator nothing has counted yet. Undecided rather than
        # false, for the same reason an unbound identifier is.
        return _Unsettled(f"{_render(left)} {op.value} {_render(right)} cannot be computed")


def _compare(op: CompareOp, left: Value | _Unsettled, right: Value | _Unsettled) -> Evaluation:
    """Decide one comparison, or say what stopped it."""
    if isinstance(left, _Unsettled):
        return unknown(left.reason)
    if isinstance(right, _Unsettled):
        return unknown(right.reason)
    if _kind(left) != _kind(right):
        return unknown(f"{_kind(left)} and {_kind(right)} cannot be compared")
    if not op.is_ordering:
        return _decided((left == right) if op is CompareOp.EQ else (left != right))
    if not isinstance(left, Decimal) or not isinstance(right, Decimal):
        # Two booleans, or two strings. Which of `true` and `false` is the
        # larger is a question with no answer, and inventing one would make a
        # predicate mean something its author did not write.
        return unknown(f"{_kind(left)} has no order, so {op.value} means nothing here")
    return _decided(_ordered(op, left, right))


def _ordered(op: CompareOp, left: Decimal, right: Decimal) -> bool:
    """The four ordering comparisons, over two numbers and nothing else."""
    if op is CompareOp.LE:
        return left <= right
    if op is CompareOp.GE:
        return left >= right
    if op is CompareOp.LT:
        return left < right
    return left > right


def _decided(held: bool) -> Evaluation:
    """A settled comparison as an evaluation."""
    return TRUE if held else FALSE


def _negate(inner: Evaluation) -> Evaluation:
    """`not`, which leaves an undecided verdict undecided and keeps its reason."""
    if inner.holds:
        return FALSE
    if inner.violated:
        return TRUE
    return inner


def _conjoin(first: Evaluation, second: Evaluation) -> Evaluation:
    """`and` under Kleene's rules: one false side settles it whatever the other is."""
    if first.violated or second.violated:
        return FALSE
    if first.holds and second.holds:
        return TRUE
    return _first_undecided(first, second)


def _disjoin(first: Evaluation, second: Evaluation) -> Evaluation:
    """`or` under Kleene's rules: one true side settles it whatever the other is."""
    if first.holds or second.holds:
        return TRUE
    if first.violated and second.violated:
        return FALSE
    return _first_undecided(first, second)


def _first_undecided(first: Evaluation, second: Evaluation) -> Evaluation:
    """The reason to report when a compound predicate could not be settled.

    The left side's, when it has one. Reporting both would double every reason
    up a deep tree; reporting the last would name whichever branch happened to
    be written second.
    """
    return first if not first.decided else second


class _Unsettled:
    """A term that denotes nothing, and why.

    Not an `Evaluation`: a term is a quantity and has no truth value, and giving
    it one here is how "the number is missing" turns into "the predicate is
    false" three lines later.
    """

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        """Record why the term could not be valued."""
        self.reason = reason


def _kind(value: Value) -> str:
    """What a value is, as a phrase that fits into a refusal.

    `bool` is looked up before `Decimal` because the table is keyed by exact
    type: a boolean is not a number here, and a predicate comparing one to the
    other is a mistake worth naming rather than coercing.
    """
    return _KINDS.get(type(value), "a value of an unknown kind")


def _render(value: Decimal) -> str:
    """A number as it appears in a refusal."""
    return str(value)
